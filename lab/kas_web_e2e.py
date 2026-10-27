"""Native Stage-2 Web Config Import smoke in the disposable Ported ARM64 guest.

This is NOT a benchmark or physical KeeneticOS acceptance. The build-time
bundled real config-import.sh must be the committed KAS source.
Only fixed PASS/FAIL labels leave the private CI workspace.
"""
import hashlib
import json
from pathlib import Path
import time

from lab.kas_lifecycle import install, prepare
from lab.kas_lifecycle import put


def check(condition, code):
    if not condition:
        raise RuntimeError(code)


def read_operation(guest):
    raw = guest.command(
        "curl -fSs --noproxy '*' --max-time 10 "
        "http://127.0.0.1:18081/api/config/operation", timeout=20)
    return json.loads(raw)


def await_operation(guest, expected, seconds=180):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        status = read_operation(guest)
        if status.get('state') != 'RUNNING':
            check(status.get('state') == expected, 'unexpected_web_result')
            code = status.get('exit_code')
            if expected == 'OK':
                check(code == 0, 'web_import_nonzero')
            else:
                check(isinstance(code, int) and code != 0, 'web_fail_has_no_exit')
            return status
        time.sleep(2)
    raise RuntimeError('web_import_timeout')


def post(guest, source):
    # Fixed local path only; never interpolate client-controlled input.
    check(source in ('/opt/lab/web-good.yaml', '/opt/lab/web-bad.yaml',
                     '/opt/lab/web-original.yaml'), 'unsafe_test_source')
    cmd = ("curl -sS --noproxy '*' --connect-timeout 2 --max-time 30"
           " -X POST -H 'Origin: http://127.0.0.1:18081'"
           " -H 'Content-Type: application/x-yaml'"
           " -H 'X-KAS-Confirm: apply'"
           " --data-binary @" + source +
           " -o /opt/lab/web-response.json -w '%{http_code}'"
           " http://127.0.0.1:18081/api/config/import")
    response = guest.command(cmd, timeout=45)
    check(response == '202', 'web_post_not_accepted')
    payload = json.loads(guest.command('cat /opt/lab/web-response.json'))
    check(payload.get('state') == 'RUNNING', 'web_post_not_running')
    check(payload.get('id') and len(payload['id']) == 24, 'web_operation_id_absent')
    return payload


def put_large_binary(guest, path, content):
    # 8+ MiB is much larger than ordinary KAS scripts; a default 30-second
    # SSH command timeout is NOT a valid transfer budget for emulated CPU.
    # Destination is fixed by the caller and never influenced by HTTP clients.
    check(path == '/opt/lab/kas-web', 'unsafe_binary_destination')
    # Paramiko's single SSH channel can stall on an 8-MiB stdin window in
    # emulated Ported. Bound each write to 256 KiB, then verify the whole
    # executable before a same-directory atomic rename.
    guest.command('umask 077; : > /opt/lab/.kas-web.new; '
                  'chmod 600 /opt/lab/.kas-web.new', timeout=30)
    for offset in range(0, len(content), 256 * 1024):
        chunk = content[offset:offset + 256 * 1024]
        guest.command('cat >> /opt/lab/.kas-web.new',
                      timeout=45, input_data=chunk)
    remote_sha = guest.command('sha256sum /opt/lab/.kas-web.new', timeout=30).split()[0]
    check(remote_sha == hashlib.sha256(content).hexdigest(), 'web_binary_integrity_error')
    guest.command('mv /opt/lab/.kas-web.new /opt/lab/kas-web; '
                  'chmod 700 /opt/lab/kas-web', timeout=30)


def test_opt_in_service(guest, mirror, evidence):
    """Native guest installation/stop/remove of ONLY the dedicated Web files."""
    key = 'web/control-center/kas-web-manage.sh'
    if key not in mirror.sources:
        evidence.check('KAS_WEB_LIFECYCLE', 'NOT_TESTED', 'unattempted')
        return True
    evidence.check('KAS_WEB_LIFECYCLE', 'NOT_TESTED', 'unattempted')
    try:
        put(guest, '/opt/lab/kas-web-manage.sh', mirror.sources[key], '700')
        before = guest.command('sha256sum /opt/etc/mihomo/config.yaml').split()[0]
        digest = guest.command('sha256sum /opt/lab/kas-web').split()[0]
        code, _, _ = guest.execute('sh /opt/lab/kas-web-manage.sh install '
                                   '/opt/lab/kas-web ' + digest, timeout=30)
        check(code == 0, 'web_service_install_failed')
        check(guest.command('sh /opt/etc/init.d/S98kas-control-center status') ==
              'NOT_RUNNING', 'web_service_implicit_start')
        guest.command('sh /opt/etc/init.d/S98kas-control-center start', timeout=30)
        check(guest.command('sh /opt/etc/init.d/S98kas-control-center status') ==
              'RUNNING', 'web_service_start_failed')
        guest.command("curl -fSs --noproxy '*' --max-time 5 "
                      "http://127.0.0.1:18081/api/status >/dev/null")
        guest.command('sh /opt/etc/init.d/S98kas-control-center stop', timeout=90)
        check(guest.command('sh /opt/etc/init.d/S98kas-control-center status') ==
              'NOT_RUNNING', 'web_service_stop_failed')
        guest.command('sh /opt/etc/init.d/S98kas-control-center remove', timeout=90)
        guest.command('test ! -e /opt/bin/kas-control-center && '
                      'test ! -e /opt/etc/init.d/S98kas-control-center && '
                      'test ! -e /opt/etc/kas-control-center.web.sha256')
        check(guest.command('sha256sum /opt/etc/mihomo/config.yaml').split()[0] ==
              before, 'web_lifecycle_modified_core_config')
        guest.command('test "$(pidof mihomo | wc -w)" -eq 1')
        evidence.check('KAS_WEB_LIFECYCLE', 'PASS', 'native_web_lifecycle_passed')
        print('KAS WEB: isolated install/start/stop/remove and core SHA: PASS',
              flush=True)
        return True
    except Exception:
        evidence.check('KAS_WEB_LIFECYCLE', 'FAIL', 'native_web_lifecycle_failed')
        print('KAS WEB: opt-in component lifecycle FAIL', flush=True)
        return False
    finally:
        # Do not delete managed files after any failure: ownership checks
        # on remove are authoritative; preserve private forensic evidence.
        try:
            guest.execute('rm -f /opt/lab/kas-web-manage.sh', timeout=15)
        except Exception:
            pass


def lifecycle(guest, mirror, revision, evidence, web_binary):
    evidence.check('KAS_WEB_IMPORT', 'NOT_TESTED', 'unattempted')
    # Match the proven normal KAS lifecycle: trusted pinned sources and
    # guest TLS mapping must be prepared BEFORE any real installer invocation.
    try:
        prepare(guest, mirror, revision)
    except Exception:
        evidence.set('KAS_INSTALL', 'BLOCKED', 'verified_tls_source_failed')
        evidence.check('KAS_WEB_IMPORT', 'BLOCKED', 'prerequisite_blocked')
        return False
    if not install(guest, mirror, revision, evidence):
        evidence.check('KAS_WEB_IMPORT', 'BLOCKED', 'prerequisite_blocked')
        return False
    process_started = False
    phase = 'BINARY_TRANSFER'
    try:
        data = Path(web_binary).read_bytes()
        check(data[:4] == b'\x7fELF' and len(data) <= 32 * 1024 * 1024,
              'invalid_web_binary')
        # Actual ARM64 binary, transferred with SHA-256 integrity check.
        put_large_binary(guest, '/opt/lab/kas-web', data)
        phase = 'SERVER_START'
        guest.command(
            'nohup /opt/lab/kas-web -listen 127.0.0.1:18081'
            ' </dev/null >/opt/lab/kas-web.log 2>&1 & echo $! > /opt/lab/kas-web.pid',
            timeout=30)
        process_started = True
        for _ in range(25):
            rc, _, _ = guest.execute(
                "curl -fSs --noproxy '*' --max-time 3 "
                "http://127.0.0.1:18081/api/status >/dev/null", timeout=8)
            if rc == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError('web_loopback_server_unavailable')
        phase = 'HTTP_GUARDS'
        # Negative checks must never run the importer.
        code = guest.command(
            "curl -sS --noproxy '*' --max-time 10"
            " -X POST -H 'Origin: http://untrusted.example'"
            " -H 'Content-Type: application/x-yaml' -H 'X-KAS-Confirm: apply'"
            " --data-binary 'mixed-port: 7890'"
            " -o /dev/null -w '%{http_code}' http://127.0.0.1:18081/api/config/import")
        check(code == '403', 'cross_origin_writes_accepted')
        code = guest.command(
            "curl -sS --noproxy '*' --max-time 10"
            " -H 'Host: attacker.example'"
            " -o /dev/null -w '%{http_code}' http://127.0.0.1:18081/api/status")
        check(code == '403', 'dns_rebinding_accepted')
        phase = 'GOOD_YAML'
        # Preserve the real installed YAML, append harmless syntax-valid marker.
        guest.command(
            'umask 077; cp /opt/etc/mihomo/config.yaml /opt/lab/web-original.yaml; '
            'cp /opt/lab/web-original.yaml /opt/lab/web-good.yaml; '
            "printf '\\n# KAS_WEB_TEST_MARKER\\n' >> /opt/lab/web-good.yaml")
        post(guest, '/opt/lab/web-good.yaml')
        await_operation(guest, 'OK')
        guest.command(
            'cmp -s /opt/etc/mihomo/config.yaml /opt/lab/web-good.yaml && '
            'cmp -s /opt/etc/mihomo/config.yaml.bak /opt/lab/web-original.yaml')
        phase = 'INVALID_YAML'
        # This is an actual Mihomo YAML parser rejection after staging,
        # not merely a frontend/body pre-validation error.
        guest.command(
            'cp /opt/lab/web-good.yaml /opt/lab/web-bad.yaml; '
            "printf '\\nweb_broken: [\\n' >> /opt/lab/web-bad.yaml")
        post(guest, '/opt/lab/web-bad.yaml')
        await_operation(guest, 'FAIL')
        guest.command('cmp -s /opt/etc/mihomo/config.yaml /opt/lab/web-good.yaml')
        phase = 'RESTORE_YAML'
        # Restore original through the *same Web->canonical importer path*.
        post(guest, '/opt/lab/web-original.yaml')
        await_operation(guest, 'OK')
        guest.command('cmp -s /opt/etc/mihomo/config.yaml /opt/lab/web-original.yaml')
        guest.command(
            'test ! -e /tmp/mihomo-lifecycle.lock.d && '
            'test ! -e /tmp/mihomo.maintenance && '
            'test "$(pidof mihomo | wc -w)" -eq 1')
        phase = 'POSTCONDITIONS'
        evidence.check('KAS_WEB_IMPORT', 'PASS', 'native_web_import_passed')
        print('KAS WEB: actual ARM64 HTTP upload -> canonical importer -> '
              'successful config, rejected invalid YAML, original restored: PASS', flush=True)
        if 'web/control-center/kas-web-manage.sh' in mirror.sources:
            # Stop the initially spawned smoke-test process before the
            # standalone service tries to own the same 127.0.0.1 port.
            phase = 'SERVICE_PREPARE'
            guest.command(
                'kill "$(cat /opt/lab/kas-web.pid)" 2>/dev/null || true',
                timeout=20)
            for _ in range(20):
                code, _, _ = guest.execute(
                    "curl -sS --noproxy '*' --connect-timeout 1 --max-time 2"
                    " -o /dev/null http://127.0.0.1:18081/api/status", timeout=5)
                if code != 0:
                    break
                time.sleep(1)
            else:
                raise RuntimeError('web_smoke_process_did_not_stop')
            process_started = False
            phase = 'OPT_IN_LIFECYCLE'
            return test_opt_in_service(guest, mirror, evidence)
        evidence.check('KAS_WEB_LIFECYCLE', 'NOT_TESTED', 'unattempted')
        return True
    except Exception as exc:
        evidence.check('KAS_WEB_IMPORT', 'FAIL', 'native_web_import_failed')
        # Public output is a fixed allowlisted phase name only, never a raw
        # guest exception/URL/config. Detailed command output remains private.
        print('KAS WEB: FAIL at ' + phase, flush=True)
        return False
    finally:
        if process_started:
            try:
                guest.command(
                    'kill "$(cat /opt/lab/kas-web.pid)" 2>/dev/null || true; '
                    'rm -f /opt/lab/web-response.json /opt/lab/web-good.yaml '
                    '/opt/lab/web-bad.yaml /opt/lab/web-original.yaml '
                    '/opt/lab/kas-web /opt/lab/kas-web.pid /opt/lab/kas-web.log',
                    timeout=30)
            except Exception:
                pass

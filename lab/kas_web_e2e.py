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
    guest.command('umask 077; cat > /opt/lab/kas-web; chmod 700 /opt/lab/kas-web',
                  timeout=180, input_data=content)
    remote_sha = guest.command('sha256sum /opt/lab/kas-web', timeout=30).split()[0]
    check(remote_sha == hashlib.sha256(content).hexdigest(), 'web_binary_integrity_error')


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

"""Native Exclude Filter editor E2E in the disposable Ported ARM64 guest.

The filter editor drives a real Mihomo running with two real HTTP
proxy-providers whose payloads are pinned fixture files from the exact KAS
source revision, served by the laboratory mirror over verified TLS. Every
write goes through the canonical config-import.sh of that revision, including
its opt-in KAS_EXPECTED_SOURCE concurrency gate, which is exercised directly
against the real guest filesystem at reproducible transaction points (stale
snapshot before stop, fresh snapshot apply, corrupted candidate rollback).
The apply result is verified through the actual Mihomo provider listing, not
only through file contents.

Only fixed PASS/FAIL labels leave the private CI workspace. Guest command
output, provider payloads and config bytes stay private.
"""
import hashlib
import json
from pathlib import Path
import re
import time

from lab.kas_lifecycle import install, prepare, put
from lab.kas_web_e2e import ClassifiedGuest

PROVIDER_A = 'lab-first'
PROVIDER_B = 'lab-second'
PROVIDER_A_NODES = ('Alpha', 'Beta')
PROVIDER_B_NODES = ('Gamma', 'Delta')
EXCLUDED = 'Beta'

PANEL = 'http://127.0.0.1:18081'
PANEL_SIMPLE = 'http://127.0.0.1:18081'
CONTROLLER = 'http://127.0.0.1:9090'
IMPORTER = '/opt/lab/config-import.sh'


def check(condition, code):
    if not condition:
        raise RuntimeError(code)


def wait_until(action, seconds, code):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        try:
            if action():
                return
        except Exception:
            pass  # transient guest timing noise; the deadline decides
        time.sleep(3)
    raise RuntimeError(code)


def panel_request(guest, method, path, payload=None, origin='http://127.0.0.1:18081',
                  confirm='apply'):
    headers = ['Origin: ' + origin]
    body = ''
    if payload is not None:
        body_path = '/opt/lab/filter-req.json'
        put(guest, body_path, json.dumps(payload).encode(), '600')
        body = ' --data-binary @' + body_path
        headers += ['Content-Type: application/json', 'X-KAS-Confirm: ' + confirm]
    code = guest.command(
        "curl -sS --noproxy '*' --connect-timeout 3 --max-time 30"
        ' -X %s %s %s%s -o /opt/lab/filter-res.json -w %%{http_code}' %
        (method, ' '.join("-H '%s'" % h for h in headers), PANEL + path, body),
        timeout=45)
    raw = guest.command('cat /opt/lab/filter-res.json', timeout=15)
    guest.command('rm -f /opt/lab/filter-res.json /opt/lab/filter-req.json',
                  timeout=15)
    return code, raw


def await_operation(guest, expected, seconds=300):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        raw = guest.command("curl -fSs --noproxy '*' --max-time 10 " +
                            PANEL + '/api/config/operation', timeout=20)
        status = json.loads(raw)
        if status.get('state') != 'RUNNING':
            if status.get('state') != expected or (
                    expected == 'OK' and status.get('exit_code') != 0):
                # Private-only deep diagnostics; the public log gets fixed
                # machine tokens from the operation record itself.
                guest.execute('date; cat ' + PANEL_SIMPLE + '/api/config/operation '
                              "2>&1 | head -3; pidof mihomo | wc -w; "
                              'ls -la /tmp/mihomo-lifecycle.lock.d '
                              '/tmp/mihomo.maintenance 2>&1 | head -4; '
                              'tail -5 /opt/lab/kas-web.log 2>&1', timeout=30)
                print('KAS FILTER DIAG: importer_state=%s exit_code=%s '
                      'error_code=%s' % (status.get('state'),
                                         status.get('exit_code'),
                                         status.get('error_code') or 'NONE'),
                      flush=True)
            check(status.get('state') == expected, 'unexpected_filter_result')
            code = status.get('exit_code')
            if expected == 'OK':
                check(code == 0, 'filter_apply_nonzero')
            else:
                check(isinstance(code, int) and code != 0, 'filter_fail_has_no_exit')
            return status
        time.sleep(2)
    raise RuntimeError('filter_operation_timeout')


def provider_listing(guest):
    raw = guest.command("curl -fSs --noproxy '*' --max-time 10 " +
                        CONTROLLER + '/providers/proxies', timeout=20)
    doc = json.loads(raw)
    return {name: sorted(p.get('name') for p in entry.get('proxies') or [])
            for name, entry in (doc.get('providers') or {}).items()}


def await_provider(guest, provider, expected, seconds=120):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        try:
            if provider_listing(guest).get(provider) == sorted(expected):
                return
        except Exception:
            pass
        time.sleep(3)
    raise RuntimeError('provider_listing_timeout')


def hygiene(payload, what):
    # Editor requests/responses carry names, filter strings and digests —
    # never config bytes, subscription URLs or credentials.
    for marker in ('raw.githubusercontent', 'vless', 'uuid', '192.0.2.',
                   'mode: rule', 'external-controller'):
        check(marker not in payload, 'filter_api_leaked_' + what)


def import_via_web(guest, seed_path):
    # Read-only deep diagnostic first: does real Mihomo accept this seed at
    # all? Output stays in the private SSH log; public labels stay fixed.
    guest.execute('mkdir -p /opt/lab/testhome', timeout=15)
    test_code, test_out, test_err = guest.execute(
        'for b in /opt/sbin/mihomo /opt/bin/mihomo; do test -x "$b" '
        '&& { echo CONFIG_HOME_RUN; "$b" -d /opt/etc/mihomo -f ' + seed_path +
        ' -t; echo EXIT=$?; echo NEUTRAL_HOME_RUN; "$b" -d /opt/lab/testhome '
        '-f ' + seed_path + ' -t; echo EXIT2=$?; break; }; done', timeout=300)
    # The real contract is the config-home run; the neutral-home run only
    # documents the home-directory boundary in the private log.
    accepted = 'EXIT=0' in test_out
    if not accepted:
        # Bring-up diagnostic only: the seed holds public fixture data and
        # project contract keys, so a bounded sanitized reason is safe.
        text = test_out + ' ' + test_err
        # Strip mihomo's timestamp/level chatter so the actual failure line
        # fits the bounded public diagnostic.
        text = re.sub(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]+Z?', ' ', text)
        text = re.sub(r'time=|level=[a-z]+|msg=', ' ', text)
        reason = re.sub(r'[^' + chr(92) + r'w .:/()=-]', ' ', text)
        reason = re.sub(r'[ ]+', ' ', reason).strip()
        print('KAS FILTER DIAG: seed reason=%s' % reason[-300:], flush=True)
    if not accepted:
        # Bring-up diagnostic only: the seed holds public fixture data and
        # project contract keys, so a bounded sanitized reason is safe.
        reason = re.sub(r'[^\w .:/()-]', ' ', (test_out + ' ' + test_err))
        reason = re.sub(r'[ ]+', ' ', reason).strip()
        print('KAS FILTER DIAG: seed reason=%s' % reason[:120], flush=True)
    code = guest.command(
        "curl -sS --noproxy '*' --connect-timeout 3 --max-time 30"
        ' -X POST -H Origin:http://127.0.0.1:18081'
        ' -H Content-Type:application/x-yaml -H X-KAS-Confirm:apply'
        ' --data-binary @' + seed_path +
        ' -o /opt/lab/filter-res.json -w %{http_code} ' + PANEL +
        '/api/config/import', timeout=45)
    if code != '202':
        guest.execute('cat ' + PANEL_SIMPLE + '/api/config/operation 2>&1 | '
                      'head -3; tail -5 /opt/lab/kas-web.log 2>&1', timeout=30)
    check(code == '202', 'seed_import_not_accepted')
    return await_operation(guest, 'OK')


def lifecycle(guest, mirror, revision, evidence, web_binary):
    evidence.check('KAS_FILTER_APPLY', 'NOT_TESTED', 'unattempted')
    # Public per-attempt failure classification for the FAIL line below.
    guest = ClassifiedGuest(guest)
    try:
        prepare(guest, mirror, revision)
    except Exception:
        evidence.set('KAS_INSTALL', 'BLOCKED', 'verified_tls_source_failed')
        evidence.check('KAS_FILTER_APPLY', 'BLOCKED', 'prerequisite_blocked')
        return False
    if not install(guest, mirror, revision, evidence):
        evidence.check('KAS_FILTER_APPLY', 'BLOCKED', 'prerequisite_blocked')
        return False
    phase = 'PAYLOADS'
    try:
        for fixture in ('tests/fixtures/filter-e2e-provider-a.yaml',
                        'tests/fixtures/filter-e2e-provider-b.yaml'):
            check(fixture in mirror.sources and
                  mirror.sources[fixture].startswith(b'#'),
                  'provider_payload_missing')
        # Mihomo requires provider cache paths inside the -d home directory
        # (path is not subpath of home directory or SAFE_PATHS otherwise) and
        # loads HTTP providers synchronously from them at startup, refreshing
        # per interval in the background. Seeding the cache from the pinned
        # mirror payloads keeps the imported config startup within the
        # canonical importer's port-verification window on the emulated CPU.
        put(guest, '/opt/etc/mihomo/kas-lab-provider-a.yaml',
            mirror.sources['tests/fixtures/filter-e2e-provider-a.yaml'],
            '600')
        put(guest, '/opt/etc/mihomo/kas-lab-provider-b.yaml',
            mirror.sources['tests/fixtures/filter-e2e-provider-b.yaml'],
            '600')
        seed = ('mixed-port: 7890\nmode: rule\nlog-level: info\n'
                'external-controller: 127.0.0.1:9090\nproxy-providers:\n'
                '  ' + PROVIDER_A + ':\n    type: http\n'
                '    url: https://raw.githubusercontent.com/saymer-alt/'
                'keenetic-auto-setup/' + revision +
                '/tests/fixtures/filter-e2e-provider-a.yaml\n'
                '    path: /opt/etc/mihomo/kas-lab-provider-a.yaml\n    interval: 3600\n'
                '  ' + PROVIDER_B + ':\n    type: http\n'
                '    url: https://raw.githubusercontent.com/saymer-alt/'
                'keenetic-auto-setup/' + revision +
                '/tests/fixtures/filter-e2e-provider-b.yaml\n'
                '    path: /opt/etc/mihomo/kas-lab-provider-b.yaml\n    interval: 3600\n'
                'proxy-groups:\n  - name: LAB\n    type: select\n    use:\n'
                '      - ' + PROVIDER_A + '\n      - ' + PROVIDER_B + '\n')
        put(guest, '/opt/lab/filter-seed.yaml', seed.encode())
        phase = 'BINARY_TRANSFER'
        data = Path(web_binary).read_bytes()
        check(data[:4] == b'\x7fELF' and len(data) <= 32 * 1024 * 1024,
              'invalid_web_binary')
        # Same bounded 256 KiB channel discipline as the proven Web import E2E.
        guest.command('umask 077; : > /opt/lab/.kas-web.new; '
                      'chmod 600 /opt/lab/.kas-web.new', timeout=30)
        for offset in range(0, len(data), 256 * 1024):
            guest.command('cat >> /opt/lab/.kas-web.new', timeout=45,
                          input_data=data[offset:offset + 256 * 1024])
        remote = guest.command('sha256sum /opt/lab/.kas-web.new',
                               timeout=30).split()[0]
        check(remote == hashlib.sha256(data).hexdigest(),
              'web_binary_integrity_error')
        guest.command('mv /opt/lab/.kas-web.new /opt/lab/kas-web; '
                      'chmod 700 /opt/lab/kas-web', timeout=30)
        phase = 'BINARY_EXEC'
        code, _, _ = guest.execute('/opt/lab/kas-web -help >/dev/null 2>&1',
                                   timeout=20)
        check(code == 0, 'web_binary_not_executable_on_guest')
        phase = 'WEB_LAUNCH'
        guest.command('/opt/lab/kas-web -listen 127.0.0.1:18081 '
                      '-provider-names-export '
                      '</dev/null >/opt/lab/kas-web.log 2>&1 & '
                      'echo $! > /opt/lab/kas-web.pid', timeout=15)
        phase = 'HTTP_LISTEN'
        wait_until(lambda: guest.execute(
            "curl -fSs --noproxy '*' --connect-timeout 1 --max-time 3 " +
            PANEL + '/api/status >/dev/null', timeout=8)[0] == 0,
            45, 'web_http_unreachable')
        phase = 'SEED_IMPORT'
        import_via_web(guest, '/opt/lab/filter-seed.yaml')
        phase = 'SEED_PROVIDERS'
        await_provider(guest, PROVIDER_A, PROVIDER_A_NODES)
        await_provider(guest, PROVIDER_B, PROVIDER_B_NODES)
        phase = 'GUARDS'
        code, _ = panel_request(guest, 'POST', '/api/filter/apply',
                                {'provider': PROVIDER_A, 'names': [EXCLUDED],
                                 'clear': False, 'source_sha': '0' * 64},
                                origin='http://evil.example')
        check(code == '403', 'cross_origin_apply_accepted')
        phase = 'INSPECT'
        code, raw = panel_request(guest, 'GET', '/api/filter/providers')
        check(code == '200', 'inspect_failed')
        hygiene(raw, 'inspect')
        inspect = json.loads(raw)
        sha = inspect.get('source_sha')
        check(isinstance(sha, str) and re.fullmatch(r'[0-9a-f]{64}', sha),
              'inspect_digest_invalid')
        entries = {p.get('name'): p for p in inspect.get('providers') or []}
        check(entries.get(PROVIDER_A, {}).get('http') is True and
              entries.get(PROVIDER_B, {}).get('http') is True,
              'providers_not_visible')
        check(not entries.get(PROVIDER_A, {}).get('has_filter') and
              not entries.get(PROVIDER_B, {}).get('has_filter'),
              'unexpected_existing_filters')
        phase = 'PREVIEW'
        code, raw = panel_request(guest, 'POST', '/api/filter/preview',
                                  {'provider': PROVIDER_A, 'names': [EXCLUDED],
                                   'clear': False, 'source_sha': sha},
                                  confirm='preview')
        check(code == '200', 'preview_rejected')
        hygiene(raw, 'preview')
        preview = json.loads(raw)
        check(preview.get('proposed_filter') and
              preview.get('source_sha') == sha and
              preview.get('applied') is False, 'preview_incomplete')
        phase = 'APPLY'
        code, raw = panel_request(guest, 'POST', '/api/filter/apply',
                                  {'provider': PROVIDER_A, 'names': [EXCLUDED],
                                   'clear': False, 'source_sha': sha})
        check(code == '202', 'apply_not_accepted')
        hygiene(raw, 'apply')
        started = json.loads(raw)
        check(started.get('state') == 'RUNNING' and started.get('id'),
              'apply_not_running')
        await_operation(guest, 'OK')
        phase = 'MIHOMO_FACT'
        await_provider(guest, PROVIDER_A,
                       [n for n in PROVIDER_A_NODES if n != EXCLUDED])
        await_provider(guest, PROVIDER_B, PROVIDER_B_NODES)
        guest.command('grep -q "exclude-filter: \\"^(?:Beta)\\$\\"" '
                      '/opt/etc/mihomo/config.yaml')
        guest.command('test -s /opt/etc/mihomo/config.yaml.bak && '
                      'test "$(pidof mihomo | wc -w)" -eq 1 && '
                      'test ! -e /tmp/mihomo-lifecycle.lock.d && '
                      'test ! -e /tmp/mihomo.maintenance')
        phase = 'EXCLUDED_INVISIBLE'
        raw = guest.command("curl -fSs --noproxy '*' --max-time 10 " +
                            CONTROLLER + '/providers/proxies', timeout=20)
        check(EXCLUDED not in raw, 'excluded_node_still_listed')
        phase = 'CLEAR'
        code, raw = panel_request(guest, 'GET', '/api/filter/providers')
        sha_clear = json.loads(raw).get('source_sha')
        code, raw = panel_request(guest, 'POST', '/api/filter/apply',
                                  {'provider': PROVIDER_A, 'names': [],
                                   'clear': True, 'source_sha': sha_clear})
        check(code == '202', 'clear_not_accepted')
        hygiene(raw, 'clear')
        await_operation(guest, 'OK')
        await_provider(guest, PROVIDER_A, PROVIDER_A_NODES)
        guest.command('! grep -q exclude-filter /opt/etc/mihomo/config.yaml && '
                      'test "$(pidof mihomo | wc -w)" -eq 1')
        phase = 'STALE_GUARD'
        code, raw = panel_request(guest, 'GET', '/api/filter/providers')
        sha_stale = json.loads(raw).get('source_sha')
        guest.command("printf 'operator: tweak\\n' >> /opt/etc/mihomo/config.yaml")
        code, raw = panel_request(guest, 'POST', '/api/filter/apply',
                                  {'provider': PROVIDER_B, 'names': ['Gamma'],
                                   'clear': False, 'source_sha': sha_stale})
        check(code == '409', 'stale_apply_accepted')
        check('CONFIG_CHANGED' in raw, 'stale_apply_wrong_reason')
        guest.command('! grep -q exclude-filter /opt/etc/mihomo/config.yaml && '
                      'test "$(pidof mihomo | wc -w)" -eq 1 && '
                      'test ! -e /tmp/mihomo-lifecycle.lock.d && '
                      'test ! -e /tmp/mihomo.maintenance')
        phase = 'GATE_A_SHELL'
        # The REAL committed importer, REAL guest filesystem, REAL gate:
        # a stale snapshot must abort before the service is touched.
        guest.command('cp /opt/etc/mihomo/config.yaml /opt/lab/snapshot-stale.yaml')
        guest.command('cp /opt/etc/mihomo/config.yaml /opt/lab/candidate-gate.yaml; '
                      "printf '# gate marker\\n' >> /opt/lab/candidate-gate.yaml")
        guest.command("printf 'writer: changed under the panel\\n' >> "
                      '/opt/etc/mihomo/config.yaml')
        code, _, _ = guest.execute('KAS_EXPECTED_SOURCE='
                                   '/opt/lab/snapshot-stale.yaml sh ' +
                                   IMPORTER + ' /opt/lab/candidate-gate.yaml',
                                   timeout=120)
        check(code != 0, 'gate_a_accepted_stale')
        guest.command('! grep -q "gate marker" /opt/etc/mihomo/config.yaml && '
                      'grep -q "writer: changed under the panel" '
                      '/opt/etc/mihomo/config.yaml && '
                      'test "$(pidof mihomo | wc -w)" -eq 1 && '
                      'test ! -e /tmp/mihomo-lifecycle.lock.d && '
                      'test ! -e /tmp/mihomo.maintenance')
        phase = 'GATE_A_MATCH_SHELL'
        guest.command('cp /opt/etc/mihomo/config.yaml /opt/lab/snapshot-fresh.yaml')
        code, _, _ = guest.execute('KAS_EXPECTED_SOURCE='
                                   '/opt/lab/snapshot-fresh.yaml sh ' +
                                   IMPORTER + ' /opt/lab/candidate-gate.yaml',
                                   timeout=180)
        check(code == 0, 'gate_a_rejected_fresh')
        guest.command('grep -q "gate marker" /opt/etc/mihomo/config.yaml && '
                      'test "$(pidof mihomo | wc -w)" -eq 1 && '
                      'test ! -e /tmp/mihomo-lifecycle.lock.d && '
                      'test ! -e /tmp/mihomo.maintenance')
        phase = 'ROLLBACK_SHELL'
        guest.command('cp /opt/etc/mihomo/config.yaml /opt/lab/snapshot-valid.yaml; '
                      "printf 'broken: [\\n' >> /opt/lab/candidate-gate.yaml")
        code, _, _ = guest.execute('KAS_EXPECTED_SOURCE='
                                   '/opt/lab/snapshot-valid.yaml sh ' +
                                   IMPORTER + ' /opt/lab/candidate-gate.yaml',
                                   timeout=180)
        check(code != 0, 'broken_candidate_accepted')
        guest.command('grep -q "gate marker" /opt/etc/mihomo/config.yaml && '
                      '! grep -q "^broken:" /opt/etc/mihomo/config.yaml && '
                      'test "$(pidof mihomo | wc -w)" -eq 1 && '
                      'test ! -e /tmp/mihomo-lifecycle.lock.d && '
                      'test ! -e /tmp/mihomo.maintenance')
        phase = 'POSTCONDITIONS'
        guest.command('kill "$(cat /opt/lab/kas-web.pid)" 2>/dev/null || true',
                      timeout=20)
        guest.command('rm -f /opt/lab/filter-seed.yaml /opt/lab/candidate-gate.yaml '
                      '/opt/lab/snapshot-stale.yaml /opt/lab/snapshot-fresh.yaml '
                      '/opt/lab/snapshot-valid.yaml /opt/lab/kas-web '
                      '/opt/lab/kas-web.pid /opt/lab/kas-web.log '
                      '/opt/etc/mihomo/kas-lab-provider-a.yaml '
                      '/opt/etc/mihomo/kas-lab-provider-b.yaml', timeout=30)
        evidence.check('KAS_FILTER_APPLY', 'PASS', 'native_filter_apply_passed')
        print('KAS FILTER: provider apply verified via Mihomo listing, clear '
              'without loaded names, stale 409, real-importer gate A and '
              'rollback: PASS', flush=True)
        return True
    except Exception as error:
        evidence.check('KAS_FILTER_APPLY', 'FAIL', 'native_filter_apply_failed')
        # RuntimeError labels are fixed identifiers raised by check() or
        # guest.command; any other exception class is printed without content.
        label = str(error) if isinstance(error, RuntimeError)             else type(error).__name__
        print('KAS FILTER: FAIL at %s (%s; last probe: %s)'
              % (phase, label[:60], guest.last_failure), flush=True)
        return False

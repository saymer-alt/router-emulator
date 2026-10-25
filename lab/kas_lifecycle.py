"""Run the real committed KAS scripts inside the authenticated disposable OS."""
import hashlib
import io
import os
import shlex
import tarfile
import re
import time

from lab.kas_mirror import DOMAINS
from lab.run import send_monitor


def put(guest, path, data, mode='600'):
    if not re.fullmatch(r'/opt/[A-Za-z0-9_./-]+', path) or '..' in path.split('/'):
        raise ValueError('unsafe guest laboratory path')
    guest.command('umask 077; cat > ' + path + '; chmod ' + mode + ' ' + path, input_data=data)
    digest = guest.command('sha256sum ' + path).split()[0]
    if digest != hashlib.sha256(data).hexdigest():
        raise RuntimeError('guest source transfer digest mismatch')


def prepare(guest, mirror, revision):
    guest.command('mkdir -p /opt/lab')
    guest.command('/bin/ndmc -c "service dns-proxy"')
    for domain in DOMAINS:
        guest.command('/bin/ndmc -c "ip host ' + domain + ' 192.168.1.100"')
    ca = (mirror.private / 'ca.pem').read_bytes()
    put(guest, '/opt/lab/ca.pem', ca)
    guest.command('cat /opt/lab/ca.pem >> /opt/etc/ssl/certs/ca-certificates.crt')
    # The local service supplies real host UTC through the isolated mapping.
    # NTP and external time synchronization remain outside this offline test.
    clock = rb'''#!/opt/bin/sh
case "$1" in
  start|restart)
    stamp=$(curl -fSs --connect-timeout 2 --max-time 5 http://192.168.1.100/lab/clock) || exit 1
    case "$stamp" in ????-??-??\ ??:??:??) date -s "$stamp" >/dev/null ;; *) exit 1 ;; esac
    ;;
  stop) exit 0 ;;
esac
'''
    put(guest, '/opt/etc/init.d/S01lab-clock', clock, '700')
    guest.command('/opt/etc/init.d/S01lab-clock start')
    # Check the exact path that the native installer will use, with its normal
    # CA verification enabled. No -k / no-check-certificate or wrapper clients.
    url = 'https://raw.githubusercontent.com/saymer-alt/keenetic-auto-setup/' + revision + '/install.sh'
    digest = guest.command('curl -fSs --max-time 20 ' + url + ' | sha256sum').split()[0]
    expected = hashlib.sha256(mirror.sources['install.sh']).hexdigest()
    if digest != expected:
        raise RuntimeError('native verified TLS source delivery failed')
    for path, data in mirror.sources.items():
        if '/' not in path and path.endswith('.sh'):
            put(guest, '/opt/lab/' + path, data)
    guest.command('/bin/ndmc -c "system configuration save"; sync')


def plain(text):
    return re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', text)


class ContractFailure(RuntimeError):
    def __init__(self, reason):
        from lab.gates import REASONS
        if reason not in REASONS:
            raise ValueError('unapproved contract failure')
        self.reason = reason
        super().__init__(reason)


def changed_reason(before, after):
    files = {'/opt/etc/mihomo/config.yaml': 'mihomo_config_changed',
        '/opt/var/lib/magitrickle/config.yaml': 'magi_config_changed',
        '/opt/bin/mihomo_watchdog.sh': 'watchdog_changed',
        '/opt/etc/cron.5mins/mihomo_watchdog': 'watchdog_changed',
        '/opt/etc/ndm/netfilter.d/020-bypass_wa.sh': 'hook_changed',
        '/opt/etc/crontab': 'crontab_changed',
        '/opt/etc/keenetic-auto-setup/ownership.v1': 'ledger_changed'}
    for path, reason in files.items():
        if before['files'].get(path) != after['files'].get(path):
            return reason
    for key, reason in (('proxy', 'proxy_changed'), ('policy', 'policy_changed'),
                        ('ledger', 'ledger_changed'), ('netfilter', 'netfilter_changed'),
                        ('prerouting', 'netfilter_changed')):
        if before[key] != after[key]:
            return reason
    return 'idempotence_failed'


def install(guest, mirror, revision, evidence, foreign=None):
    evidence.set('KAS_INSTALL', 'NOT_TESTED', 'unattempted')
    code, output, errors = guest.execute('KEENETIC_AUTO_SETUP_REF=' + revision +
        ' sh /opt/lab/install.sh disk', timeout=900)
    if code != 0 or '[OK] Done' not in plain(output):
        evidence.set('KAS_INSTALL', 'FAIL', 'native_installer_failed')
        return False
    installed = guest.command('opkg list-installed')
    if 'mihomo - 1.19.33-2' not in installed or 'magitrickle - 0.8.2-1' not in installed:
        evidence.set('KAS_INSTALL', 'FAIL', 'native_installer_failed')
        return False
    try:
        contract(guest, mirror, foreign)
    except ContractFailure as error:
        evidence.set('KAS_INSTALL', 'FAIL', error.reason)
        return False
    except Exception:
        evidence.set('KAS_INSTALL', 'FAIL', 'native_installer_failed')
        return False
    evidence.set('KAS_INSTALL', 'PASS', 'native_installer_passed')
    print('KAS INSTALL: actual pinned installer completed and packages verified', flush=True)
    return True


def doctor(guest, evidence, revision):
    code, output, errors = guest.execute('KEENETIC_AUTO_SETUP_REF=' + revision +
        ' sh /opt/lab/mihomo-doctor.sh', timeout=240)
    output = plain(output)
    for label, key in (('OK', 'doctor_ok'), ('WARN', 'doctor_warn'), ('FAIL', 'doctor_fail')):
        count = re.search(r'(?m)^' + label + r':\s+(\d+)', output)
        if count:
            evidence.measurement(key, int(count[1]))
    failures = re.search(r'(?m)^FAIL:\s+(\d+)', output)
    warnings = re.search(r'(?m)^WARN:\s+(\d+)', output)
    if code == 0 and warnings and int(warnings[1]) == 0 and failures and int(failures[1]) == 0 and 'Overall status: OK' in output:
        evidence.set('KAS_DOCTOR', 'PASS', 'native_doctor_passed')
    elif code == 1 and warnings and int(warnings[1]) > 0 and failures and int(failures[1]) == 0 and 'Overall status: WARN' in output:
        evidence.set('KAS_DOCTOR', 'BLOCKED', 'doctor_warning')
    else:
        evidence.set('KAS_DOCTOR', 'FAIL', 'native_doctor_failed')
    # Publish only source-defined finding prefixes, never arbitrary guest text.
    # The full diagnostic remains in ssh-private.log.
    warning_prefixes = (
        'Watchdog log not found', 'Watchdog log is empty',
        'No healthy check recorded', "No '[OK] All good' entry found",
        'Historical stability: WARN', 'Repeated failures:',
        'Watchdog interventions in the last 24h:',
        'magitrickled is running but DNS proxy port',
        'bypass_wa policy exists but has no interface permit',
        'bypass_wa policy not found', 'DNS resolution FAILED',
    )
    for line in output.splitlines():
        if '[WARN] ' in line:
            finding = line.split('[WARN] ', 1)[1]
            if finding.startswith('Overall status:'):
                continue
            label = next((p for p in warning_prefixes if finding.startswith(p)), 'unclassified_warning')
            print('KAS DOCTOR WARNING: ' + label, flush=True)
    print('KAS DOCTOR: ' + evidence.gates['KAS_DOCTOR']['status'], flush=True)
    return evidence.gates['KAS_DOCTOR']['status'] == 'PASS'


STABLE_FILES = ('/opt/etc/mihomo/config.yaml', '/opt/var/lib/magitrickle/config.yaml',
    '/opt/bin/mihomo_watchdog.sh', '/opt/etc/cron.5mins/mihomo_watchdog',
    '/opt/etc/ndm/netfilter.d/020-bypass_wa.sh', '/opt/etc/crontab',
    '/opt/etc/keenetic-auto-setup/ownership.v1')


def block(config, header):
    lines = config.splitlines()
    for index, line in enumerate(lines):
        if line.strip() == header and not line.startswith(' '):
            result = [line]
            for following in lines[index + 1:]:
                if following.startswith('!') or (following and not following.startswith((' ', '\t'))):
                    break
                result.append(following)
            return '\n'.join(result)
    raise RuntimeError('native configuration block missing')


def wait_services(guest, timeout=360):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        code, output, errors = guest.execute('pidof mihomo; pidof magitrickled')
        rows = output.splitlines()
        if any(len(line.split()) > 1 for line in rows):
            raise ContractFailure('native_services_duplicate')
        if code == 0 and len(rows) == 2 and all(len(line.split()) == 1 and line.strip().isdigit() for line in rows):
            return
        time.sleep(3)
    raise ContractFailure('native_services_not_ready')


def netfilter_settled(rules, links):
    entries = [line for line in rules.splitlines() if line.startswith('-A ')]
    return (len(entries) == 3 and all(value in entries[index] for index, value in
        enumerate(('-j MARK', '-j CONNMARK --save-mark', '-j RETURN')))
        and len(links) == 1 and bool(re.search(r'--mark 0x0(?:/0xffffffff)? -j ', links[0])))


def wait_netfilter_settled(guest, budget=60):
    # Native Netfilter rewrite callbacks apply asynchronously after the
    # installer's Proxy down/up and hook reinstallation, including across a
    # reboot. Sample the identical assertion until the chain settles, with the
    # same bounded snapshot budget the native installer self-check uses; the
    # expected content never changes and a chain that never settles fails.
    # A failed probe is "not ready", never a verdict: while the rewrite holds
    # the xtables lock an `iptables -S` read can exit nonzero, and a transient
    # channel error must not abort the sample. The installer's own bounded
    # self-check retries failed probes the same way; the assertion itself is
    # unchanged and a chain that never settles still fails. The 60s bound
    # (3x the installer's own budget) covers the foreign-profile churn under
    # QEMU TCG observed exceeding 20s while every probe kept failing; real
    # hardware settles in seconds, and exceeding the bound still fails.
    until = time.monotonic() + budget
    probe_failures = 0
    content_samples = 0
    last_shape = None
    while True:
        try:
            rules = guest.command('iptables -t mangle -S _CUST_BYPASS_WA_')
            links = [line for line in guest.command('iptables -t mangle -S PREROUTING').splitlines()
                     if line.endswith(' -j _CUST_BYPASS_WA_')]
        except Exception:
            rules = None
            probe_failures += 1
        if rules is not None:
            content_samples += 1
            entries = [line for line in rules.splitlines() if line.startswith('-A ')]
            last_shape = (len(entries), len(links), len(entries) == 3 and all(
                value in entries[index] for index, value in
                enumerate(('-j MARK', '-j CONNMARK --save-mark', '-j RETURN'))))
            if netfilter_settled(rules, links):
                return rules, links
        if time.monotonic() >= until:
            # Sanitized one-line diagnosis: counters and the chain's public
            # shape only; no guest text, no rule bodies, no UART data.
            print('KAS SETTLE DIAGNOSTIC: probe_failures=%d content_samples=%d '
                  'last_shape(entries,links,order_ok)=%s'
                  % (probe_failures, content_samples, last_shape), flush=True)
            raise ContractFailure('native_rules_not_ready')
        time.sleep(2)


def contract(guest, mirror, foreign=None):
    wait_services(guest)
    def read(command, reason='native_command_failed'):
        try:
            return guest.command(command)
        except Exception as error:
            raise ContractFailure('native_command_timed_out' if isinstance(error, TimeoutError) else reason) from None
    ledger = read('cat /opt/etc/keenetic-auto-setup/ownership.v1', 'native_ownership_invalid')
    if guest.command('ls -ld /opt/etc/keenetic-auto-setup/ownership.v1').split()[0] != '-rw-------':
        raise ContractFailure('native_ownership_invalid')
    if not ledger.startswith('KAS_OWNERSHIP_V1\n'):
        raise ContractFailure('native_ownership_invalid')
    rows = ledger.splitlines()[1:]
    if len(rows) != len(set(rows)):
        raise ContractFailure('native_ownership_invalid')
    proxies = [row[12:] for row in rows if row.startswith('owned|proxy|')]
    if len(proxies) != 1 or not re.fullmatch(r'Proxy\d+', proxies[0]):
        raise ContractFailure('native_proxy_invalid')
    for package in ('mihomo', 'magitrickle'):
        if 'owned|package|' + package not in rows:
            raise ContractFailure('native_ownership_invalid')
    config = plain(guest.command('/bin/ndmc -c "show running-config"'))
    try:
        proxy = block(config, 'interface ' + proxies[0])
    except RuntimeError:
        raise ContractFailure('native_proxy_invalid') from None
    if not all(value in proxy for value in ('proxy protocol socks5', 'proxy socks5-udp', 'proxy upstream 127.0.0.1 7890')):
        raise ContractFailure('native_proxy_invalid')
    try:
        policy = block(config, 'ip policy bypass_wa')
    except RuntimeError:
        raise ContractFailure('native_policy_invalid') from None
    if 'permit global ' + proxies[0] not in policy:
        raise ContractFailure('native_policy_invalid')
    if not re.search(r'(?m)^\s+intercept enable\s*$', config):
        raise ContractFailure('native_dns_invalid')
    rules, links = wait_netfilter_settled(guest)
    hashes = {}
    for line in guest.command('sha256sum ' + ' '.join(STABLE_FILES)).splitlines():
        digest, path = line.split(None, 1)
        if not re.fullmatch('[a-f0-9]{64}', digest) or path not in STABLE_FILES:
            raise ContractFailure('native_file_digest_invalid')
        hashes[path] = digest
    expected = {'/opt/bin/mihomo_watchdog.sh': 'mihomo-watchdog.sh',
                '/opt/etc/ndm/netfilter.d/020-bypass_wa.sh': '020-bypass-wa.sh'}
    for path, source in expected.items():
        if hashes.get(path) != hashlib.sha256(mirror.sources[source]).hexdigest():
            raise ContractFailure('native_file_digest_invalid')
    if foreign is not None:
        try:
            preserved = block(config, 'interface Proxy0') == foreign['proxy']
        except RuntimeError:
            preserved = False
        if not preserved or 'owned|proxy|Proxy0' in rows:
            raise ContractFailure('foreign_proxy_changed')
        if foreign['cron'] not in guest.command('cat /opt/etc/crontab'):
            raise ContractFailure('foreign_cron_changed')
    # The real init script returns after spawning; PID existence precedes
    # listener readiness. Retry the actual transport, without service writes.
    until = time.monotonic() + 90
    while True:
        code, response, errors = guest.execute('curl -fSs --max-time 10 --proxy socks5h://127.0.0.1:7890 http://192.168.1.100/lab/health', timeout=15)
        if code == 0 and response == 'KEENETIC_LAB_HTTP_OK':
            break
        if time.monotonic() >= until:
            raise ContractFailure('native_socks_not_ready')
        time.sleep(2)
    return {'files': hashes, 'proxy': proxy, 'policy': policy, 'ledger': ledger,
            'netfilter': rules, 'prerouting': links[0]}


def foreign_resources(guest):
    config = plain(guest.command('/bin/ndmc -c "show running-config"'))
    if re.search(r'(?m)^interface Proxy0\s*$', config):
        raise RuntimeError('fresh foreign-resource profile requires a free Proxy0')
    for command in ('interface Proxy0', 'interface Proxy0 description lab-foreign',
                    'interface Proxy0 proxy protocol socks5',
                    'interface Proxy0 proxy upstream 127.0.0.1 1081', 'interface Proxy0 down'):
        guest.command('/bin/ndmc -c "' + command + '"')
    config = plain(guest.command('/bin/ndmc -c "show running-config"'))
    cron = '*/17 * * * * root /opt/lab/foreign-job'
    put(guest, '/opt/lab/foreign-job', b'#!/opt/bin/sh\nexit 0\n', '700')
    guest.command("printf '%s\\n' '" + cron + "' >> /opt/etc/crontab")
    guest.command('/bin/ndmc -c "system configuration save"; sync')
    return {'proxy': block(config, 'interface Proxy0'), 'cron': cron}


def idempotence(guest, mirror, revision, evidence, foreign):
    evidence.check('KAS_IDEMPOTENCE', 'NOT_TESTED', 'unattempted')
    before = contract(guest, mirror, foreign)
    code, output, errors = guest.execute('KEENETIC_AUTO_SETUP_REF=' + revision +
        ' sh /opt/lab/install.sh disk', timeout=600)
    after = contract(guest, mirror, foreign)
    if code != 0 or '[OK] Done' not in plain(output) or before != after:
        evidence.check('KAS_IDEMPOTENCE', 'FAIL', changed_reason(before, after) if before != after else 'native_installer_failed')
        return False
    evidence.check('KAS_IDEMPOTENCE', 'PASS', 'native_command_verified')
    if foreign:
        evidence.check('KAS_FOREIGN_RESOURCES', 'PASS', 'native_command_verified')
    print('KAS IDEMPOTENCE: files, ownership, policy, Proxy and foreign resources preserved', flush=True)
    return True


def reboot(guest, mirror, evidence, context, foreign):
    before = contract(guest, mirror, foreign)
    guest.command('/bin/ndmc -c "system configuration save"; sync')
    guest.close()
    send_monitor(context['monitor'], 'system_reset')
    time.sleep(5)
    guest.connect(300)
    after = contract(guest, mirror, foreign)
    if before != after:
        evidence.set('KAS_REBOOT', 'FAIL', 'native_reboot_failed')
        return False
    evidence.set('KAS_REBOOT', 'PASS', 'services_restored_after_reboot')
    print('KAS REBOOT: real services and configuration restored', flush=True)
    return True


def interrupted_repeat(guest, mirror, revision, evidence):
    evidence.check('KAS_INSTALL_INTERRUPTION', 'NOT_TESTED', 'unattempted')
    before = contract(guest, mirror)
    pending = b''
    signalled = False

    def services_verified(chunk):
        nonlocal pending, signalled
        pending = (pending + chunk)[-8192:]
        marker = b'Mihomo binary/config unchanged and daemon is running - restart skipped'
        if not signalled and marker in pending:
            signalled = True
            guest.command('p=$(cat /opt/lab/install.pid) || exit 1; '
                'case "$p" in ""|0|*[!0-9]*) exit 1;; esac; '
                'read owner start < /tmp/mihomo-lifecycle.lock.d/owner || exit 1; '
                '[ "$p" = "$owner" ] && kill -TERM "$p"')

    command = 'export KEENETIC_AUTO_SETUP_REF=' + revision + '; echo $$ > /opt/lab/install.pid; exec sh /opt/lab/install.sh disk'
    code, output, errors = guest.execute(command, timeout=600, on_stdout=services_verified)
    if not signalled or code == 0:
        evidence.check('KAS_INSTALL_INTERRUPTION', 'BLOCKED', 'rollback_not_exercised')
        return False
    guest.command('[ ! -e /tmp/mihomo-lifecycle.lock.d ] || exit 1; '
        'for p in /opt/bin/.mihomo_watchdog.sh.new.*; do [ ! -e "$p" ] || exit 1; done')
    if before != contract(guest, mirror):
        evidence.check('KAS_INSTALL_INTERRUPTION', 'FAIL', 'execution_failed')
        return False
    code, output, errors = guest.execute('KEENETIC_AUTO_SETUP_REF=' + revision +
        ' sh /opt/lab/install.sh disk', timeout=600)
    passed = code == 0 and '[OK] Done' in plain(output) and before == contract(guest, mirror)
    evidence.check('KAS_INSTALL_INTERRUPTION', 'PASS' if passed else 'FAIL',
                   'native_command_verified' if passed else 'execution_failed')
    print('KAS INSTALL INTERRUPTION: handled TERM and repeat-install recovery checked', flush=True)
    return passed


def wan_guard(guest, evidence):
    evidence.check('KAS_WAN_RESTART_GUARD', 'NOT_TESTED', 'unattempted')
    def identity():
        pid = guest.command('pidof mihomo')
        if not pid.isdigit():
            raise RuntimeError('one native Mihomo required')
        stat = guest.command('cat /proc/' + pid + '/stat')
        return pid, stat.rsplit(') ', 1)[1].split()[19]
    before = identity()
    observed = 0
    count_command = "grep -c 'WAN unreachable' /opt/var/log/mihomo_watchdog.log 2>/dev/null || true"
    # A normal cron run may already hold the per-run lock. The native script
    # then exits successfully without a probe; do not count that as evidence.
    for _ in range(4):
        count = int(guest.command(count_command) or '0')
        code, output, errors = guest.execute('sh /opt/bin/mihomo_watchdog.sh', timeout=120)
        # The normal watchdog writes its own log; the test never fabricates it.
        after = int(guest.command(count_command) or '0')
        observed += int(code == 0 and after > count)
        if identity() != before:
            evidence.check('KAS_WAN_RESTART_GUARD', 'FAIL', 'execution_failed')
            return False
        if observed == 2:
            break
        if after == count:
            time.sleep(3)
    evidence.check('KAS_WAN_RESTART_GUARD', 'PASS' if observed == 2 else 'FAIL',
                   'native_command_verified' if observed == 2 else 'execution_failed')
    print('KAS WATCHDOG: native offline-WAN path checked without restart', flush=True)
    return observed == 2


def update_and_rollback(guest, mirror, revision, evidence, foreign):
    evidence.check('KAS_UPDATE', 'NOT_TESTED', 'unattempted')
    before = contract(guest, mirror, foreign)
    command = 'export KEENETIC_AUTO_SETUP_REF=' + revision + '; echo $$ > /opt/lab/update.pid; exec sh /opt/lab/update-mihomo.sh --force'
    code, output, errors = guest.execute(command, timeout=600)
    if code != 0 or '[OK] Mihomo updated successfully' not in plain(output):
        evidence.check('KAS_UPDATE', 'FAIL', 'native_update_failed')
        evidence.set('KAS_ROLLBACK', 'BLOCKED', 'prerequisite_blocked')
        return False
    if before != contract(guest, mirror, foreign):
        evidence.check('KAS_UPDATE', 'FAIL', 'native_update_failed')
        return False
    evidence.check('KAS_UPDATE', 'PASS', 'native_command_verified')
    # --force is the updater's documented same-version replacement path. It
    # never grants downgrade, swap or uninstall authority. Inject TERM only
    # after the real atomic commit, exercising the production Phase-B trap.
    binary = guest.command('for p in /opt/sbin/mihomo /opt/bin/mihomo; do if [ -x "$p" ]; then echo "$p"; break; fi; done')
    state_paths = binary + ' /opt/etc/keenetic-auto-setup-mihomo.state'
    prior = guest.command('sha256sum ' + state_paths)
    pending = b''
    signalled = False

    def committed(chunk):
        nonlocal pending, signalled
        pending = (pending + chunk)[-8192:]
        if not signalled and b'Commit point passed: the new binary is the canonical Mihomo.' in pending:
            signalled = True
            guest.command('p=$(cat /opt/lab/update.pid) || exit 1; '
                'case "$p" in ""|0|*[!0-9]*) exit 1;; esac; '
                'read owner start < /tmp/mihomo-lifecycle.lock.d/owner || exit 1; '
                '[ "$p" = "$owner" ] && kill -TERM "$p"')

    code, output, errors = guest.execute(command, timeout=600, on_stdout=committed)
    restored = contract(guest, mirror, foreign)
    after = guest.command('sha256sum ' + state_paths)
    if not signalled:
        evidence.set('KAS_ROLLBACK', 'BLOCKED', 'rollback_not_exercised')
        return False
    if code == 0 or 'Rollback successful.' not in plain(output) or before != restored or prior != after:
        evidence.set('KAS_ROLLBACK', 'FAIL', 'native_rollback_failed')
        return False
    evidence.set('KAS_ROLLBACK', 'PASS', 'native_rollback_passed')
    print('KAS ROLLBACK: native post-commit TERM trap restored binary, metadata and service', flush=True)
    return True


def lab_origin(guest, mirror):
    """Provenance only for a fresh controller-owned VM, never production."""
    code, output, errors = guest.execute('''
for n in mihomo magitrickled; do
    if pidof "$n" >/dev/null 2>&1; then exit 3; else [ "$?" = 1 ] || exit 3; fi
done
for p in /opt/bin/mihomo /opt/sbin/mihomo /opt/bin/magitrickled; do
    [ ! -e "$p" ] && [ ! -L "$p" ] || exit 3
done
list=$(opkg list-installed) || exit 3
printf '%s\n' "$list" | grep -E '^(mihomo|magitrickle) ' && exit 3
exit 0
''')
    if code != 0:
        raise ContractFailure('lab_process_provenance_unproven')
    payloads = {name: set() for name in ('mihomo', 'magitrickled')}
    # KASMirror already checked these package bytes against the locked SHA256.
    for route, data in mirror.routes.items():
        if not route[2].endswith('.ipk') or not any('/' + p in route[2] for p in ('mihomo_', 'magitrickle_')):
            continue
        with tarfile.open(fileobj=io.BytesIO(data)) as package:
            member = next(m for m in package if m.name.endswith('data.tar.gz'))
            with tarfile.open(fileobj=io.BytesIO(package.extractfile(member).read())) as files:
                blobs = {m.name.removeprefix('./'): files.extractfile(m).read() for m in files if m.isfile()}
        for name in payloads:
            init = 'S99mihomo' if name == 'mihomo' else 'S99magitrickle'
            if 'opt/bin/' + name in blobs and 'opt/etc/init.d/' + init in blobs:
                binary = hashlib.sha256(blobs['opt/bin/' + name]).hexdigest()
                init_data = blobs['opt/etc/init.d/' + init]
                payloads[name].add((binary, hashlib.sha256(init_data).hexdigest()))
                if 'uninstall-remove.sh' in getattr(mirror, 'sources', {}):
                    payloads[name].add((binary, hashlib.sha256(init_data.replace(b'ENABLED=yes\n', b'ENABLED=no\n')).hexdigest()))
    if not all(payloads.values()):
        raise ContractFailure('lab_process_provenance_unproven')
    return payloads


def lab_identities(guest, origin):
    if not origin:
        raise ContractFailure('lab_process_provenance_unproven')
    result = {}
    for name, allowed in origin.items():
        init = 'S99mihomo' if name == 'mihomo' else 'S99magitrickle'
        code, output, errors = guest.execute(r'''p=$(pidof NAME) || exit 3
case "$p" in ''|*[!0-9]*) exit 3;; esac
printf '%s\n' "$p"
cat /proc/$p/stat
readlink /proc/$p/exe
tr '\000' '|' < /proc/$p/cmdline; echo
grep '^Uid:' /proc/$p/status
[ /proc/$p/exe -ef /opt/bin/NAME ] || exit 3
sha256sum /proc/$p/exe /opt/bin/NAME /opt/etc/init.d/INIT
'''.replace('NAME', name).replace('INIT', init))
        try:
            pid, stat, exe, args, uid, process_hash, binary_hash, init_hash = output.splitlines()
            start = stat.rsplit(') ', 1)[1].split()[19]
            argv = '-d|/opt/etc/mihomo|' if name == 'mihomo' else ''
            if not (code == 0 and pid.isdigit() and int(pid) > 1 and start.isdigit()
                    and exe == '/opt/bin/' + name and uid.split()[1:] == ['0'] * 4
                    and args in (name + '|' + argv, '/opt/bin/' + name + '|' + argv)):
                raise ValueError('unproven process')
            hashes = process_hash.split()[0], binary_hash.split()[0], init_hash.split()[0]
            if hashes[0] != hashes[1] or (hashes[0], hashes[2]) not in allowed:
                raise ValueError('unproven payload')
        except (ValueError, IndexError):
            raise ContractFailure('lab_process_provenance_unproven') from None
        result[name] = (pid, start, hashes[0], hashes[2], args)
    return result


def lab_startup_settle_script():
    # The locked KN S99magitrickle has its own 5s startup loop until
    # uptime > 300. It does not use the KAS watchdog lock. Wait for its
    # natural exit before recording identities; never signal the init shell.
    return r'''
tries=0
observed=0
while :; do
    uptime=$(cut -d. -f1 /proc/uptime) || exit 3
    case "$uptime" in ''|*[!0-9]*) exit 3;; esac
    active=0
    for file in /proc/[0-9]*/cmdline; do
        [ -e "$file" ] || continue
        args=$(tr '\000' '|' < "$file") || { [ ! -e "$file" ] && continue; exit 3; }
        case "$args" in *'S99magitrickle|'*) active=1;; esac
    done
    if [ "$active" = 1 ] && [ "$observed" = 0 ]; then
        echo 'KAS LAB STOP: boot_startup_loop_observed'
        observed=1
    fi
    [ "$uptime" -gt 300 ] && [ "$active" = 0 ] && break
    tries=$((tries+1)); [ "$tries" -le 70 ] || exit 3
    sleep 5
done
echo 'KAS LAB STOP: boot_startup_settled'
'''


def lab_stop_script(mirror, identities):
    simple = 'uninstall-remove.sh' in mirror.sources
    source = mirror.sources['uninstall-remove.sh' if simple else 'uninstall-transaction.sh'].decode()
    lock = source[source.index('# BEGIN MIHOMO LIFECYCLE LOCK'):source.index('# END MIHOMO LIFECYCLE LOCK')]
    if simple:
        guard = ('ROOT=\n' + source[source.index('sr_processes() {'):source.index('# A reference in another scheduled/init job')] +
                 '\ntxn_step_services_done() { sr_processes; [ "$SR_BUSY" -eq 0 ]; }\n')
    else:
        guard = source[source.index('txn_step_services_done() {'):source.index('# Refuse unproven live processes')]
    script = 'set -eu\n' + lock + r'''
fail() { exit 3; }
LAB_WATCHDOG_LOCK=/tmp/mihomo_watchdog.lock.d
LAB_WATCHDOG_HELD=0
lab_cleanup() {
    result=$?
    if [ "$result" != 0 ] && command -v lab_diagnose >/dev/null 2>&1; then
        lab_diagnose || true
    fi
    ml_lifecycle_release || result=3
    if [ "$LAB_WATCHDOG_HELD" = 1 ]; then
        ml_lock_release "$LAB_WATCHDOG_LOCK" || result=3
    fi
    trap - EXIT
    exit "$result"
}
trap lab_cleanup EXIT
trap 'exit 3' INT TERM HUP
ml_lifecycle_acquire || exit 3
echo 'KAS LAB STOP: lifecycle_locked'
verify() {
    [ "$(ml_starttime "$1")" = "$2" ] &&
    [ /proc/$1/exe -ef /opt/bin/$3 ] &&
    [ "$(sha256sum /proc/$1/exe | cut -d' ' -f1)" = "$4" ] &&
    [ "$(sha256sum /opt/etc/init.d/$6 | cut -d' ' -f1)" = "$5" ] &&
    [ "$(tr '\000' '|' < /proc/$1/cmdline)" = "$7" ] &&
    awk '/^Uid:/ { found=1; if ($2 != 0 || $3 != 0 || $4 != 0 || $5 != 0) exit 1 }
         END { if (!found) exit 1 }' /proc/$1/status
}
'''
    records = []
    for name, (pid, start, binary, init_hash, args) in identities.items():
        init = 'S99mihomo' if name == 'mihomo' else 'S99magitrickle'
        record = ' '.join(shlex.quote(v) for v in (pid, start, name, binary, init_hash, init, args))
        records.append(record)
        script += 'verify ' + record + ' || exit 3\n'
    script += ('''if [ -f /opt/bin/mihomo_watchdog.sh ]; then\n''' if simple else '')
    script += '''before=$(grep -c 'maintenance in progress - checks skipped' /opt/var/log/mihomo_watchdog.log 2>/dev/null || true)
before=${before:-0}
tries=0
while :; do
    sh /opt/bin/mihomo_watchdog.sh || exit 3
    after=$(grep -c 'maintenance in progress - checks skipped' /opt/var/log/mihomo_watchdog.log 2>/dev/null || true)
    after=${after:-0}
    [ "$after" -gt "$before" ] && break
    tries=$((tries+1)); [ "$tries" -le 30 ] || exit 3
    sleep 2
done
echo 'KAS LAB STOP: watchdog_excluded'
'''
    script += ('fi\n' if simple else '')
    script += '''# Take the two locks in watchdog's order. Nothing has been stopped yet;
# drift in this short reacquisition interval is checked before any signal.
ml_lifecycle_release || exit 3
ml_lock_acquire "$LAB_WATCHDOG_LOCK" || exit 3
LAB_WATCHDOG_HELD=1
ml_lifecycle_acquire || exit 3
echo 'KAS LAB STOP: watchdog_run_locked'
'''
    for record in records:
        script += 'verify ' + record + ' || exit 3\n'
    for record in records:
        script += 'set -- ' + record + '''
verify "$@" || exit 3
echo "KAS LAB STOP: term_$3"
kill -TERM "$1" || exit 3
tries=0
while [ -d /proc/$1 ]; do
    state=$(sed 's/^.*) //' /proc/$1/stat | cut -d' ' -f1)
    case "$state" in Z|X) break;; esac
    if ! verify "$@"; then [ ! -d /proc/$1 ] && break; exit 3; fi
    tries=$((tries+1)); [ "$tries" -le 15 ] || exit 3
    if [ "$tries" = 10 ]; then
        echo "KAS LAB STOP: kill_$3"
        kill -KILL "$1" || exit 3
    fi
    sleep 2
done
echo "KAS LAB STOP: stopped_$3"
'''
    script += '\nroot=\n' + guard + '''
tries=0
until txn_step_services_done; do
    tries=$((tries+1)); [ "$tries" -le 5 ] || exit 3
    sleep 1
done
echo 'KAS LAB STOP: manual_stop_verified'
ml_lifecycle_release || exit 3
echo 'KAS LAB STOP: handoff_ready'
'''
    if simple:
        # Native PARTIAL already disabled the verified autostart and removed
        # watchdog files. No parent lock is inherited by the native remover.
        script += '\nml_lock_release "$LAB_WATCHDOG_LOCK" || exit 3\nLAB_WATCHDOG_HELD=0\n'
    return script


def lab_diagnostic_script(identities):
    comparisons = ''
    for name, (pid, start, binary, _, _) in identities.items():
        comparisons += (name + ') expected_pid=' + shlex.quote(pid) + '; expected_start='
                        + shlex.quote(start) + '; expected_binary=' + shlex.quote(binary) + ';;\n')
    return r'''
lab_diagnose() {
    umask 077
    file=/opt/lab/uninstall-blocked.private
    : > "$file"; chmod 600 "$file"
    found=0
    for proc in /proc/[0-9]*; do
        [ -d "$proc" ] || continue
        p=${proc##*/}
        comm=$(cat "$proc/comm" 2>/dev/null) || continue
        exe=$(readlink "$proc/exe" 2>/dev/null) || exe=unreadable
        name=''
        case "$comm" in mihomo|magitrickled) name=$comm;; esac
        case "${exe% (deleted)}" in */mihomo) name=mihomo;; */magitrickled) name=magitrickled;; esac
        for n in mihomo magitrickled; do
            [ "$proc/exe" -ef "/opt/bin/$n" ] && name=$n
        done
        [ -n "$name" ] || continue
        found=1
        start=$(ml_starttime "$p") || start=unknown
        binary=$(sha256sum "$proc/exe" 2>/dev/null | cut -d' ' -f1)
        expected_pid=''; expected_start=''; expected_binary=''
        case "$name" in
''' + comparisons + r'''
        esac
        identity=identity_changed
        [ -n "$expected_pid" ] || identity=identity_unrecorded
        [ "$start" != unknown ] || identity=identity_unknown
        if [ "$p" = "$expected_pid" ] && [ "$start" = "$expected_start" ]; then identity=identity_same; fi
        printf 'KAS LAB DIAG: %s_%s\n' "$name" "$identity"
        payload=payload_changed
        [ -n "$expected_binary" ] && [ "$binary" = "$expected_binary" ] && payload=payload_same
        printf 'KAS LAB DIAG: %s_%s\n' "$name" "$payload"
        stat=$(cat "$proc/stat" 2>/dev/null) || stat=''
        tail=${stat##*) }
        parent=$(printf '%s\n' "$tail" | awk '{print $2}')
        category=parent_unknown
        case "$parent" in
            ''|*[!0-9]*) parent=0;;
            1) category=parent_reparented;;
            *)
                parent_args=$(tr '\000' '|' < /proc/$parent/cmdline 2>/dev/null) || parent_args=''
                case "$parent_args" in
                    *'|/opt/bin/mihomo_watchdog.sh|'*|*'|/opt/etc/cron.5mins/mihomo_watchdog|'*) category=parent_watchdog_argv;;
                    *'|/opt/etc/init.d/S99mihomo|'*|*'|/opt/etc/init.d/S99magitrickle|'*) category=parent_init_argv;;
                esac
                [ "$parent" != "$$" ] || category=parent_controller
                ;;
        esac
        printf 'KAS LAB DIAG: %s_%s\n' "$name" "$category"
        {
            printf 'name=%s pid=%s start=%s expected_pid=%s expected_start=%s exe=%s parent=%s category=%s\n' \
                "$name" "$p" "$start" "$expected_pid" "$expected_start" "$exe" "$parent" "$category"
            printf 'stat=%s\n' "$stat"
            grep '^Uid:' "$proc/status"
            sha256sum "$proc/exe" "$proc/cmdline"
            if [ "$parent" != 0 ]; then
                cat /proc/$parent/stat
                readlink /proc/$parent/exe
                sha256sum /proc/$parent/cmdline
            fi
        } >> "$file" 2>&1
    done
    [ "$found" = 1 ] || echo 'KAS LAB DIAG: no_matching_process'
}
'''


def simple_uninstall(guest, mirror, evidence, foreign, revision, origin):
    """Exercise the public simple command, including its honest PARTIAL path.

    Stopping processes below is controller-only: fresh VM origin and exact
    identities are required by the existing lab_stop_script. Native remove
    never receives that authority and never invokes transaction preparation.
    """
    def fail(reason='native_uninstall_failed'):
        evidence.set('KAS_UNINSTALL', 'FAIL', reason)
        return False
    yaml_command = 'sha256sum /opt/etc/mihomo/config.yaml /opt/var/lib/magitrickle/config.yaml'
    yaml_before = guest.command(yaml_command)
    guest.command('iptables -t mangle -A PREROUTING -p udp --dport 65000 -j RETURN')
    foreign_rule = guest.command('iptables -t mangle -S PREROUTING | grep 65000')
    try:
        code, _, _ = guest.execute('sh', timeout=370, input_data=lab_startup_settle_script())
        if code != 0:
            raise ContractFailure('lab_process_provenance_unproven')
        before = lab_identities(guest, origin)
        code, output, errors = guest.execute('sh /opt/lab/uninstall.sh --remove', timeout=180, input_data='REMOVE\n')
        if code != 1 or '[PARTIAL]' not in output:
            return fail()
        after = lab_identities(guest, origin)
        if ({k: (v[0], v[1], v[2], v[4]) for k, v in before.items()} !=
                {k: (v[0], v[1], v[2], v[4]) for k, v in after.items()}):
            return fail('native_uninstall_guard_failed')
        guest.command('test ! -e /opt/bin/mihomo_watchdog.sh && '
                      'test ! -e /opt/etc/cron.5mins/mihomo_watchdog && '
                      'grep -qx ENABLED=no /opt/etc/init.d/S99mihomo && '
                      'grep -qx ENABLED=no /opt/etc/init.d/S99magitrickle')
        evidence.check('KAS_UNINSTALL_GUARD', 'PASS', 'native_command_verified')
        print('KAS SIMPLE REMOVE: active processes preserved, autostart disabled, native PARTIAL verified', flush=True)
        stop = lab_stop_script(mirror, after)
        code, output, errors = guest.execute('sh', timeout=180, input_data=stop)
        if code != 0:
            raise ContractFailure('lab_manual_stop_failed')
        evidence.check('KAS_MANUAL_STOP', 'PASS', 'native_command_verified')
        # The production command excludes watchdog itself; no inherited lock,
        # snapshot, gate, cache or recovery journal is supplied by the controller.
        for attempt in range(2):
            code, output, errors = guest.execute('sh /opt/lab/uninstall.sh --remove', timeout=240, input_data='REMOVE\n')
            if code not in (0, 1) or ('[SUCCESS]' if code == 0 else '[PARTIAL]') not in output:
                return fail()
            allowed = ('/opt/etc/opkg/magitrickle.conf', '/opt/etc/init.d/S00ubifs',
                       '/opt/etc/keenetic-auto-setup-mihomo.state')
            if any(line.startswith('[LEFT] ') and not any(line.startswith('[LEFT] ' + p + ' —') for p in allowed)
                   for line in output.splitlines()):
                return fail()
            print('KAS SIMPLE REMOVE: native result ' + ('SUCCESS' if code == 0 else 'PARTIAL') +
                  ('; repeated command returned; postconditions pending' if attempt else '; postconditions pending'), flush=True)
        # Never lose the failed postcondition behind a generic runner error.
        # Only fixed labels leave the private guest channel, never its text.
        def probe(label, command, expected=None):
            code, data, _ = guest.execute(command)
            ok = code == 0 and (expected is None or data == expected)
            print('KAS SIMPLE VERIFY: ' + label + (' PASS' if ok else ' FAIL'), flush=True)
            return ok, data

        code, proxy, _ = guest.execute("awk -F'|' '$2==\"proxy\" {print $3}' /opt/etc/keenetic-auto-setup/ownership.v1")
        if code != 0 or not re.fullmatch(r'Proxy[0-9]+', proxy):
            print('KAS SIMPLE VERIFY: ledger_proxy FAIL', flush=True)
            return fail('native_ownership_invalid')
        print('KAS SIMPLE VERIFY: ledger_proxy PASS', flush=True)
        checks = [
            ('packages_absent', 'list=$(opkg list-installed) || exit 3; ! printf "%s\\n" "$list" | grep -E "^(mihomo|magitrickle) "'),
            ('mangle_absent', 'rules=$(iptables -t mangle -S) || exit 3; ! printf "%s\\n" "$rules" | grep _CUST_BYPASS_WA_'),
            ('routing_absent', 'cfg=$(/bin/ndmc -c "show running-config") || exit 3; '
             'cfg=$(printf "%s\\n" "$cfg" | tr -d "\\r"); '
             'printf "%s\\n" "$cfg" | grep -q "^interface " || exit 3; '
             '! printf "%s\\n" "$cfg" | grep -E "^ip policy bypass_wa$|^interface ' + proxy + '$"'),
            ('locks_absent', 'test ! -e /tmp/mihomo-lifecycle.lock.d && test ! -e /tmp/mihomo_watchdog.lock.d && test ! -e /tmp/mihomo.maintenance'),
        ]
        for label, command in checks:
            if not probe(label, command)[0]:
                return fail()
        if not probe('yaml_preserved', yaml_command, yaml_before)[0]:
            return fail('mihomo_config_changed')
        if not foreign_rule or not probe('foreign_netfilter', 'iptables -t mangle -S PREROUTING | grep 65000', foreign_rule)[0]:
            return fail('netfilter_changed')
        if foreign is not None:
            if not probe('foreign_proxy', '/bin/ndmc -c "show running-config" | grep -A4 "^interface Proxy0$" | grep 1081')[0]:
                return fail('foreign_proxy_changed')
            if not probe('foreign_cron', 'grep foreign-job /opt/etc/crontab')[0]:
                return fail('foreign_cron_changed')
        print('KAS SIMPLE VERIFY: postconditions PASS', flush=True)
    except ContractFailure as error:
        evidence.set('KAS_UNINSTALL', 'BLOCKED', error.reason)
        return False
    if not install(guest, mirror, revision, evidence, foreign) or not wan_guard(guest, evidence):
        return fail()
    doctor_pass = doctor(guest, evidence, revision)
    # A Doctor warning is retained in its own gate and counters. Removal has
    # independent evidence; never claim Doctor PASS from lack of FAIL.
    print('KAS SIMPLE REMOVE: reinstall Doctor ' + evidence.gates['KAS_DOCTOR']['status'], flush=True)
    evidence.set('KAS_UNINSTALL', 'PASS', 'native_uninstall_passed')
    return doctor_pass


def uninstall(guest, mirror, evidence, foreign, revision, origin=None):
    if 'uninstall-remove.sh' in mirror.sources:
        return simple_uninstall(guest, mirror, evidence, foreign, revision, origin)
    def phase(marker):
        # Public fixed-format progress marker; the controller side owns it.
        print('KAS UNINSTALL PHASE: ' + marker, flush=True)
    phase('contract snapshot')
    before = contract(guest, mirror, foreign)
    phase('read-only --check')
    guest.command('sh /opt/lab/uninstall.sh --check')
    phase('read-only --plan')
    guest.execute('sh /opt/lab/uninstall.sh --plan')
    ready = False
    snapshot_name = None
    phase('snapshot --check')
    code, output, errors = guest.execute('sh /opt/lab/uninstall-snapshot.sh --check')
    if code == 0:
        phase('snapshot --create')
        code, output, errors = guest.execute('sh /opt/lab/uninstall-snapshot.sh --create')
        match = re.search(r'snapshot created: /opt/kas-uninstall-backups/([A-Za-z0-9_-]+)', output)
        if code == 0 and match:
            snapshot_name = match[1]
            code, output, errors = guest.execute('sh /opt/lab/uninstall-snapshot-verify.sh ' + snapshot_name)
            ready = code == 0
    evidence.check('KAS_UNINSTALL_SNAPSHOT', 'PASS' if ready else 'BLOCKED',
                   'native_command_verified' if ready else 'snapshot_gate_blocked')
    transaction = False
    if ready:
        phase('transaction gate --check')
        code, output, errors = guest.execute('sh /opt/lab/uninstall-transaction-gate.sh --check ' + snapshot_name)
        if code == 0:
            phase('transaction --prepare')
            code, output, errors = guest.execute('sh /opt/lab/uninstall-transaction.sh --prepare ' + snapshot_name)
            transaction = code == 0
            for line in (plain(output) + '\n' + errors).splitlines():
                if line.startswith(('[BLOCKED]', '[PASS]', '[REVIEW]', '[INFO]')):
                    print('KAS PREPARE GUEST: ' + line, flush=True)
    evidence.check('KAS_TRANSACTION_GATE', 'PASS' if transaction else 'BLOCKED',
                   'native_command_verified' if transaction else 'snapshot_gate_blocked')
    # The frozen baseline's remove branch has no deletion code. On a changed
    # candidate, a rejected transaction must prevent invoking --remove at all.
    from pathlib import Path
    import json
    lock = json.loads(Path(__file__).with_name('kas-assets.lock.json').read_text())
    known_no_remove = hashlib.sha256(mirror.sources['uninstall.sh']).hexdigest() == lock['uninstall_no_remove_sha256']
    if not transaction and not known_no_remove:
        evidence.set('KAS_UNINSTALL', 'BLOCKED', 'snapshot_gate_blocked')
        return False
    if not known_no_remove:
        phase('live-process refusal and controller-owned manual stop')
        try:
            phase('wait for KN init boot loop to finish naturally')
            # execute() returns nonzero without raising: require the actual
            # bounded read-only prerequisite before trusting daemon identities.
            code, output, errors = guest.execute('sh', timeout=370, input_data=lab_startup_settle_script())
            if code != 0:
                raise ContractFailure('lab_process_provenance_unproven')
            if 'KAS LAB STOP: boot_startup_loop_observed' in output.splitlines():
                print('KAS LAB STOP: boot_startup_loop_observed', flush=True)
            print('KAS LAB STOP: boot_startup_settled', flush=True)
            identities = lab_identities(guest, origin)
            code, output, errors = guest.execute('sh /opt/lab/uninstall.sh --remove', timeout=120)
            if (code != 3 or 'live service ownership unproven' not in output + errors
                    or before != contract(guest, mirror, foreign)
                    or identities != lab_identities(guest, origin)):
                raise ContractFailure('native_uninstall_guard_failed')
            evidence.check('KAS_UNINSTALL_GUARD', 'PASS', 'native_command_verified')
            guest.command('[ ! -e /tmp/mihomo-lifecycle.lock.d ] && [ ! -e /tmp/mihomo.maintenance ] && '
                          '[ ! -e /opt/kas-uninstall-backups/' + snapshot_name + '/transaction/journal.v1 ]')
            snapshot = shlex.quote(snapshot_name)
            script = lab_diagnostic_script(identities) + lab_stop_script(mirror, identities)
            script += ('\nsh /opt/lab/uninstall-snapshot-verify.sh ' + snapshot + ' || exit 3\n'
                       'sh /opt/lab/uninstall-transaction-gate.sh --check ' + snapshot + ' || exit 3\n'
                       'sh /opt/lab/uninstall-transaction.sh --prepare ' + snapshot + ' || exit 3\n'
                       "echo 'KAS LAB STOP: native_remove_begin'\n"
                       'sh /opt/lab/uninstall.sh --remove ' + snapshot + '\n')
            code, output, errors = guest.execute('sh', timeout=1200, input_data=script)
            markers = {'lifecycle_locked', 'watchdog_excluded', 'watchdog_run_locked',
                       'manual_stop_verified', 'handoff_ready', 'native_remove_begin'} | {
                action + '_' + name for action in ('term', 'kill', 'stopped')
                for name in ('mihomo', 'magitrickled')}
            for line in output.splitlines():
                if line.removeprefix('KAS LAB STOP: ') in markers:
                    print(line, flush=True)
            categories = {'no_matching_process'} | {
                name + '_' + category for name in ('mihomo', 'magitrickled') for category in
                ('identity_same', 'identity_changed', 'identity_unknown', 'identity_unrecorded',
                 'payload_same', 'payload_changed', 'parent_unknown', 'parent_reparented',
                 'parent_watchdog_argv', 'parent_init_argv', 'parent_controller')}
            for line in output.splitlines():
                if line.removeprefix('KAS LAB DIAG: ') in categories:
                    print(line, flush=True)
            if code != 0:
                try:
                    detail_code, details, _ = guest.execute('cat /opt/lab/uninstall-blocked.private')
                    if detail_code != 0:
                        raise RuntimeError('private diagnostic unavailable')
                    path = guest.private / 'uninstall-processes.private'
                    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as file:
                        file.write(details)
                except (RuntimeError, TimeoutError, OSError):
                    print('KAS LAB DIAG: private_snapshot_unavailable', flush=True)
            if 'KAS LAB STOP: manual_stop_verified' not in output.splitlines():
                raise ContractFailure('lab_manual_stop_failed')
            evidence.check('KAS_MANUAL_STOP', 'PASS', 'native_command_verified')
            print('KAS MANUAL STOP: VM origin, exact identities, watchdog exclusion PASS', flush=True)
            if 'KAS LAB STOP: native_remove_begin' not in output.splitlines():
                raise ContractFailure('snapshot_gate_blocked')
        except ContractFailure as error:
            evidence.set('KAS_UNINSTALL', 'BLOCKED', error.reason)
            return False
    if known_no_remove:
        phase('code-3 guard --remove')
        code, output, errors = guest.execute('sh /opt/lab/uninstall.sh --remove', timeout=900)
    phase('--remove finished with exit %d' % code)
    if known_no_remove:
        if code == 3 and '[BLOCKED]' in output + errors and contract(guest, mirror, foreign) == before:
            evidence.set('KAS_UNINSTALL', 'BLOCKED', 'native_remove_not_implemented')
            evidence.check('KAS_UNINSTALL_GUARD', 'PASS', 'native_command_verified')
            print('KAS UNINSTALL: native code-3 guard verified; no removal implemented by this KAS revision', flush=True)
            return True
        evidence.set('KAS_UNINSTALL', 'FAIL', 'execution_failed')
        return False

    # Real transactional removal: the journal-bearing engine must complete,
    # leave ONLY ledger-owned resources removed, accept an idempotent re-run,
    # and the guest must accept a clean reinstall afterwards.
    if code != 0 or '[PASS] KAS removal transaction complete' not in plain(output):
        for line in (plain(output) + '\n' + errors).splitlines():
            if line.startswith(('[BLOCKED]', '[PASS]', '[REVIEW]', '[INFO]')):
                print('KAS UNINSTALL GUEST: ' + line, flush=True)
        evidence.set('KAS_UNINSTALL', 'FAIL', 'native_uninstall_failed')
        return False
    if not removal_teardown_clean(guest, foreign):
        evidence.set('KAS_UNINSTALL', 'FAIL', 'native_uninstall_failed')
        return False
    phase('idempotent second --remove')
    code, output, errors = guest.execute('sh /opt/lab/uninstall.sh --remove', timeout=300)
    phase('second --remove finished with exit %d' % code)
    if code != 0 or 'already fully removed' not in plain(output):
        evidence.set('KAS_UNINSTALL', 'FAIL', 'native_uninstall_failed')
        return False
    evidence.check('KAS_UNINSTALL_GUARD', 'PASS', 'native_command_verified')
    print('KAS UNINSTALL: owned resources removed, foreign preserved, idempotent re-run PASS', flush=True)
    phase('clean reinstall')
    if not install(guest, mirror, revision, evidence, foreign):
        evidence.set('KAS_UNINSTALL', 'FAIL', 'native_uninstall_failed')
        return False
    phase('post-reinstall doctor')
    # Actual offline-WAN checks generate history; no fabricated healthy entry.
    if not wan_guard(guest, evidence):
        evidence.set('KAS_UNINSTALL', 'BLOCKED', 'execution_failed')
        return False
    if not doctor(guest, evidence, revision):
        verdict = evidence.gates['KAS_DOCTOR']
        evidence.set('KAS_UNINSTALL', verdict['status'], verdict['reason'])
        print('KAS UNINSTALL: removal and reinstall completed; Doctor ' + verdict['status'], flush=True)
        return False
    evidence.set('KAS_UNINSTALL', 'PASS', 'native_uninstall_passed')
    print('KAS UNINSTALL: clean reinstall and Doctor passed after removal', flush=True)
    return True


def removal_teardown_clean(guest, foreign):
    """Every KAS-owned artifact is gone; user and foreign resources survive."""
    def absent(command):
        code, output, errors = guest.execute(command)
        return code != 0 or not output.strip()
    def present(command):
        code, output, errors = guest.execute(command)
        return code == 0 and bool(output.strip())

    def owned_proxy_removed(guest):
        # The engine records the interface it removed (journal line
        # 'owned-proxy ProxyN'). Assert THAT interface is absent; a foreign
        # ProxyN to another upstream must not influence this check.
        code, output, _ = guest.execute(
            'grep -m1 owned-proxy /opt/kas-uninstall-backups/*/transaction/journal.v1')
        if code != 0:
            return False
        match = re.search(r'owned-proxy (Proxy[0-9]+)', output)
        if not match:
            return False
        return absent('/bin/ndmc -c "show running-config" | grep -c "^interface ' + match.group(1) + '$"')

    checks = [
        ('locks and maintenance cleaned', guest.execute(
            'test ! -e /tmp/mihomo-lifecycle.lock.d && '
            'test ! -e /tmp/mihomo_watchdog.lock.d && '
            'test ! -e /tmp/mihomo.maintenance')[0] == 0),
        ('ledger removed', absent('cat /opt/etc/keenetic-auto-setup/ownership.v1')),
        ('watchdog wrapper removed', absent('cat /opt/etc/cron.5mins/mihomo_watchdog')),
        ('watchdog binary removed', absent('cat /opt/bin/mihomo_watchdog.sh')),
        ('netfilter hook removed', absent('cat /opt/etc/ndm/netfilter.d/020-bypass_wa.sh')),
        ('owned feed removed', absent('cat /opt/etc/opkg/magitrickle.conf')),
        ('owned cron schedule removed', absent("grep mihomo_watchdog /opt/etc/crontab")),
        ('mangle chain removed', absent('iptables -t mangle -S _CUST_BYPASS_WA_')),
        ('policy removed', absent('/bin/ndmc -c "show running-config" | grep -c "^ip policy bypass_wa$"')),
        ('owned proxy removed', owned_proxy_removed(guest)),
        ('packages removed', absent('opkg list-installed | grep -E "^(mihomo|magitrickle) "')),
        ('dns intercept preserved', present('/bin/ndmc -c "show running-config" | grep "intercept enable"')),
        ('journal complete', present('tail -n 1 /opt/kas-uninstall-backups/*/transaction/journal.v1 | grep -x complete')),
        ('rollback cache present', present('cat /opt/kas-uninstall-backups/*/packages/MANIFEST')),
    ]
    if foreign is not None:
        checks.append(('foreign proxy preserved',
                       present('/bin/ndmc -c "show running-config" | grep -A4 "^interface Proxy0$" | grep 1081')))
        checks.append(('foreign cron preserved',
                       present('grep foreign-job /opt/etc/crontab')))
    for label, ok in checks:
        if not ok:
            print('KAS UNINSTALL teardown: ' + label + ' FAILED', flush=True)
            return False
    return True


def lifecycle(guest, mirror, revision, evidence, context, private, phase):
    phase['name'] = 'KAS_INSTALL'
    evidence.set('KAS_INSTALL', 'NOT_TESTED', 'unattempted')
    try:
        prepare(guest, mirror, revision)
    except Exception:
        evidence.set('KAS_INSTALL', 'BLOCKED', 'verified_tls_source_failed')
        return False
    context['snapshot'](private / 'before-kas')
    try:
        origin = lab_origin(guest, mirror)
    except ContractFailure as error:
        evidence.set('KAS_INSTALL', 'BLOCKED', error.reason)
        return False
    if not install(guest, mirror, revision, evidence):
        return False
    if not wan_guard(guest, evidence):
        return False
    phase['name'] = 'KAS_DOCTOR'
    evidence.set('KAS_DOCTOR', 'NOT_TESTED', 'unattempted')
    if not doctor(guest, evidence, revision):
        return False
    if not idempotence(guest, mirror, revision, evidence, None):
        return False
    if not interrupted_repeat(guest, mirror, revision, evidence):
        return False
    phase['name'] = 'KAS_REBOOT'
    evidence.set('KAS_REBOOT', 'NOT_TESTED', 'unattempted')
    if not reboot(guest, mirror, evidence, context, None):
        return False
    phase['name'] = 'KAS_ROLLBACK'
    evidence.set('KAS_ROLLBACK', 'NOT_TESTED', 'unattempted')
    if not update_and_rollback(guest, mirror, revision, evidence, None):
        return False
    phase['name'] = 'KAS_UNINSTALL'
    evidence.set('KAS_UNINSTALL', 'NOT_TESTED', 'unattempted')
    return uninstall(guest, mirror, evidence, None, revision, origin)


def foreign_profile(args, private, credentials, coldboot, mirror, port, tls_port, evidence):
    import json
    from types import SimpleNamespace
    from lab.gates import Evidence
    from lab.guest import Guest
    from lab.run import run
    evidence.check('KAS_FOREIGN_RESOURCES', 'NOT_TESTED', 'unattempted')
    root = private / 'foreign-profile'
    root.mkdir(mode=0o700)
    scenario = root / 'scenario.json'
    scenario.write_text(json.dumps({'name': 'Foreign Proxy and cron preservation',
        'evidence': 'KEENETIC_PORTED_EXPERIMENTAL', 'steps': coldboot + [
            {'action': 'wait_serial', 'pattern': 'Core::Server: system ready', 'timeout_sec': 240}]}))
    local = Evidence()
    completed = {'ok': False}
    started = {'probes': False}
    # The published check carries one fixed public label naming the actual
    # failing phase; guest text, UART output and configuration stay private.
    failure = {'reason': 'execution_failed'}

    def probes(**context):
        guest = Guest(credentials, root, deadline=context['deadline'])
        started['probes'] = True
        try:
            try:
                guest.connect(240)
            except TimeoutError:
                failure['reason'] = 'native_command_timed_out'
                return
            except RuntimeError:
                failure['reason'] = 'authentication_rejected'
                return
            failure['reason'] = 'verified_tls_source_failed'
            prepare(guest, mirror, args.kas_ref)
            failure['reason'] = 'foreign_setup_failed'
            foreign = foreign_resources(guest)
            origin = lab_origin(guest, mirror)
            failure['reason'] = 'native_installer_failed'
            if not install(guest, mirror, args.kas_ref, local, foreign):
                recorded = local.gates['KAS_INSTALL']
                if recorded['status'] == 'FAIL':
                    failure['reason'] = recorded['reason']
                return
            failure['reason'] = 'idempotence_failed'
            if not idempotence(guest, mirror, args.kas_ref, local, foreign):
                recorded = local.checks.get('KAS_IDEMPOTENCE')
                if recorded and recorded['status'] == 'FAIL':
                    failure['reason'] = recorded['reason']
                return
            if not reboot(guest, mirror, local, context, foreign):
                recorded = local.gates['KAS_REBOOT']
                failure['reason'] = recorded['reason'] if recorded['status'] == 'FAIL' else 'native_reboot_failed'
                return
            completed['ok'] = uninstall(guest, mirror, local, foreign, args.kas_ref, origin)
            if not completed['ok']:
                recorded = local.gates['KAS_UNINSTALL']
                failure['reason'] = recorded['reason'] if recorded['status'] == 'FAIL' else 'execution_failed'
        except ContractFailure as error:
            failure['reason'] = error.reason
        except TimeoutError:
            failure['reason'] = 'native_command_timed_out'
        except RuntimeError:
            failure['reason'] = 'native_command_failed'
        finally:
            guest.close()

    snapshot = private / 'before-kas'
    result = run(SimpleNamespace(scenario=scenario, emulator_root=args.emulator_root,
        nand_dir=snapshot / 'flash', opt_image=snapshot / 'opt.img',
        output_dir=root / 'uart', max_seconds=1500, preset='cudy-wr3000p-v1',
        lan_forwards='8080:80;8022:222', mirror_port=port,
        mirror_tls_port=tls_port, after_scenario=probes))
    passed = result == 0 and completed['ok']
    # The clean profile must not hide the foreign profile's Doctor verdict.
    verdict = local.gates['KAS_DOCTOR']
    evidence.check('KAS_FOREIGN_DOCTOR', verdict['status'], verdict['reason'])
    for key, value in local.measurements.items():
        evidence.measurement('foreign_' + key, value)
    if passed:
        evidence.check('KAS_FOREIGN_RESOURCES', 'PASS', 'native_command_verified')
    else:
        if result != 0:
            # The UART scenario failed, or the controller hook raised; only a
            # scenario failure may be reported as a serial timeout.
            failure['reason'] = 'serial_timeout' if not started['probes'] else 'runner_failure'
        blocked = local.gates['KAS_UNINSTALL']
        if result == 0 and (verdict['status'] == 'BLOCKED' or blocked['status'] == 'BLOCKED'):
            evidence.check('KAS_FOREIGN_RESOURCES', 'BLOCKED',
                           verdict['reason'] if verdict['status'] == 'BLOCKED' else blocked['reason'])
        else:
            evidence.check('KAS_FOREIGN_RESOURCES', 'FAIL', failure['reason'])
    return passed

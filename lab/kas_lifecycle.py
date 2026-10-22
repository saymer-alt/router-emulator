"""Run the real committed KAS scripts inside the authenticated disposable OS."""
import hashlib
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
    if code == 0 and failures and int(failures[1]) == 0 and 'Overall status: OK' in output:
        evidence.set('KAS_DOCTOR', 'PASS', 'native_doctor_passed')
    elif code == 1 and failures and int(failures[1]) == 0 and 'Overall status: WARN' in output:
        evidence.set('KAS_DOCTOR', 'BLOCKED', 'doctor_warning')
    else:
        evidence.set('KAS_DOCTOR', 'FAIL', 'native_doctor_failed')
    print('KAS DOCTOR: ' + evidence.gates['KAS_DOCTOR']['status'], flush=True)
    return code in (0, 1) and failures and int(failures[1]) == 0


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
            raise RuntimeError('duplicate native service processes')
        if code == 0 and len(rows) == 2 and all(len(line.split()) == 1 and line.strip().isdigit() for line in rows):
            return
        time.sleep(3)
    raise RuntimeError('actual Mihomo and MagiTrickle daemons not ready')


def contract(guest, mirror, foreign=None):
    wait_services(guest)
    ledger = guest.command('cat /opt/etc/keenetic-auto-setup/ownership.v1')
    if guest.command('ls -ld /opt/etc/keenetic-auto-setup/ownership.v1').split()[0] != '-rw-------':
        raise RuntimeError('ownership journal permissions are not private')
    if not ledger.startswith('KAS_OWNERSHIP_V1\n'):
        raise RuntimeError('native ownership journal invalid')
    rows = ledger.splitlines()[1:]
    if len(rows) != len(set(rows)):
        raise RuntimeError('duplicate native ownership records')
    proxies = [row[12:] for row in rows if row.startswith('owned|proxy|')]
    if len(proxies) != 1 or not re.fullmatch(r'Proxy\d+', proxies[0]):
        raise RuntimeError('project Proxy ownership ambiguous')
    for package in ('mihomo', 'magitrickle'):
        if 'owned|package|' + package not in rows:
            raise RuntimeError('new KAS package ownership missing')
    config = plain(guest.command('/bin/ndmc -c "show running-config"'))
    proxy = block(config, 'interface ' + proxies[0])
    if not all(value in proxy for value in ('proxy protocol socks5', 'proxy socks5-udp', 'proxy upstream 127.0.0.1 7890')):
        raise RuntimeError('actual Proxy upstream differs from KAS contract')
    policy = block(config, 'ip policy bypass_wa')
    if 'permit global ' + proxies[0] not in policy:
        raise RuntimeError('actual bypass policy lacks project exit')
    if not re.search(r'(?m)^\s+intercept enable\s*$', config):
        raise RuntimeError('native DNS interception absent')
    rules = guest.command('iptables -t mangle -S _CUST_BYPASS_WA_')
    entries = [line for line in rules.splitlines() if line.startswith('-A ')]
    if len(entries) != 3 or not all(value in entries[index] for index, value in
        enumerate(('-j MARK', '-j CONNMARK --save-mark', '-j RETURN'))):
        raise RuntimeError('actual MARK/CONNMARK rules absent')
    links = [line for line in guest.command('iptables -t mangle -S PREROUTING').splitlines()
             if line.endswith(' -j _CUST_BYPASS_WA_')]
    if len(links) != 1 or not re.search(r'--mark 0x0(?:/0xffffffff)? -j ', links[0]):
        raise RuntimeError('native PREROUTING link absent or duplicated')
    hashes = {}
    for line in guest.command('sha256sum ' + ' '.join(STABLE_FILES)).splitlines():
        digest, path = line.split(None, 1)
        if not re.fullmatch('[a-f0-9]{64}', digest) or path not in STABLE_FILES:
            raise RuntimeError('installed file digest unavailable')
        hashes[path] = digest
    expected = {'/opt/bin/mihomo_watchdog.sh': 'mihomo-watchdog.sh',
                '/opt/etc/ndm/netfilter.d/020-bypass_wa.sh': '020-bypass-wa.sh'}
    for path, source in expected.items():
        if hashes.get(path) != hashlib.sha256(mirror.sources[source]).hexdigest():
            raise RuntimeError('installed KAS script differs from committed source')
    if foreign is not None:
        if block(config, 'interface Proxy0') != foreign['proxy'] or 'owned|proxy|Proxy0' in rows:
            raise RuntimeError('foreign Proxy0 changed or was claimed')
        if foreign['cron'] not in guest.command('cat /opt/etc/crontab'):
            raise RuntimeError('foreign cron entry removed')
    response = guest.command('curl -fSs --max-time 15 --proxy socks5h://127.0.0.1:7890 http://192.168.1.100/lab/health')
    if response != 'KEENETIC_LAB_HTTP_OK':
        raise RuntimeError('real Mihomo SOCKS fixture request failed')
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
    before = contract(guest, mirror, foreign)
    code, output, errors = guest.execute('KEENETIC_AUTO_SETUP_REF=' + revision +
        ' sh /opt/lab/install.sh disk', timeout=600)
    after = contract(guest, mirror, foreign)
    if code != 0 or '[OK] Done' not in plain(output) or before != after:
        evidence.check('KAS_IDEMPOTENCE', 'FAIL', 'idempotence_failed')
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


def uninstall(guest, mirror, evidence, foreign):
    before = contract(guest, mirror, foreign)
    guest.command('sh /opt/lab/uninstall.sh --check')
    guest.execute('sh /opt/lab/uninstall.sh --plan')
    ready = False
    snapshot_name = None
    code, output, errors = guest.execute('sh /opt/lab/uninstall-snapshot.sh --check')
    if code == 0:
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
        code, output, errors = guest.execute('sh /opt/lab/uninstall-transaction-gate.sh --check ' + snapshot_name)
        if code == 0:
            code, output, errors = guest.execute('sh /opt/lab/uninstall-transaction.sh --prepare ' + snapshot_name)
            transaction = code == 0
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
    code, output, errors = guest.execute('sh /opt/lab/uninstall.sh --remove')
    if known_no_remove and code == 3 and '[BLOCKED]' in output + errors and contract(guest, mirror, foreign) == before:
        evidence.set('KAS_UNINSTALL', 'BLOCKED', 'native_remove_not_implemented')
        evidence.check('KAS_UNINSTALL_GUARD', 'PASS', 'native_command_verified')
        print('KAS UNINSTALL: native code-3 guard verified; no removal implemented by this KAS revision', flush=True)
        return True
    evidence.set('KAS_UNINSTALL', 'FAIL', 'execution_failed')
    return False


def lifecycle(guest, mirror, revision, evidence, context, private, phase):
    phase['name'] = 'KAS_INSTALL'
    evidence.set('KAS_INSTALL', 'NOT_TESTED', 'unattempted')
    try:
        prepare(guest, mirror, revision)
    except Exception:
        evidence.set('KAS_INSTALL', 'BLOCKED', 'verified_tls_source_failed')
        return False
    context['snapshot'](private / 'before-kas')
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
    return uninstall(guest, mirror, evidence, None)


def foreign_profile(args, private, credentials, coldboot, mirror, port, tls_port, evidence):
    import json
    from types import SimpleNamespace
    from lab.gates import Evidence
    from lab.guest import Guest
    from lab.run import run
    root = private / 'foreign-profile'
    root.mkdir(mode=0o700)
    scenario = root / 'scenario.json'
    scenario.write_text(json.dumps({'name': 'Foreign Proxy and cron preservation',
        'evidence': 'KEENETIC_PORTED_EXPERIMENTAL', 'steps': coldboot + [
            {'action': 'wait_serial', 'pattern': 'Core::Server: system ready', 'timeout_sec': 240}]}))
    local = Evidence()
    completed = {'ok': False}

    def probes(**context):
        guest = Guest(credentials, root, deadline=context['deadline'])
        try:
            guest.connect(240)
            prepare(guest, mirror, args.kas_ref)
            foreign = foreign_resources(guest)
            if not install(guest, mirror, args.kas_ref, local, foreign):
                return
            if not idempotence(guest, mirror, args.kas_ref, local, foreign):
                return
            if not reboot(guest, mirror, local, context, foreign):
                return
            completed['ok'] = uninstall(guest, mirror, local, foreign)
        finally:
            guest.close()

    snapshot = private / 'before-kas'
    result = run(SimpleNamespace(scenario=scenario, emulator_root=args.emulator_root,
        nand_dir=snapshot / 'flash', opt_image=snapshot / 'opt.img',
        output_dir=root / 'uart', max_seconds=1500, preset='cudy-wr3000p-v1',
        lan_forwards='8080:80;8022:222', mirror_port=port,
        mirror_tls_port=tls_port, after_scenario=probes))
    passed = result == 0 and completed['ok']
    evidence.check('KAS_FOREIGN_RESOURCES', 'PASS' if passed else 'FAIL',
                   'native_command_verified' if passed else 'execution_failed')
    return passed

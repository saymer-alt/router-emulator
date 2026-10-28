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


def classify_iptables_error(exit_code, stderr):
    """Reduce private guest stderr to a small allowlist of public categories.

    Real stderr may contain host-specific data. It must stay only in the
    existing private SSH log, never in the public CI output or reports.
    """
    if exit_code == 0:
        return None
    msg = stderr.lower()
    if ('no chain/target/match by that name' in msg
            or 'chain does not exist' in msg
            or 'chain not found' in msg):
        return 'CHAIN_MISSING'
    if ('xtables lock' in msg or 'another app is currently holding' in msg
            or 'resource temporarily unavailable' in msg
            or 'could not get lock' in msg or 'cannot lock' in msg):
        return 'XTABLES_LOCK'
    return 'COMMAND_NONZERO'


def read_iptables_diagnostic(guest, command):
    """Return output or fixed failure category; keep all raw output private."""
    try:
        code, output, stderr = guest.execute(command)
    except TimeoutError:
        return None, 'TIMEOUT'
    except (OSError, EOFError):
        return None, 'SSH_ERROR'
    except Exception as error:
        # Paramiko transport and channel exceptions need no full guest text.
        # Restrict the decision to the *class*, not a private message.
        if type(error).__name__ in ('SSHException', 'ChannelException',
                                    'AuthenticationException'):
            return None, 'SSH_ERROR'
        return None, 'PROBE_ERROR'
    if code != 0:
        return None, classify_iptables_error(code, stderr)
    return output, None


def wait_netfilter_settled(guest, budget=60):
    # KeeneticOS may rewrite Netfilter asynchronously after Proxy down/up.
    # Do not relax the three-entry rule and one PREROUTING link assertion.
    # Classify command failures on a bounded 60s deadline instead of
    # treating all failures as proof of missing/changing rules.
    until = time.monotonic() + budget
    probe_failures = 0
    content_samples = 0
    last_shape = None
    failures = {}
    while True:
        rules, reason = read_iptables_diagnostic(
            guest, 'iptables -t mangle -S _CUST_BYPASS_WA_')
        if reason is not None:
            failures['CHAIN_' + reason] = failures.get('CHAIN_' + reason, 0) + 1
            probe_failures += 1
        else:
            prerouting, reason = read_iptables_diagnostic(
                guest, 'iptables -t mangle -S PREROUTING')
            if reason is not None:
                failures['PREROUTING_' + reason] = (
                    failures.get('PREROUTING_' + reason, 0) + 1)
                probe_failures += 1
            else:
                links = [line for line in prerouting.splitlines()
                         if line.endswith(' -j _CUST_BYPASS_WA_')]
                content_samples += 1
                entries = [line for line in rules.splitlines() if line.startswith('-A ')]
                last_shape = (len(entries), len(links), len(entries) == 3 and all(
                    value in entries[index] for index, value in
                    enumerate(('-j MARK', '-j CONNMARK --save-mark', '-j RETURN'))))
                if netfilter_settled(rules, links):
                    return rules, links
        if time.monotonic() >= until:
            # Keys only come from fixed enums and stage names, values are
            # integers. Never publish stderr/stdout, command text or secrets.
            counts = ','.join('%s:%d' % (key, failures[key])
                              for key in sorted(failures)) or 'none'
            print('KAS SETTLE DIAGNOSTIC: probe_failures=%d content_samples=%d '
                  'last_shape(entries,links,order_ok)=%s failure_categories=%s'
                  % (probe_failures, content_samples, last_shape, counts), flush=True)
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


def uninstall(guest, mirror, evidence, foreign, revision):
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
    phase('real --remove' if not known_no_remove else 'code-3 guard --remove')
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
    if not doctor(guest, evidence, revision):
        evidence.set('KAS_UNINSTALL', 'FAIL', 'native_uninstall_failed')
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
    return uninstall(guest, mirror, evidence, None, revision)


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
            completed['ok'] = uninstall(guest, mirror, local, foreign, args.kas_ref)
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
    if passed:
        evidence.check('KAS_FOREIGN_RESOURCES', 'PASS', 'native_command_verified')
    else:
        if result != 0:
            # The UART scenario failed, or the controller hook raised; only a
            # scenario failure may be reported as a serial timeout.
            failure['reason'] = 'serial_timeout' if not started['probes'] else 'runner_failure'
        evidence.check('KAS_FOREIGN_RESOURCES', 'FAIL', failure['reason'])
    return passed

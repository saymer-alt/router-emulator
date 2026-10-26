"""Real-package uninstall E2E for KAS uninstall-packages.sh (PR #235 lineage).

Exercises the minimal ordinary removal — stop the running services through the
native init.d scripts, then remove the mihomo and magitrickle Entware packages
through real `opkg remove` — and asserts the actual guest state before, after
and after a second run and a reboot. No mocks: every check runs the same
commands an operator would. KeeneticOS ProxyN/policy/DNS and live Netfilter
rules are deliberately NOT torn down; that partial scope is the published
contract of the uninstaller.
"""
import re
import time

from lab.kas_lifecycle import install, prepare
from lab.run import send_monitor

FOREIGN_PACKAGES = ('ca-bundle', 'curl', 'jq', 'iptables', 'ip-full', 'nano', 'cron', 'socat')
YAML_PATHS = ('/opt/etc/mihomo/config.yaml', '/opt/var/lib/magitrickle/config.yaml')
WATCHDOG_CRON = 'mihomo_watchdog'


def phase(marker):
    # Public fixed-format progress marker; guest text stays private.
    print('UNINSTALL E2E PHASE: ' + marker, flush=True)


class SnapshotBlocked(RuntimeError):
    def __init__(self, label):
        self.label = label
        super().__init__(label)


def probe(guest, command):
    """One transport retry; the command's own exit code is returned as is."""
    try:
        return guest.execute(command)
    except Exception:
        time.sleep(2)
        return guest.execute(command)


def snapshot_state(guest):
    """Read the real installed/running state.

    Each prerequisite of the uninstaller itself (packages installed, both
    daemons running, watchdog cron present, init autostart enabled) is
    mandatory and raises SnapshotBlocked with a public label. Observational
    state (foreign package list, mounts) is recorded without failing the
    snapshot; preservation is asserted later against this same record.
    """
    state = {}
    code, installed, errors = probe(guest, 'opkg list-installed')
    if code != 0:
        raise SnapshotBlocked('opkg_list_unavailable')
    state['installed'] = installed
    for package in ('mihomo', 'magitrickle'):
        if not re.search('(?m)^' + package + r' - ', installed):
            raise SnapshotBlocked('package_missing_before_removal')
    # The daemons must be observed running; right after install a watchdog
    # or init restart can briefly race the snapshot, so wait bounded (same
    # budget shape as the installer's own service wait) — never fabricate.
    until = time.monotonic() + 30
    pids = ''
    rows = []
    while True:
        code, pids, errors = probe(guest, 'pidof mihomo; pidof magitrickled')
        rows = pids.splitlines()
        if code == 0 and len(rows) == 2 and all(r.strip().isdigit() for r in rows):
            break
        if time.monotonic() >= until:
            raise SnapshotBlocked('services_not_running_before_removal')
        time.sleep(3)
    state['pids'] = rows
    code, crontab, errors = probe(guest, 'cat /opt/etc/crontab')
    if code != 0:
        raise SnapshotBlocked('crontab_unreadable')
    state['crontab'] = crontab
    state['foreign_crontab'] = [line for line in crontab.splitlines()
                                if WATCHDOG_CRON not in line and line.strip()]
    # The installer registers the watchdog through a thin wrapper in
    # /opt/etc/cron.5mins (run-parts) and may also have a direct crontab
    # line on legacy layouts. Either is a valid pre-removal scheduler state;
    # no mihomo_watchdog schedule at all means the removal is not exercised.
    code, wrapper, errors = probe(guest, 'cat /opt/etc/cron.5mins/mihomo_watchdog')
    if code != 0 and WATCHDOG_CRON not in crontab:
        raise SnapshotBlocked('watchdog_cron_missing_before_removal')
    state['init_enabled'] = {}
    for pkg in ('mihomo', 'magitrickle'):
        code, count, errors = probe(guest, 'grep -c "^ENABLED=yes$" /opt/etc/init.d/S99' + pkg)
        state['init_enabled'][pkg] = count.strip()
        if code != 0 or count.strip() != '1':
            raise SnapshotBlocked('init_not_enabled_before_removal')
    code, yaml_hashes, errors = probe(guest, digest_command(YAML_PATHS))
    if code != 0:
        raise SnapshotBlocked('yaml_unreadable_before_removal')
    state['yaml'] = dict(line.split(None, 1)[::-1] for line in yaml_hashes.splitlines())
    state['foreign_packages'] = [line for line in installed.splitlines()
                                 if any(line.startswith(p + ' - ') for p in FOREIGN_PACKAGES)]
    if len(state['foreign_packages']) < len(FOREIGN_PACKAGES):
        missing = [p for p in FOREIGN_PACKAGES
                   if not any(line.startswith(p + ' - ') for line in state['foreign_packages'])]
        print('UNINSTALL E2E PRE: foreign packages not in OPKG at snapshot time: ' + ','.join(missing), flush=True)
    code, mounts, errors = probe(guest, 'cat /proc/mounts')
    state['ext4'] = [row for row in mounts.splitlines() if '/opt ext4' in row] if code == 0 else []
    if not state['ext4']:
        raise SnapshotBlocked('opt_not_ext4')
    return state


def wait_watchdog_quiet(guest, budget=40):
    """Start the removal outside an active watchdog run when possible.

    A watchdog that started just before us legitimately holds its per-run
    lock; the uninstaller then waits up to 30 s itself and otherwise aborts
    safely. Waiting here only reduces that race; it never removes a lock.
    """
    until = time.monotonic() + budget
    while time.monotonic() < until:
        code, output, errors = guest.execute('test -d /tmp/mihomo_watchdog.lock.d')
        if code != 0:
            return True
        time.sleep(2)
    return False


def digest_command(paths):
    return 'sha256sum ' + ' '.join(paths)


def yaml_state(guest):
    code, output, errors = guest.execute(digest_command(YAML_PATHS))
    if code != 0:
        return None
    return dict(line.split(None, 1)[::-1] for line in output.splitlines())


def packages_gone(guest):
    installed = guest.command('opkg list-installed')
    if re.search('(?m)^(mihomo|magitrickle) - ', installed):
        return None
    return installed


def check_absent(guest, command):
    code, output, errors = guest.execute(command)
    return code != 0 and not output.strip()


def check_present(guest, command):
    code, output, errors = guest.execute(command)
    return code == 0 and bool(output.strip())


def uninstall_packages(guest, mirror, revision, evidence, context, private, phase_box):
    """Stages A (install), B (single real removal), C (repeat and reboot)."""
    phase_box['name'] = 'KAS_INSTALL'
    evidence.set('KAS_INSTALL', 'NOT_TESTED', 'unattempted')
    try:
        prepare(guest, mirror, revision)
    except Exception:
        evidence.set('KAS_INSTALL', 'BLOCKED', 'verified_tls_source_failed')
        return False
    if not install(guest, mirror, revision, evidence):
        return False

    # --- Stage A: real running installation, recorded honestly -------------
    phase('installed services state snapshot')
    try:
        before = snapshot_state(guest)
    except SnapshotBlocked as blocked:
        evidence.set('KAS_UNINSTALL_PACKAGES', 'BLOCKED', 'native_command_failed')
        print('UNINSTALL E2E: pre-removal prerequisite unavailable: ' + blocked.label, flush=True)
        return False
    except Exception as error:
        evidence.set('KAS_UNINSTALL_PACKAGES', 'BLOCKED', 'native_command_failed')
        print('UNINSTALL E2E: pre-removal state unavailable (%s)' % type(error).__name__, flush=True)
        return False
    print('UNINSTALL E2E PRE: mihomo pid %s, magitrickled pid %s, both init ENABLED=yes'
          % (before['pids'][0].strip(), before['pids'][1].strip()), flush=True)
    print('UNINSTALL E2E PRE: watchdog cron line present, %d foreign Entware packages pinned'
          % len(before['foreign_packages']), flush=True)
    evidence.set('KAS_UNINSTALL_PACKAGES', 'NOT_TESTED', 'unattempted')
    phase_box['name'] = 'KAS_UNINSTALL_PACKAGES'

    # --- Stage B: one real, plain removal -----------------------------------
    quiet = wait_watchdog_quiet(guest)
    if not quiet:
        print('UNINSTALL E2E: watchdog lock still held at start; the uninstaller owns the safe abort', flush=True)
    phase('real sh uninstall-packages.sh')
    code, output, errors = guest.execute('cd /opt/lab && KEENETIC_AUTO_SETUP_REF=' + revision +
                                         ' sh uninstall-packages.sh', timeout=900)
    plain_out = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', output)
    for line in plain_out.splitlines():
        if line.startswith(('[OK]', '[SKIP]', '[REMOVE]', '[SAVED]', '[WARN]', '[ERROR]', '[INFO]', '[PARTIAL]')):
            print('UNINSTALL E2E GUEST: ' + line, flush=True)
    failed = []
    if code != 0 or '[OK] Entware package removal finished' not in plain_out:
        failed.append('exit%d_or_missing_ok_line' % code)

    def require(label, ok):
        if not ok:
            failed.append(label)

    # 1. watchdog cron schedule is gone, wrapper removed, cron service alive
    require('cron_line_removed', check_absent(guest, "grep mihomo_watchdog /opt/etc/crontab"))
    require('cron_wrapper_removed', check_absent(guest, 'cat /opt/etc/cron.5mins/mihomo_watchdog'))
    code, cron_probe, errors = probe(guest, 'test -x /opt/etc/init.d/S10cron')
    require('cron_service_alive', code == 0)
    require('foreign_cron_preserved',
            all(line in guest.command('cat /opt/etc/crontab') for line in before['foreign_crontab']))
    # 2. no watchdog process or active watchdog run is left behind; a shell
    #    script's comm is the interpreter, so ps/args is the honest probe
    require('watchdog_process_gone',
            check_absent(guest, "ps 2>/dev/null | grep -F mihomo_watchdog | grep -vF grep"))
    require('watchdog_run_lock_closed', check_absent(guest, 'test -d /tmp/mihomo_watchdog.lock.d'))
    # 3-6. services stopped through init; no live processes remain
    require('mihomo_process_gone', check_absent(guest, 'pidof mihomo'))
    require('magitrickled_process_gone', check_absent(guest, 'pidof magitrickled'))
    require('mihomo_marker_in_output', '[REMOVE] mihomo' in plain_out and '[OK] mihomo removed through OPKG' in plain_out)
    require('magitrickle_marker_in_output',
            '[REMOVE] magitrickle' in plain_out and '[OK] magitrickle removed through OPKG' in plain_out)
    # 8. real OPKG database no longer lists the packages; binaries are gone
    after_installed = packages_gone(guest)
    require('opkg_db_packages_absent', after_installed is not None)
    require('binaries_removed', check_absent(guest,
        'ls /opt/sbin/mihomo /opt/bin/mihomo /opt/bin/magitrickled 2>/dev/null'))
    # opkg may keep an init script as a conffile: gone OR fully disabled is
    # the honest post-state; any ENABLED=yes remnant would restart the service
    code, init_state, errors = guest.execute(
        'grep -H "^ENABLED=" /opt/etc/init.d/S99mihomo /opt/etc/init.d/S99magitrickle 2>/dev/null')
    init_ok = (code != 0 and not init_state.strip()) or ('ENABLED=no' in init_state and 'ENABLED=yes' not in init_state)
    require('init_gone_or_disabled', init_ok)
    # 9. user YAML preserved (restored by the uninstaller if OPKG dropped it)
    after_yaml = yaml_state(guest)
    require('yaml_preserved', after_yaml is not None and after_yaml == before['yaml'])
    # 10. EXT4 intact, foreign Entware packages intact
    require('ext4_still_mounted',
            bool([row for row in guest.command('cat /proc/mounts').splitlines() if '/opt ext4' in row]))
    require('foreign_packages_preserved',
            all(line in (after_installed or '') for line in before['foreign_packages']))

    if failed:
        evidence.set('KAS_UNINSTALL_PACKAGES', 'FAIL', 'uninstall_packages_failed')
        print('UNINSTALL E2E: failed assertions: ' + ', '.join(failed), flush=True)
        return False
    evidence.set('KAS_UNINSTALL_PACKAGES', 'PASS', 'uninstall_packages_passed')
    print('UNINSTALL E2E: services stopped through native init, packages removed through real OPKG, '
          'YAML preserved, foreign resources intact', flush=True)

    # --- Stage C: second run and a reboot -----------------------------------
    evidence.check('KAS_UNINSTALL_PACKAGES_REPEAT', 'NOT_TESTED', 'unattempted')
    phase('second run on already removed packages')
    code, output, errors = guest.execute('cd /opt/lab && sh uninstall-packages.sh', timeout=300)
    plain_out = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', output)
    for line in plain_out.splitlines():
        if line.startswith(('[OK]', '[SKIP]', '[ERROR]', '[PARTIAL]')):
            print('UNINSTALL E2E GUEST: ' + line, flush=True)
    repeat_ok = (code == 0 and '[SKIP] mihomo is not installed' in plain_out
                 and '[SKIP] magitrickle is not installed' in plain_out
                 and '[OK] Entware package removal finished' in plain_out
                 and yaml_state(guest) == before['yaml']
                 and packages_gone(guest) is not None)
    evidence.check('KAS_UNINSTALL_PACKAGES_REPEAT', 'PASS' if repeat_ok else 'FAIL',
                   'native_command_verified' if repeat_ok else 'uninstall_packages_failed')
    if not repeat_ok:
        return False
    print('UNINSTALL E2E: second run skipped absent packages and changed nothing', flush=True)

    evidence.check('KAS_UNINSTALL_PACKAGES_REBOOT', 'NOT_TESTED', 'unattempted')
    phase('reboot; removed services must not return')
    guest.command('/bin/ndmc -c "system configuration save"; sync')
    guest.close()
    send_monitor(context['monitor'], 'system_reset')
    time.sleep(5)
    guest.connect(300)
    boot_ok = (check_absent(guest, 'pidof mihomo')
               and check_absent(guest, 'pidof magitrickled')
               and check_absent(guest, "ps 2>/dev/null | grep -F mihomo_watchdog | grep -vF grep")
               and check_absent(guest, 'test -d /tmp/mihomo_watchdog.lock.d')
               and check_absent(guest, "grep mihomo_watchdog /opt/etc/crontab")
               and check_absent(guest, 'cat /opt/etc/cron.5mins/mihomo_watchdog')
               and packages_gone(guest) is not None
               and yaml_state(guest) == before['yaml']
               and all(line in guest.command('opkg list-installed') for line in before['foreign_packages'])
               and bool([row for row in guest.command('cat /proc/mounts').splitlines() if '/opt ext4' in row]))
    evidence.check('KAS_UNINSTALL_PACKAGES_REBOOT', 'PASS' if boot_ok else 'FAIL',
                   'native_command_verified' if boot_ok else 'uninstall_packages_failed')
    if boot_ok:
        print('UNINSTALL E2E: after reboot no removed service returned; EXT4, YAML and foreign packages intact', flush=True)
    return boot_ok

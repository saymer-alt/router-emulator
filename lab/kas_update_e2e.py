"""Native kas-update --apply transaction E2E (FIELD Stage 2B).

Exercises the real unified updater front end against the real transactional
watchdog updater inside the isolated Ported/Entware guest. Every scenario
uses the same scripts and transports an operator uses: the guest downloads
kas-update.sh itself over the verified TLS mirror, and --apply delegates to
the committed update-watchdog.sh. Nothing is mocked: replacement, atomic
rename, cron normalization and refusals run for real on the guest EXT4.

Machine-checked evidence only: guest exit codes, SHA-256 digests, ls -ldn
permission strings, state-line identity and service counts. Guest text never
enters the public evidence; expected diagnostic substrings are asserted but
never published.

Honest limits: TERM/SIGKILL or power-loss between the binary commit and the
end of the transaction is not exercised here. The ordinary watchdog updater
commits each managed file individually and has no post-commit rollback; that
contract is covered by the host regression suite, and this stage verifies
the post-commit failure case with an injected unreadable crontab instead.
A killed-updater rollback check would require a new fixture mechanism and is
reported through the same NOT_TESTED honesty rule when attempted.
"""
import hashlib
import re
import time

from lab.kas_lifecycle import install, plain, prepare, put

OLD_RELEASE_REF = 'v1.8.6'
OLD_BODY_FIXTURE = 'kas-update-v186-watchdog.body'
OLD_BODY_SHA256 = '63d99cf5197c637ac7def84ce512acbb6768dda3bee7693976ef8a4d901c9748'

CANONICAL = '/opt/bin/mihomo_watchdog.sh'
WRAPPER = '/opt/etc/cron.5mins/mihomo_watchdog'
CRONTAB = '/opt/etc/crontab'
CONFIG = '/opt/etc/mihomo/config.yaml'
LEDGER = '/opt/etc/keenetic-auto-setup/ownership.v1'
KAS_RUN_DIR = '/opt/lab/kas-run'
OLD_BODY_LAB = '/opt/lab/kas-update-old-body'
EXECUTABLE_PERMS = '-rwxr-xr-x'
NETFAIL_REF = 'lab-nonexistent-ref-xyz'

# Every scenario records its observed guest exit codes here; the aggregate
# exit-code check compares this table against the expected contract.
EXPECTED_EXIT_CODES = {
    'release_upgrade_check': 1,
    'release_upgrade_apply': 0,
    'noop_apply': 0,
    'idempotent_apply': 0,
    'netfail_check': 2,
    'netfail_apply': 1,
    'modified_check': 1,
    'modified_apply': 1,
    'foreign_check': 2,
    'foreign_apply': 1,
    'commit_failure_check': 2,
    'commit_failure_frontend_apply': 1,
    'commit_failure_updater': 1,
    'commit_recovery_apply': 0,
    'busy_apply': 1,
}


def phase(marker):
    # Public fixed-format progress marker; guest text stays private.
    print('KAS UPDATE E2E PHASE: ' + marker, flush=True)


def probe(guest, command, timeout=60):
    """One transport retry; the command's own exit code is returned as is."""
    try:
        return guest.execute(command, timeout=timeout)
    except Exception:
        time.sleep(2)
        return guest.execute(command, timeout=timeout)


def fixture_old_body():
    """Pinned historical release body served to the guest for the upgrade
    scenario. The digest pin rejects any fixture drift unconditionally."""
    from pathlib import Path
    data = Path(__file__).with_name('fixtures').joinpath(OLD_BODY_FIXTURE).read_bytes()
    if hashlib.sha256(data).hexdigest() != OLD_BODY_SHA256:
        raise ValueError('pinned v1.8.6 watchdog fixture digest mismatch')
    return data


def inject_release_routes(mirror):
    """Serve the pinned historical release body at its real
    raw.githubusercontent.com path so the guest downloads it through the same
    verified TLS transport as the channel files. Only the registered path is
    served; no new domain is opened."""
    body = fixture_old_body()
    base = '/saymer-alt/keenetic-auto-setup/' + OLD_RELEASE_REF
    mirror.routes[('https', 'raw.githubusercontent.com', base + '/mihomo-watchdog.sh')] = body


def channel_url(revision, path):
    return 'https://raw.githubusercontent.com/saymer-alt/keenetic-auto-setup/' + revision + '/' + path


def guest_sha256(guest, path):
    code, out, _ = probe(guest, 'sha256sum ' + path)
    if code != 0:
        return ''
    fields = out.split()
    return fields[0] if fields and re.fullmatch(r'[0-9a-f]{64}', fields[0]) else ''


def guest_state_line(guest, path):
    """ls -lidn string: identity, mode and timestamps in one comparable line."""
    code, out, _ = probe(guest, 'ls -lidn ' + path)
    return out if code == 0 else ''


def guest_perm_string(guest, path):
    code, out, _ = probe(guest, 'ls -ldn ' + path)
    if code != 0 or not out.split():
        return ''
    return out.split()[0]


def snapshot_inert_state(guest):
    """Read-only record of everything kas-update must never change. Process
    identity is compared by count (PIDs legitimately differ across restarts);
    content by digest."""
    state = {}
    state['config_md5'] = guest.command('md5sum ' + CONFIG).split()[0]
    code, installed, _ = probe(guest, 'opkg list-installed')
    state['installed'] = installed
    state['crontab'] = guest.command('cat ' + CRONTAB)
    state['service_count'] = guest.command('pidof mihomo | wc -w; pidof magitrickled | wc -w')
    code, rules, _ = probe(guest, 'iptables-save | grep -c bypass_wa')
    state['bypass_rules'] = rules if code == 0 else 'unavailable'
    state['ledger'] = guest_sha256(guest, LEDGER)
    state['wrapper_sha'] = guest_sha256(guest, WRAPPER)
    return state


def inert_state_changes(before, after):
    """Fixed public labels naming what changed; empty when nothing did."""
    changes = []
    if before['config_md5'] != after['config_md5']:
        changes.append('mihomo_config_changed')
    if before['installed'] != after['installed']:
        changes.append('magi_config_changed')
    if before['crontab'] != after['crontab']:
        changes.append('crontab_changed')
    if before['service_count'] != after['service_count']:
        changes.append('native_services_duplicate')
    if before['bypass_rules'] != after['bypass_rules']:
        changes.append('netfilter_changed')
    if before['ledger'] != after['ledger']:
        changes.append('ledger_changed')
    if before['wrapper_sha'] != after['wrapper_sha']:
        changes.append('watchdog_changed')
    return changes


class ScenarioBlocked(RuntimeError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


class KasUpdateScenario:
    """Runs one --check/--apply scenario and records machine evidence."""

    def __init__(self, guest, mirror, revision):
        self.guest = guest
        self.mirror = mirror
        self.revision = revision
        self.script = KAS_RUN_DIR + '/kas-update.sh'

    def deliver_script(self):
        """The guest downloads kas-update.sh itself over verified TLS, like a
        real operator; the digest must equal the pinned kas_ref blob."""
        url = channel_url(self.revision, 'kas-update.sh')
        code, out, _ = probe(self.guest,
            'mkdir -p ' + KAS_RUN_DIR + ' && curl -fSs --max-time 20 ' + url
            + ' -o ' + self.script + ' && sha256sum ' + self.script, timeout=120)
        if code != 0:
            raise ScenarioBlocked('kas_update_tls_delivery_failed')
        digest = out.split()[0] if out.split() else ''
        expected = hashlib.sha256(self.mirror.sources['kas-update.sh']).hexdigest()
        if digest != expected:
            raise ScenarioBlocked('kas_update_tls_delivery_failed')

    def run(self, args, timeout=300):
        """One real kas-update invocation with the lab channel reference."""
        code, out, _ = probe(self.guest, 'KEENETIC_AUTO_SETUP_REF=' + self.revision
                             + ' sh ' + self.script + ' ' + args, timeout=timeout)
        return code, plain(out)

    def candidate_digest(self):
        return hashlib.sha256(self.mirror.sources['mihomo-watchdog.sh']).hexdigest()


def _scenario(evidence, label, pass_reason, body):
    evidence.check(label, 'NOT_TESTED', 'unattempted')
    try:
        ok = body()
        failure = None
    except ScenarioBlocked as error:
        ok, failure = False, error.reason
    except Exception:
        ok, failure = False, 'native_command_failed'
    if ok:
        evidence.check(label, 'PASS', pass_reason)
    elif failure in ('kas_update_tls_delivery_failed', 'kas_update_fixture_unavailable'):
        evidence.check(label, 'BLOCKED', failure)
    else:
        evidence.check(label, 'FAIL', failure or 'native_command_failed')
    return ok


def seed_old_body(guest):
    """Atomically place the pinned historical release body at the canonical
    path (same-filesystem rename from /opt/tmp), like an old installation."""
    probe(guest, 'cat ' + OLD_BODY_LAB + ' > /opt/tmp/kas-update-seed && chmod 755 /opt/tmp/kas-update-seed')
    guest.command('mv -f /opt/tmp/kas-update-seed ' + CANONICAL)
    return guest_sha256(guest, CANONICAL) == OLD_BODY_SHA256


def kas_update(guest, mirror, revision, evidence, context, private, phase_box):
    """Stage entry: real install at the pinned revision, then the transaction
    scenarios. Returns True when every attempted check PASSed."""
    phase_box['name'] = 'KAS_INSTALL'
    evidence.set('KAS_INSTALL', 'NOT_TESTED', 'unattempted')
    try:
        prepare(guest, mirror, revision)
    except Exception:
        evidence.set('KAS_INSTALL', 'BLOCKED', 'verified_tls_source_failed')
        return False
    if not install(guest, mirror, revision, evidence):
        return False

    phase_box['name'] = 'KAS_UPDATE_E2E'
    evidence.set('KAS_UPDATE_E2E', 'NOT_TESTED', 'unattempted')
    scenario = KasUpdateScenario(guest, mirror, revision)
    codes = {}
    ok = True

    # Stage prerequisite: the guest delivers the updater front end itself
    # over verified TLS, and the pinned historical body enters the lab.
    try:
        scenario.deliver_script()
        put(guest, OLD_BODY_LAB, fixture_old_body(), '700')
    except ScenarioBlocked as error:
        evidence.check('KAS_UPDATE_RELEASE_UPGRADE', 'BLOCKED', error.reason)
        evidence.set('KAS_UPDATE_E2E', 'BLOCKED', 'kas_update_tls_delivery_failed')
        return False
    except Exception:
        evidence.check('KAS_UPDATE_RELEASE_UPGRADE', 'BLOCKED', 'kas_update_fixture_unavailable')
        evidence.set('KAS_UPDATE_E2E', 'BLOCKED', 'kas_update_fixture_unavailable')
        return False

    # Baseline: the real installer left the current watchdog in place.
    candidate = scenario.candidate_digest()
    if guest_sha256(guest, CANONICAL) != candidate:
        evidence.check('KAS_UPDATE_RELEASE_UPGRADE', 'FAIL', 'kas_update_precondition_missing')
        evidence.set('KAS_UPDATE_E2E', 'FAIL', 'kas_update_stage_failed')
        return False
    before = snapshot_inert_state(guest)

    # 1. Historical release body -> current channel body (the real upgrade).
    def release_upgrade():
        # The route delivery check: the guest fetches the historical body
        # through the same verified TLS transport as the channel files.
        url = channel_url(OLD_RELEASE_REF, 'mihomo-watchdog.sh')
        code, out, _ = probe(guest, 'curl -fSs --max-time 20 ' + url
                             + ' | sha256sum', timeout=120)
        if code != 0 or out.split()[:1] != [OLD_BODY_SHA256]:
            return False
        if not seed_old_body(guest):
            return False
        crontab_before = guest.command('cat ' + CRONTAB)
        code, out = scenario.run('--check')
        codes['release_upgrade_check'] = code
        if code != 1 or 'update available' not in out \
                or 'recognized managed release body' not in out:
            return False
        code, out = scenario.run('--apply --yes')
        codes['release_upgrade_apply'] = code
        if code != 0 or 'apply verified' not in out:
            return False
        return guest_sha256(guest, CANONICAL) == candidate \
            and guest_perm_string(guest, CANONICAL) == EXECUTABLE_PERMS \
            and guest.command('cat ' + CRONTAB) == crontab_before \
            and guest_sha256(guest, WRAPPER) == before['wrapper_sha']
    ok = _scenario(evidence, 'KAS_UPDATE_RELEASE_UPGRADE', 'kas_update_release_upgrade_passed',
                   release_upgrade) and ok

    # 2. Current body -> declared no-op without any rewrite.
    def noop():
        identity = guest_state_line(guest, CANONICAL)
        code, out = scenario.run('--apply --yes')
        codes['noop_apply'] = code
        return code == 0 and 'nothing to apply' in out \
            and guest_state_line(guest, CANONICAL) == identity \
            and guest_sha256(guest, CANONICAL) == candidate
    ok = _scenario(evidence, 'KAS_UPDATE_NOOP', 'kas_update_noop_passed', noop) and ok

    # 3. Idempotent repeat: same outcome, no second updater invocation.
    def idempotent():
        identity = guest_state_line(guest, CANONICAL)
        code, out = scenario.run('--apply --yes')
        codes['idempotent_apply'] = code
        return code == 0 and 'nothing to apply' in out \
            and 'Downloading watchdog' not in out \
            and guest_state_line(guest, CANONICAL) == identity
    ok = _scenario(evidence, 'KAS_UPDATE_IDEMPOTENT', 'kas_update_idempotent_passed',
                   idempotent) and ok

    # 4. Unresolvable channel -> refusal, installed files byte-identical.
    def netfail():
        code, out, _ = probe(guest, 'KEENETIC_AUTO_SETUP_REF=' + NETFAIL_REF
                             + ' sh ' + scenario.script + ' --check', timeout=300)
        codes['netfail_check'] = code
        code2, out2, _ = probe(guest, 'KEENETIC_AUTO_SETUP_REF=' + NETFAIL_REF
                               + ' sh ' + scenario.script + ' --apply --yes', timeout=300)
        codes['netfail_apply'] = code2
        return code == 2 and 'UNKNOWN' in out \
            and code2 == 1 \
            and guest_sha256(guest, CANONICAL) == candidate \
            and guest_sha256(guest, WRAPPER) == before['wrapper_sha']
    ok = _scenario(evidence, 'KAS_UPDATE_NETFAIL_REFUSAL', 'kas_update_netfail_refused',
                   netfail) and ok

    # 5. Locally modified watchdog (marker kept) -> explicit approval gate;
    #    the modified body is preserved, then restored for later scenarios.
    def modified():
        probe(guest, 'cp -p ' + CANONICAL + ' /opt/tmp/kas-update-restore-body')
        guest.command('printf "\\n# field modification\\n" >> ' + CANONICAL)
        modified_digest = guest_sha256(guest, CANONICAL)
        code, out = scenario.run('--check')
        codes['modified_check'] = code
        refused = code == 1 and 'UNRECOGNIZED' in out
        code2, out2 = scenario.run('--apply --yes')
        codes['modified_apply'] = code2
        preserved = guest_sha256(guest, CANONICAL) == modified_digest
        guest.command('cat /opt/tmp/kas-update-restore-body > ' + CANONICAL
                      + ' && chmod 755 ' + CANONICAL)
        restored = guest_sha256(guest, CANONICAL) == candidate
        return refused and code2 == 1 and 'not a known managed release' in out2 \
            and preserved and restored
    ok = _scenario(evidence, 'KAS_UPDATE_MODIFIED_REFUSAL', 'kas_update_modified_refused',
                   modified) and ok

    # 6. Unknown full watchdog at the cron path -> REVIEW, nothing rewritten.
    def foreign_cron():
        crontab_before = guest.command('cat ' + CRONTAB)
        probe(guest, 'cp -p ' + WRAPPER + ' /opt/tmp/wrapper-restore')
        guest.command('printf "#!/bin/sh\\n# MIHOMO WATCHDOG SCRIPT\\n'
                      + '# lab unknown fixture body\\nexit 0\\n" > ' + WRAPPER
                      + ' && chmod 755 ' + WRAPPER)
        foreign_digest = guest_sha256(guest, WRAPPER)
        code, out = scenario.run('--check')
        codes['foreign_check'] = code
        code2, out2 = scenario.run('--apply --yes')
        codes['foreign_apply'] = code2
        preserved = guest_sha256(guest, WRAPPER) == foreign_digest
        crontab_kept = guest.command('cat ' + CRONTAB) == crontab_before
        guest.command('cat /opt/tmp/wrapper-restore > ' + WRAPPER
                      + ' && chmod 755 ' + WRAPPER)
        restored = guest_sha256(guest, WRAPPER) == before['wrapper_sha']
        return code == 2 and 'REVIEW REQUIRED' in out \
            and code2 == 1 and 'REVIEW REQUIRED' in out2 \
            and preserved and crontab_kept and restored
    ok = _scenario(evidence, 'KAS_UPDATE_FOREIGN_CRON_REVIEW', 'kas_update_foreign_cron_reviewed',
                   foreign_cron) and ok

    # 7. Unreadable crontab: the front end refuses before any transaction
    #    (route UNKNOWN -> REVIEW), and the delegated transactional updater,
    #    driven directly under the same fault, commits the binary and then
    #    fails honestly at cron normalization with the old file already
    #    replaced by its own atomic rename. Recovery completes via kas-update.
    def commit_failure():
        if not seed_old_body(guest):
            return False
        guest.command('mv ' + CRONTAB + ' /opt/lab/crontab-saved && mkdir ' + CRONTAB)
        code, out = scenario.run('--check')
        codes['commit_failure_check'] = code
        code2, out2 = scenario.run('--apply --yes')
        codes['commit_failure_frontend_apply'] = code2
        untouched = guest_sha256(guest, CANONICAL) == OLD_BODY_SHA256
        # The real delegated updater under the same fault (no mocks).
        code3, out3, _ = probe(guest, 'KEENETIC_AUTO_SETUP_REF=' + revision
                               + ' sh /opt/lab/update-watchdog.sh', timeout=300)
        codes['commit_failure_updater'] = code3
        committed = guest_sha256(guest, CANONICAL) == candidate
        wrapper_kept = guest_perm_string(guest, WRAPPER) == EXECUTABLE_PERMS
        guest.command('rmdir ' + CRONTAB + ' && mv /opt/lab/crontab-saved ' + CRONTAB)
        code4, out4 = scenario.run('--apply --yes')
        codes['commit_recovery_apply'] = code4
        return code == 2 and 'REVIEW REQUIRED' in out \
            and code2 == 1 and untouched \
            and code3 == 1 \
            and committed and wrapper_kept \
            and code4 == 0 and 'nothing to apply' in out4 \
            and guest.command('cat ' + CRONTAB) == before['crontab']
    ok = _scenario(evidence, 'KAS_UPDATE_COMMIT_FAILURE', 'kas_update_commit_failure_verified',
                   commit_failure) and ok

    # 8. Busy lifecycle lock -> refusal without any state change.
    def busy():
        put(guest, '/opt/lab/lock-holder.sh',
            b'#!/bin/sh\nmkdir -p /tmp/mihomo-lifecycle.lock.d\n'
            b'data=$(cat "/proc/$$/stat")\ntail=${data##*) }\n'
            b'start=$(printf \'%s\\n\' "$tail" | awk \'NF >= 20 && $20 ~ /^[0-9]+$/ '
            b'{ print $20; ok=1 } END { if (!ok) exit 1 }\') || exit 3\n'
            b'printf \'%s %s\\n\' "$$" "$start" > /tmp/mihomo-lifecycle.lock.d/owner\n'
            b'sleep 180\n', '700')
        holder = guest.command('nohup sh /opt/lab/lock-holder.sh >/dev/null 2>&1 & echo $!')
        holder_pid = holder.strip().splitlines()[-1].strip()
        try:
            code, out = scenario.run('--apply --yes')
            codes['busy_apply'] = code
            return code == 1 and 'busy' in out \
                and guest_sha256(guest, CANONICAL) == candidate
        finally:
            probe(guest, 'kill ' + holder_pid + ' 2>/dev/null; true')
    ok = _scenario(evidence, 'KAS_UPDATE_BUSY_REFUSAL', 'kas_update_busy_refused', busy) and ok

    # 9. Nothing outside the watchdog pair changed during the whole stage.
    def inertness():
        after = snapshot_inert_state(guest)
        return not inert_state_changes(before, after)
    ok = _scenario(evidence, 'KAS_UPDATE_INERTNESS', 'kas_update_inertness_passed',
                   inertness) and ok

    # 10. Observed guest exit codes match the published contract.
    def exitcodes():
        return codes == EXPECTED_EXIT_CODES
    ok = _scenario(evidence, 'KAS_UPDATE_EXITCODES', 'kas_update_exitcodes_passed',
                   exitcodes) and ok

    evidence.set('KAS_UPDATE_E2E', 'PASS' if ok else 'FAIL',
                 'kas_update_stage_passed' if ok else 'kas_update_stage_failed')
    return ok

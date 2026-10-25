import hashlib
import json
from pathlib import Path
import unittest
from lab.kas_lifecycle import (ContractFailure, block, changed_reason,
                              doctor, netfilter_settled, plain, wait_netfilter_settled)
from lab.gates import Evidence
import contextlib
import io
import tempfile
import os
import subprocess
import tarfile
from types import SimpleNamespace
from unittest.mock import patch


class LifecycleDataTests(unittest.TestCase):
    def test_lab_origin_needs_empty_guest_and_package_payload(self):
        from lab.kas_lifecycle import lab_origin
        content = io.BytesIO()
        with tarfile.open(fileobj=content, mode='w:gz') as files:
            for name in ('opt/bin/mihomo', 'opt/bin/magitrickled',
                         'opt/etc/init.d/S99mihomo', 'opt/etc/init.d/S99magitrickle'):
                data = name.encode()
                member = tarfile.TarInfo('./' + name)
                member.size = len(data)
                files.addfile(member, io.BytesIO(data))
        package = io.BytesIO()
        with tarfile.open(fileobj=package, mode='w:gz') as files:
            member = tarfile.TarInfo('./data.tar.gz')
            member.size = len(content.getvalue())
            files.addfile(member, io.BytesIO(content.getvalue()))
        mirror = SimpleNamespace(routes={('https', 'test', '/mihomo_fixture.ipk'): package.getvalue()})
        class Guest:
            code = 0
            def execute(self, *args, **kwargs):
                return self.code, '', ''
        guest = Guest()
        self.assertEqual(set(lab_origin(guest, mirror)), {'mihomo', 'magitrickled'})
        guest.code = 3
        with self.assertRaises(ContractFailure):
            lab_origin(guest, mirror)

    def test_reinstall_warning_never_reports_doctor_pass(self):
        import lab.kas_lifecycle as lifecycle

        class Guest:
            removes = 0

            def command(self, *args, **kwargs):
                return ''

            def execute(self, command, **kwargs):
                if 'mihomo-doctor.sh' in command:
                    return 1, ('[WARN] Watchdog log is empty - the watchdog has not logged anything yet\n'
                               'OK: 35\nWARN: 1\nFAIL: 0\n[WARN] Overall status: WARN'), ''
                if command.endswith('--create'):
                    return 0, 'snapshot created: /opt/kas-uninstall-backups/test', ''
                if command.endswith('--remove'):
                    self.removes += 1
                    if self.removes == 1:
                        return 3, '', 'live service ownership unproven'
                    return 0, ('[PASS] KAS removal transaction complete' if self.removes == 2
                               else 'already fully removed'), ''
                return 0, '', ''

        evidence = Evidence()
        stream = io.StringIO()
        mirror = SimpleNamespace(sources={'uninstall.sh': b'changed candidate'})
        with patch.object(lifecycle, 'contract', return_value={}), \
                patch.object(lifecycle, 'removal_teardown_clean', return_value=True), \
                patch.object(lifecycle, 'install', return_value=True), \
                patch.object(lifecycle, 'lab_identities', return_value={}), \
                patch.object(lifecycle, 'lab_stop_script', return_value='fixture manual stop'), \
                patch.object(lifecycle, 'wan_guard', return_value=True), \
                contextlib.redirect_stdout(stream):
            result = lifecycle.uninstall(Guest(), mirror, evidence, None, 'a' * 40)
        self.assertFalse(result)
        self.assertEqual(evidence.gates['KAS_UNINSTALL']['status'], 'BLOCKED')
        self.assertIn('KAS DOCTOR WARNING: Watchdog log is empty', stream.getvalue())
        self.assertIn('Doctor BLOCKED', stream.getvalue())
        self.assertNotIn('Doctor passed', stream.getvalue())
        self.assertNotIn('unclassified_warning', stream.getvalue())

    def test_lab_identity_needs_origin_and_all_runtime_fields(self):
        from lab.kas_lifecycle import lab_identities
        rows = ['123', '123 (mihomo) S ' + '0 ' * 18 + '456', '/opt/bin/mihomo',
                'mihomo|-d|/opt/etc/mihomo|', 'Uid: 0 0 0 0',
                'a' * 64 + '  /proc/123/exe', 'a' * 64 + '  /opt/bin/mihomo',
                'b' * 64 + '  /opt/etc/init.d/S99mihomo']
        class Guest:
            def execute(self, *args, **kwargs):
                return 0, '\n'.join(self.rows), ''
        guest = Guest()
        origin = {'mihomo': {('a' * 64, 'b' * 64)}}
        guest.rows = rows
        self.assertEqual(lab_identities(guest, origin)['mihomo'][:2], ('123', '456'))
        with self.assertRaises(ContractFailure):
            lab_identities(guest, None)
        for field, value in ((0, '123 124'), (2, '/foreign/mihomo'), (3, 'mihomo|--foreign|'),
                             (4, 'Uid: 0 1000 0 0'), (5, 'c' * 64 + '  exe')):
            guest.rows = list(rows)
            guest.rows[field] = value
            with self.assertRaises(ContractFailure):
                lab_identities(guest, origin)

    def test_manual_stop_rechecks_both_before_signals_under_lock(self):
        from lab.kas_lifecycle import lab_stop_script
        # Shell fixtures only. The actual watchdog/lock race is independently
        # exercised by KAS lifecycle-regression on dash and BusyBox ash.
        source = b'''# BEGIN MIHOMO LIFECYCLE LOCK
ml_lifecycle_acquire() { touch /opt/held; }
ml_lifecycle_release() { rm -f /opt/held; }
ml_starttime() { cat /proc/$1/start; }
# END MIHOMO LIFECYCLE LOCK
txn_step_services_done() { [ ! -d /proc/101 ] && [ ! -d /proc/102 ]; }
txn_step_services() { txn_step_services_done || fail; }
# Refuse unproven live processes
'''
        for shell in (['dash'], ['busybox', 'ash']):
            for drift in (False, True):
                with tempfile.TemporaryDirectory() as temp:
                    root = Path(temp)
                    (root / 'opt/etc/init.d').mkdir(parents=True)
                    (root / 'opt/bin').mkdir()
                    (root / 'opt/var/log').mkdir(parents=True)
                    (root / 'opt/var/log/mihomo_watchdog.log').touch()
                    watchdog = root / 'opt/bin/mihomo_watchdog.sh'
                    watchdog.write_text('[ -e "' + temp + '/opt/held" ] || exit 1\n'
                                        'echo "maintenance in progress - checks skipped" >> "'
                                        + temp + '/opt/var/log/mihomo_watchdog.log"\n')
                    identities = {}
                    for pid, name in (('101', 'mihomo'), ('102', 'magitrickled')):
                        proc = root / 'proc' / pid
                        proc.mkdir(parents=True)
                        binary = root / 'opt/bin' / name
                        binary.write_bytes(b'fixture binary')
                        os.link(binary, proc / 'exe')
                        init = root / 'opt/etc/init.d' / ('S99mihomo' if name == 'mihomo' else 'S99magitrickle')
                        init.write_bytes(b'fixture init')
                        args = name + '|'
                        (proc / 'cmdline').write_bytes(args.replace('|', '\0').encode())
                        (proc / 'status').write_text('Uid: 0 0 0 0\n')
                        (proc / 'start').write_text('999' if drift and pid == '102' else '456')
                        identities[name] = (pid, '456', hashlib.sha256(binary.read_bytes()).hexdigest(),
                                            hashlib.sha256(init.read_bytes()).hexdigest(), args)
                    script = lab_stop_script(SimpleNamespace(sources={'uninstall-transaction.sh': source}), identities)
                    script = script.replace('/proc/', temp + '/proc/').replace('/opt/', temp + '/opt/')
                    fake_kill = ('kill() { echo "$*" >> "' + temp + '/signals"; '
                                 'rm -f "' + temp + '/proc/$2/"*; rmdir "' + temp + '/proc/$2"; }\n')
                    script = script.replace('verify() {', fake_kill + 'verify() {')
                    result = subprocess.run(shell, input=script, text=True, capture_output=True, timeout=10)
                    self.assertEqual(result.returncode, 3 if drift else 0, result.stderr)
                    self.assertFalse((root / 'opt/held').exists())
                    if drift:
                        self.assertFalse((root / 'signals').exists())
                    else:
                        self.assertEqual((root / 'signals').read_text().splitlines(), ['-TERM 101', '-TERM 102'])

    def test_doctor_requires_full_pass(self):
        for code, warnings, failures, overall, expected in (
                (0, 0, 0, 'OK', 'PASS'), (1, 1, 0, 'WARN', 'BLOCKED'),
                (2, 0, 1, 'FAIL', 'FAIL'), (1, 0, 0, 'missing', 'FAIL'),
                (0, 1, 0, 'OK', 'FAIL')):
            class Guest:
                def execute(self, *args, **kwargs):
                    return code, ('[WARN] private-router-data\nOK: 35\nWARN: %d\nFAIL: %d\nOverall status: %s'
                                  % (warnings, failures, overall)), ''
            evidence = Evidence()
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                result = doctor(Guest(), evidence, 'a' * 40)
            self.assertEqual(result, expected == 'PASS')
            self.assertEqual(evidence.gates['KAS_DOCTOR']['status'], expected)
            self.assertNotIn('private-router-data', stream.getvalue())
            # Persisting a clean PASS beside a foreign BLOCKED cannot turn green.
            evidence.check('KAS_FOREIGN_DOCTOR', expected, evidence.gates['KAS_DOCTOR']['reason'])
            for gate in evidence.gates:
                evidence.set(gate, 'PASS', 'native_command_verified')
            with tempfile.TemporaryDirectory() as temp:
                evidence.write(temp)
                self.assertEqual(json.loads((Path(temp) / 'gates.json').read_text())['outcome'], expected)

    def test_proxy_and_policy_blocks_never_share_directives(self):
        config = 'interface Proxy0\n    proxy upstream 127.0.0.1 1081\n!\ninterface Proxy1\n    proxy upstream 127.0.0.1 7890\n!\nip policy bypass_wa\n    permit global Proxy1\n!\n'
        self.assertNotIn('7890', block(config, 'interface Proxy0'))
        self.assertNotIn('1081', block(config, 'interface Proxy1'))
        self.assertNotIn('Proxy0', block(config, 'ip policy bypass_wa'))
        with self.assertRaises(RuntimeError):
            block(config, 'interface Proxy2')

    def test_dependency_snapshot_is_canonical_and_digest_pinned(self):
        root = Path(__file__).parent
        lock = json.loads((root / 'kas-assets.lock.json').read_text())
        for record in lock['assets']:
            if 'fixture' in record:
                data = (root / record['fixture']).read_bytes()
                self.assertNotIn(b'\r\n', data)
                self.assertEqual(hashlib.sha256(data).hexdigest(), record['sha256'])

    def test_ansi_removed_without_inventing_doctor_success(self):
        self.assertEqual(plain('\x1b[31mFAIL: 2\x1b[0m'), 'FAIL: 2')

    def test_contract_failures_carry_only_public_labels(self):
        with self.assertRaises(ValueError):
            ContractFailure('private UART detail')
        self.assertEqual(ContractFailure('foreign_proxy_changed').reason, 'foreign_proxy_changed')

    def test_changed_reason_localizes_the_changed_resource(self):
        state = {'files': {'/opt/etc/crontab': 'a', '/opt/etc/mihomo/config.yaml': 'a'},
                 'proxy': 'p', 'policy': 'l', 'ledger': 'd', 'netfilter': 'n', 'prerouting': 'r'}
        self.assertEqual(changed_reason(state, dict(state, files=dict(state['files'], **{'/opt/etc/crontab': 'b'}))), 'crontab_changed')
        self.assertEqual(changed_reason(state, dict(state, proxy='x')), 'proxy_changed')
        self.assertEqual(changed_reason(state, dict(state, prerouting='m')), 'netfilter_changed')
        self.assertEqual(changed_reason(state, state), 'idempotence_failed')

    def test_netfilter_settle_assertion_keeps_full_rule_contract(self):
        rules = ('-P _CUST_BYPASS_WA_ ACCEPT\n'
                 '-A _CUST_BYPASS_WA_ -j MARK --set-xmark 0x2000/0xffffffff\n'
                 '-A _CUST_BYPASS_WA_ -j CONNMARK --save-mark --mask 0xffffffff\n'
                 '-A _CUST_BYPASS_WA_ -j RETURN\n')
        link = ['-A PREROUTING -m mark --mark 0x0/0xffffffff -j _CUST_BYPASS_WA_']
        self.assertTrue(netfilter_settled(rules, link))
        self.assertFalse(netfilter_settled(rules.replace('-A _CUST_BYPASS_WA_ -j RETURN\n', ''), link))
        self.assertFalse(netfilter_settled(rules, link * 2))
        self.assertFalse(netfilter_settled(rules, ['-A PREROUTING -m mark --mark 0x1/0xffffffff -j _CUST_BYPASS_WA_']))
        self.assertFalse(netfilter_settled('', link))

    def test_settle_loop_retries_failed_probes_within_budget(self):
        import lab.kas_lifecycle as lifecycle

        class FlakyGuest:
            """Two failed probes (xtables lock / channel error), then settled."""

            def __init__(self):
                self.failures = 2

            def command(self, text, timeout=30, input_data=None):
                if self.failures > 0:
                    self.failures -= 1
                    raise RuntimeError('guest probe returned nonzero')
                if text.endswith('_CUST_BYPASS_WA_'):
                    return ('-P _CUST_BYPASS_WA_ ACCEPT\n'
                            '-A _CUST_BYPASS_WA_ -j MARK --set-xmark 0x2000/0xffffffff\n'
                            '-A _CUST_BYPASS_WA_ -j CONNMARK --save-mark --mask 0xffffffff\n'
                            '-A _CUST_BYPASS_WA_ -j RETURN\n')
                return ('-P PREROUTING ACCEPT\n'
                        '-A PREROUTING -m mark --mark 0x0/0xffffffff -j _CUST_BYPASS_WA_\n')

        original_sleep = lifecycle.time.sleep
        lifecycle.time.sleep = lambda seconds: None
        try:
            rules, links = wait_netfilter_settled(FlakyGuest())
        finally:
            lifecycle.time.sleep = original_sleep
        self.assertTrue(netfilter_settled(rules, links))

    def test_settle_loop_fails_closed_when_probes_never_succeed(self):
        import lab.kas_lifecycle as lifecycle

        class DeadGuest:
            def command(self, text, timeout=30, input_data=None):
                raise RuntimeError('guest probe returned nonzero')

        original_sleep = lifecycle.time.sleep
        lifecycle.time.sleep = lambda seconds: None
        try:
            with self.assertRaises(ContractFailure) as raised:
                wait_netfilter_settled(DeadGuest(), budget=0)
        finally:
            lifecycle.time.sleep = original_sleep
        self.assertEqual(raised.exception.reason, 'native_rules_not_ready')

    def test_settle_loop_fails_closed_on_never_settling_content(self):
        import lab.kas_lifecycle as lifecycle

        class HalfReadyGuest:
            def command(self, text, timeout=30, input_data=None):
                if text.endswith('_CUST_BYPASS_WA_'):
                    return '-P _CUST_BYPASS_WA_ ACCEPT\n'  # chain exists, rules missing
                return '-P PREROUTING ACCEPT\n'

        original_sleep = lifecycle.time.sleep
        lifecycle.time.sleep = lambda seconds: None
        try:
            with self.assertRaises(ContractFailure) as raised:
                wait_netfilter_settled(HalfReadyGuest(), budget=0)
        finally:
            lifecycle.time.sleep = original_sleep
        self.assertEqual(raised.exception.reason, 'native_rules_not_ready')

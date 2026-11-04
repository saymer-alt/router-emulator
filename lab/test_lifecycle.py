import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET
from lab.kas_lifecycle import (ContractFailure, block, changed_reason,
                              netfilter_settled, plain, wait_netfilter_settled)


class LifecycleDataTests(unittest.TestCase):
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

    def test_iptables_failure_categories_never_expose_private_stderr(self):
        from lab.kas_lifecycle import classify_iptables_error, read_iptables_diagnostic

        self.assertEqual(classify_iptables_error(1, 'iptables: No chain/target/match by that name'), 'CHAIN_MISSING')
        self.assertEqual(classify_iptables_error(4, 'Another app is currently holding the xtables lock'), 'XTABLES_LOCK')
        self.assertEqual(classify_iptables_error(1, 'Resource temporarily unavailable'), 'XTABLES_LOCK')
        self.assertEqual(classify_iptables_error(1, 'secret-host.example invalid'), 'COMMAND_NONZERO')
        self.assertIsNone(classify_iptables_error(0, ''))

        class Guest:
            def __init__(self, result=None, exception=None):
                self.result, self.exception = result, exception
            def execute(self, command, timeout=30):
                if self.exception:
                    raise self.exception
                return self.result

        self.assertEqual(read_iptables_diagnostic(Guest((1, '', 'No chain/target/match by that name')),
                                                   'iptables -t mangle -S chain'), (None, 'CHAIN_MISSING'))
        self.assertEqual(read_iptables_diagnostic(Guest(exception=TimeoutError('private-host')),
                                                   'iptables -t mangle -S chain'), (None, 'TIMEOUT'))
        self.assertEqual(read_iptables_diagnostic(Guest(exception=EOFError('private-host')),
                                                   'iptables -t mangle -S chain'), (None, 'SSH_ERROR'))

    def test_failed_probe_prints_only_fixed_categories_and_never_stderr(self):
        import contextlib
        import io
        from lab import kas_lifecycle as lifecycle

        secret = 'PRIVATE_ROUTER_123'
        class Guest:
            def execute(self, text, timeout=30):
                return 1, '', 'iptables: No chain/target/match by that name; ' + secret

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            with self.assertRaises(ContractFailure):
                lifecycle.wait_netfilter_settled(Guest(), budget=0)
        log = buffer.getvalue()
        self.assertIn('CHAIN_CHAIN_MISSING:1', log)
        self.assertNotIn(secret, log)
        self.assertNotIn('iptables:', log)

    def test_prerouting_probe_classified_separately_from_chain(self):
        import contextlib
        import io
        from lab import kas_lifecycle as lifecycle

        class Guest:
            def execute(self, text, timeout=30):
                if text.endswith('PREROUTING'):
                    return 1, '', 'Could not get lock /run/xtables.lock'
                return 0, '-P _CUST_BYPASS_WA_ ACCEPT', ''

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            with self.assertRaises(ContractFailure):
                lifecycle.wait_netfilter_settled(Guest(), budget=0)
        self.assertIn('PREROUTING_XTABLES_LOCK:1', buffer.getvalue())

    def test_settle_loop_retries_failed_probes_within_budget(self):
        import lab.kas_lifecycle as lifecycle

        class FlakyGuest:
            """Two failed probes (xtables lock / channel error), then settled."""

            def __init__(self):
                self.failures = 2

            def execute(self, text, timeout=30, input_data=None):
                if self.failures > 0:
                    self.failures -= 1
                    return 1, '', 'Another app is currently holding the xtables lock'
                if text.endswith('_CUST_BYPASS_WA_'):
                    return (0, '-P _CUST_BYPASS_WA_ ACCEPT\n'
                            '-A _CUST_BYPASS_WA_ -j MARK --set-xmark 0x2000/0xffffffff\n'
                            '-A _CUST_BYPASS_WA_ -j CONNMARK --save-mark --mask 0xffffffff\n'
                            '-A _CUST_BYPASS_WA_ -j RETURN\n', '')
                return (0, '-P PREROUTING ACCEPT\n'
                        '-A PREROUTING -m mark --mark 0x0/0xffffffff -j _CUST_BYPASS_WA_\n', '')

        original_sleep = lifecycle.time.sleep
        lifecycle.time.sleep = lambda seconds: None
        try:
            rules, links = wait_netfilter_settled(FlakyGuest())
        finally:
            lifecycle.time.sleep = original_sleep
        self.assertTrue(netfilter_settled(rules, links))

    def test_settle_success_reports_recovered_failure_categories_without_stderr(self):
        import contextlib
        import io
        from lab import kas_lifecycle as lifecycle

        class RecoveredGuest:
            def __init__(self):
                self.attempt = 0

            def execute(self, text, timeout=30):
                if text.endswith('_CUST_BYPASS_WA_'):
                    self.attempt += 1
                    if self.attempt == 1:
                        return (1, '', 'iptables: No chain/target/match by that name; PRIVATE_ROUTER')
                    return (0, '-P _CUST_BYPASS_WA_ ACCEPT\n'
                            '-A _CUST_BYPASS_WA_ -j MARK --set-xmark 0x2000/0xffffffff\n'
                            '-A _CUST_BYPASS_WA_ -j CONNMARK --save-mark\n'
                            '-A _CUST_BYPASS_WA_ -j RETURN\n', '')
                return (0, '-P PREROUTING ACCEPT\n'
                        '-A PREROUTING -m mark --mark 0x0 -j _CUST_BYPASS_WA_\n', '')

        captured = io.StringIO()
        original_sleep = lifecycle.time.sleep
        lifecycle.time.sleep = lambda seconds: None
        try:
            with contextlib.redirect_stdout(captured):
                rules, links = lifecycle.wait_netfilter_settled(RecoveredGuest())
        finally:
            lifecycle.time.sleep = original_sleep
        self.assertTrue(netfilter_settled(rules, links))
        self.assertIn('KAS SETTLE RECOVERED:', captured.getvalue())
        self.assertIn('CHAIN_CHAIN_MISSING:1', captured.getvalue())
        self.assertNotIn('PRIVATE_ROUTER', captured.getvalue())

    def test_settle_clean_success_does_not_emit_failure_record(self):
        import contextlib
        import io
        from lab import kas_lifecycle as lifecycle

        class CleanGuest:
            def execute(self, text, timeout=30):
                if text.endswith('_CUST_BYPASS_WA_'):
                    return (0, '-P _CUST_BYPASS_WA_ ACCEPT\n'
                            '-A _CUST_BYPASS_WA_ -j MARK --set-xmark 0x2000/0xffffffff\n'
                            '-A _CUST_BYPASS_WA_ -j CONNMARK --save-mark\n'
                            '-A _CUST_BYPASS_WA_ -j RETURN\n', '')
                return (0, '-P PREROUTING ACCEPT\n'
                        '-A PREROUTING -m mark --mark 0x0 -j _CUST_BYPASS_WA_\n', '')

        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            rules, links = lifecycle.wait_netfilter_settled(CleanGuest())
        self.assertTrue(netfilter_settled(rules, links))
        self.assertEqual(captured.getvalue(), '')

    def test_settle_loop_fails_closed_when_probes_never_succeed(self):
        import lab.kas_lifecycle as lifecycle

        class DeadGuest:
            def execute(self, text, timeout=30, input_data=None):
                return 1, '', 'iptables: No chain/target/match by that name.'

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
            def execute(self, text, timeout=30, input_data=None):
                if text.endswith('_CUST_BYPASS_WA_'):
                    return 0, '-P _CUST_BYPASS_WA_ ACCEPT\n', ''  # missing rules
                return 0, '-P PREROUTING ACCEPT\n', ''

        original_sleep = lifecycle.time.sleep
        lifecycle.time.sleep = lambda seconds: None
        try:
            with self.assertRaises(ContractFailure) as raised:
                wait_netfilter_settled(HalfReadyGuest(), budget=0)
        finally:
            lifecycle.time.sleep = original_sleep
        self.assertEqual(raised.exception.reason, 'native_rules_not_ready')


class _UninstallGuest:
    """Records phase traffic; per-command scripted returns/raises."""

    def __init__(self, command_plan=None, execute_plan=None):
        self.commands = []
        self.executes = []
        self.command_plan = command_plan or {}
        self.execute_plan = execute_plan or {}

    def command(self, text, timeout=30, input_data=None):
        self.commands.append(text)
        result = self.command_plan.get(text)
        if isinstance(result, Exception):
            raise result
        return result if result is not None else ''

    def execute(self, text, timeout=30, input_data=None, on_stdout=None):
        self.executes.append(text)
        result = self.execute_plan.get(text)
        if isinstance(result, Exception):
            raise result
        return result if result is not None else (0, '', '')


class UninstallPhaseTests(unittest.TestCase):
    """The uninstall phase must distinguish three candidate states honestly:
    absent component -> BLOCKED (not applicable), delivered-but-broken -> FAIL,
    delivered and gated -> existing snapshot/transaction gate semantics."""

    CHECK = 'sh /opt/lab/uninstall.sh --check'
    LOCK = json.loads(Path(__file__).with_name('kas-assets.lock.json').read_text())

    def test_absent_uninstall_sh_is_blocked_not_applicable(self):
        import lab.kas_lifecycle as lifecycle
        from lab.gates import Evidence

        mirror = type('Mirror', (), {'sources': {'install.sh': b'#!/bin/sh\n'}})()
        guest = _UninstallGuest()
        evidence = Evidence()
        for name in evidence.gates:
            evidence.set(name, 'PASS', 'native_command_verified')
        self.assertTrue(lifecycle.uninstall(guest, mirror, evidence, None, 'f' * 40))
        self.assertEqual(guest.commands, [])  # the phase never touched the guest
        self.assertEqual(evidence.gates['KAS_UNINSTALL'],
                         {'status': 'BLOCKED', 'reason': 'uninstall_not_delivered'})
        self.assertEqual(evidence.checks['KAS_UNINSTALL_GUARD'],
                         {'status': 'BLOCKED', 'reason': 'uninstall_not_delivered'})
        with tempfile.TemporaryDirectory() as directory:
            evidence.write(directory)
            report = json.loads((Path(directory) / 'gates.json').read_text())
            self.assertEqual(report['outcome'], 'BLOCKED')  # never a PASS
            root = ET.parse(Path(directory) / 'junit.xml').getroot()
            self.assertEqual(len(root.findall('testcase/failure')), 0)
            # exactly the blocked gate and its guard check, nothing else:
            self.assertEqual(len(root.findall('testcase/skipped')), 2)

    def test_reviewed_but_broken_uninstall_sh_fails_closed(self):
        import lab.kas_lifecycle as lifecycle
        from lab.gates import Evidence
        from unittest import mock

        mirror = type('Mirror', (), {'sources': {'install.sh': b'#!/bin/sh\n',
                                                 'uninstall.sh': b'#!/bin/sh\nexit 1\n'}})()
        guest = _UninstallGuest(command_plan={self.CHECK: RuntimeError('guest probe returned nonzero')})
        evidence = Evidence()
        with mock.patch.object(lifecycle, 'contract', return_value={}), \
             mock.patch.object(lifecycle.hashlib, 'sha256') as sha256:
            sha256.return_value.hexdigest.return_value = self.LOCK['uninstall_no_remove_sha256']
            self.assertFalse(lifecycle.uninstall(guest, mirror, evidence, None, 'f' * 40))
        self.assertEqual(evidence.gates['KAS_UNINSTALL'],
                         {'status': 'FAIL', 'reason': 'execution_failed'})

    def test_unknown_uninstall_never_reaches_guest(self):
        import lab.kas_lifecycle as lifecycle
        from lab.gates import Evidence
        mirror = type('Mirror', (), {'sources': {'install.sh': b'#!/bin/sh\n',
                                                 'uninstall.sh': b'#!/bin/sh\n# synthetic candidate script\n'}})()
        guest = _UninstallGuest()
        evidence = Evidence()
        self.assertFalse(lifecycle.uninstall(guest, mirror, evidence, None, 'f' * 40))
        self.assertEqual((guest.commands, guest.executes), ([], []))
        self.assertEqual(evidence.gates['KAS_UNINSTALL'],
                         {'status': 'BLOCKED', 'reason': 'uninstall_candidate_unreviewed'})

    def test_reviewed_baseline_and_inventory_candidate_preserve_normal_and_foreign(self):
        import lab.kas_lifecycle as lifecycle
        from lab.gates import Evidence
        from unittest import mock

        mirror = type('Mirror', (), {'sources': {'uninstall.sh': b'synthetic fixture'}})()
        guard_commands = ['sh /opt/lab/uninstall.sh --remove', 'sh /opt/lab/uninstall.sh --purge']
        for key in ('uninstall_no_remove_sha256', 'uninstall_inventory_s28_sha256'):
            for foreign in (None, {'proxy': 'fixture', 'cron': 'fixture'}):
                for change_state in (False, True):
                    with self.subTest(key=key, foreign=foreign, change_state=change_state):
                        guest = _UninstallGuest(execute_plan={
                            **{command: (3, '', '[BLOCKED]') for command in guard_commands},
                            'sh /opt/lab/uninstall-snapshot.sh --check': (1, '', ''),
                        })
                        evidence = Evidence()
                        states = [{}, {'changed': True}, {}] if change_state else [{}, {}, {}]
                        with mock.patch.object(lifecycle, 'contract', side_effect=states), \
                             mock.patch.object(lifecycle.hashlib, 'sha256') as sha256:
                            sha256.return_value.hexdigest.return_value = self.LOCK[key]
                            result = lifecycle.uninstall(guest, mirror, evidence, foreign, 'f' * 40)
                        self.assertEqual(result, not change_state)
                        self.assertEqual([command for command in guest.executes if command in guard_commands],
                                         guard_commands[:1] if change_state else guard_commands)


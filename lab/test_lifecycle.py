import hashlib
import json
from pathlib import Path
import unittest
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

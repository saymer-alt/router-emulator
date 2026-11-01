"""Issue #50 host-side contract tests: fresh Entware + surviving legacy Proxy0.

The production functions run against a stubbed guest; every guest-facing
behavior asserted here is exercised natively by the legacy-bridge profile.
No router, firewall, policy or table is touched by these tests.
"""
import hashlib
import unittest
from types import SimpleNamespace

import lab.kas_lifecycle as kl
from lab.gates import Evidence


FIXTURE_BLOCK = (
    'interface Proxy0\n'
    ' description "user-legacy-bridge"\n'
    ' proxy protocol socks5\n'
    ' proxy socks5-udp\n'
    ' proxy upstream 127.0.0.1 7890\n'
    ' up\n'
)

NETFILTER_RULES = (
    '-P _CUST_BYPASS_WA_ ACCEPT\n'
    '-A _CUST_BYPASS_WA_ -j MARK --set-xmark 0xffffaab/0xffffffff\n'
    '-A _CUST_BYPASS_WA_ -j CONNMARK --save-mark --mask 0xffffffff\n'
    '-A _CUST_BYPASS_WA_ -j RETURN\n'
)
NETFILTER_LINK = '-A PREROUTING -m mark --mark 0x0/0xffffffff -j _CUST_BYPASS_WA_'

CLEAN_LEDGER = (
    'KAS_OWNERSHIP_V1\n'
    'owned|package|mihomo\n'
    'owned|package|magitrickle\n'
)


class LegacyBridgeShapeTests(unittest.TestCase):
    def test_functional_signature_with_non_canonical_name_is_accepted(self):
        self.assertTrue(kl.legacy_bridge_shape(FIXTURE_BLOCK))

    def test_canonical_name_is_rejected(self):
        canonical = FIXTURE_BLOCK.replace('user-legacy-bridge', 'mihomo t2s0')
        self.assertFalse(kl.legacy_bridge_shape(canonical))

    def test_missing_udp_support_is_rejected(self):
        self.assertFalse(kl.legacy_bridge_shape(
            FIXTURE_BLOCK.replace(' proxy socks5-udp\n', '\n')))

    def test_foreign_upstream_is_rejected(self):
        self.assertFalse(kl.legacy_bridge_shape(
            FIXTURE_BLOCK.replace('127.0.0.1 7890', '127.0.0.1 7891')))

    def test_explicit_up_is_required(self):
        self.assertFalse(kl.legacy_bridge_shape(
            FIXTURE_BLOCK.replace(' up\n', '\n')))


class LegacyBridgeResourceTests(unittest.TestCase):
    def test_occupied_proxy0_is_refused(self):
        class Busy:
            def execute(self, _command):
                return 0, '', ''

            def command(self, _):
                return 'interface Proxy0\n!'
        with self.assertRaises(RuntimeError):
            kl.legacy_bridge_resources(Busy())

    def test_dirty_opt_is_refused(self):
        class Dirty:
            def execute(self, command):
                if 'test -e /opt/bin/mihomo' in command:
                    return 0, '', ''
                return 1, '', ''
        with self.assertRaises(RuntimeError):
            kl.legacy_bridge_resources(Dirty())


class LegacyBridgeContractTests(unittest.TestCase):
    def _fixture(self):
        return {'iface': 'Proxy0', 'proxy': FIXTURE_BLOCK}

    @staticmethod
    def _config(proxy_block):
        return (proxy_block
                + '!\n'
                + 'ip policy bypass_wa\n'
                + ' permit global Proxy0\n'
                + '!\n'
                + 'ip hotspot\n'
                + ' intercept enable\n'
                + '!\n')

    @staticmethod
    def _mirror():
        return SimpleNamespace(sources={
            'mihomo-watchdog.sh': b'watchdog-bytes',
            '020-bypass-wa.sh': b'hook-bytes'})

    @staticmethod
    def _guest(ledger, config):
        digests = {}
        for path in kl.STABLE_FILES:
            payload = (b'hook-bytes' if path.endswith('020-bypass_wa.sh')
                       else b'watchdog-bytes' if path.endswith('mihomo_watchdog.sh')
                       else b'x')
            digests[path] = hashlib.sha256(payload).hexdigest()

        class Guest:
            def command(self, text):
                if text.startswith('cat /opt/etc/keenetic-auto-setup/ownership.v1'):
                    return ledger
                if text.startswith('ls -ld '):
                    return '-rw------- 1 root root 64 Jan 1 00:00 ownership.v1'
                if 'show running-config' in text:
                    return config
                if text.startswith('sha256sum '):
                    return ''.join(digests[path] + '  ' + path + '\n'
                                   for path in kl.STABLE_FILES)
                raise AssertionError('unexpected command: ' + text)

            def execute(self, text, timeout=None, on_stdout=None):
                if 'pidof' in text:
                    return 0, '123\n456\n', ''
                if 'iptables -t mangle -S _CUST_BYPASS_WA_' in text:
                    return 0, NETFILTER_RULES, ''
                if 'iptables -t mangle -S PREROUTING' in text:
                    return 0, NETFILTER_LINK + '\n', ''
                if 'curl' in text:
                    return 0, 'KEENETIC_LAB_HTTP_OK', ''
                raise AssertionError('unexpected execute: ' + text)
        return Guest()

    def test_ledger_must_not_claim_the_reused_bridge(self):
        ledger = (CLEAN_LEDGER
                  .replace('owned|package|mihomo\n',
                           'owned|proxy|Proxy0\nowned|package|mihomo\n'))
        guest = self._guest(ledger, self._config(FIXTURE_BLOCK))
        with self.assertRaises(kl.ContractFailure) as caught:
            kl.contract(guest, self._mirror(), None, self._fixture())
        self.assertEqual(caught.exception.reason, 'native_proxy_invalid')

    def test_renamed_description_is_a_public_failure(self):
        renamed = FIXTURE_BLOCK.replace('user-legacy-bridge', 'renamed-by-someone')
        guest = self._guest(CLEAN_LEDGER, self._config(renamed))
        with self.assertRaises(kl.ContractFailure) as caught:
            kl.contract(guest, self._mirror(), None, self._fixture())
        self.assertEqual(caught.exception.reason, 'legacy_bridge_changed')

    def test_matching_bridge_and_unowned_ledger_pass(self):
        guest = self._guest(CLEAN_LEDGER, self._config(FIXTURE_BLOCK))
        state = kl.contract(guest, self._mirror(), None, self._fixture())
        # block() extracts the block without a trailing newline; the contract
        # comparison normalizes trailing newlines on both sides.
        self.assertEqual(state['proxy'].rstrip('\n'), FIXTURE_BLOCK.rstrip('\n'))
        self.assertIn('-j _CUST_BYPASS_WA_', state['prerouting'])
        self.assertTrue(kl.legacy_bridge_shape(state['proxy']))


class LegacyBridgeIdempotenceTests(unittest.TestCase):
    SNAPSHOT = {'files': {}, 'proxy': 'p', 'policy': 'l',
                'ledger': 'd', 'netfilter': 'n', 'prerouting': 'r'}

    def _run(self, install_output):
        saved = kl.contract
        kl.contract = lambda *a, **k: dict(self.SNAPSHOT)
        try:
            evidence = Evidence()

            class Guest:
                def execute(self, _command, timeout=None, on_stdout=None):
                    return 0, install_output, ''
            ok = kl.idempotence(Guest(), None, 'a' * 40, evidence, None,
                                {'iface': 'Proxy0', 'proxy': 'p'})
            return ok, evidence
        finally:
            kl.contract = saved

    def test_second_toggle_on_a_reused_bridge_fails_publicly(self):
        ok, evidence = self._run('output without the skip marker [OK] Done')
        self.assertFalse(ok)
        self.assertEqual(evidence.checks['KAS_IDEMPOTENCE']['reason'],
                         'legacy_bridge_reactivated')

    def test_production_skip_marker_passes(self):
        marker = ('[setup] Skipping proxy activation: existing project stack '
                  'and reused interface [OK] Done')
        ok, evidence = self._run(marker)
        self.assertTrue(ok)
        self.assertEqual(evidence.checks['KAS_IDEMPOTENCE']['reason'],
                         'native_command_verified')


class LegacyBridgeIpGlobalTests(unittest.TestCase):
    """A/B (issue #50 review): 'ip global auto' is issued only when requested."""

    BLOCK = ('interface Proxy0\n'
             ' description "user-legacy-bridge"\n'
             ' proxy protocol socks5\n'
             ' proxy socks5-udp\n'
             ' proxy upstream 127.0.0.1 7890\n'
             ' up\n!')

    def _fixture_calls(self, with_ip_global):
        calls = []
        shows = {'n': 0}

        class Guest:
            def execute(self, command):
                if command.startswith('test -e '):
                    return 1, '', ''
                raise AssertionError('unexpected execute: ' + command)

            def command(self, text):
                calls.append(text)
                if 'show running-config' in text:
                    shows['n'] += 1
                    return self.BLOCK if shows['n'] >= 2 else ''
                return ''
        Guest.BLOCK = self.BLOCK
        guest = Guest()
        kl.legacy_bridge_resources(guest, with_ip_global=with_ip_global)
        return [c for c in calls if 'ndmc' in c]

    def test_ip_global_issued_when_requested(self):
        calls = self._fixture_calls(True)
        self.assertTrue(any('ip global auto' in c for c in calls),
                        'ip global auto missing: %r' % (calls,))

    def test_ip_global_omitted_by_default(self):
        calls = self._fixture_calls(False)
        self.assertFalse(any('ip global auto' in c for c in calls))
        self.assertTrue(any(c.endswith('"interface Proxy0 up"') for c in calls))


if __name__ == '__main__':
    unittest.main()

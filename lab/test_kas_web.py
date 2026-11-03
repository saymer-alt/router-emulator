"""Fast host regression: native Web guest must use canonical KAS prepare first."""
import unittest
from unittest.mock import patch, Mock
from lab.gates import Evidence
from lab import kas_web_e2e


class WebImporterContractTests(unittest.TestCase):
    def test_old_and_new_listener_source_contracts(self):
        marker = 'web/control-center/lan.go'
        self.assertFalse(kas_web_e2e.web_uses_wildcard_listener({}))
        self.assertFalse(kas_web_e2e.web_uses_wildcard_listener({
            marker: b'address:=net.JoinHostPort(ip.String(),"8081")'}))
        self.assertTrue(kas_web_e2e.web_uses_wildcard_listener({
            marker: b'address:=net.JoinHostPort("0.0.0.0","8081")'}))
        self.assertTrue(kas_web_e2e.web_uses_wildcard_listener({
            marker: b'address:=net.JoinHostPort("0.0.0.0","18080")'}))
        self.assertFalse(kas_web_e2e.web_uses_wildcard_listener({
            marker: b'address:=net.JoinHostPort("0.0.0.0","8080")'}))

    def test_initial_port_comes_from_pinned_manage_script(self):
        script = 'web/control-center/kas-web-manage.sh'
        self.assertIsNone(kas_web_e2e.panel_initial_port({}))
        self.assertEqual(kas_web_e2e.panel_initial_port({
            script: b'set -- -lan -listen auto -lan-interface br0'
                    b' -initial-port 18081 -settings-file "$ROOT/etc/x"'}), 18081)
        self.assertEqual(kas_web_e2e.panel_initial_port({
            script: b'set -- -lan -listen auto -lan-interface br0'
                    b' -initial-port 18080 -settings-file "$ROOT/etc/x"'}), 18080)

    def test_service_port_prefers_the_panel_persisted_record(self):
        import json as _json
        script = 'web/control-center/kas-web-manage.sh'
        beta6_sources = {
            script: b'-initial-port 18080 -settings-file "$ROOT/etc/kas.settings"',
        }
        guest = Mock()
        guest.command.return_value = _json.dumps(
            {'version': 1, 'port': 18084, 'auto_port_fallback': True})
        self.assertEqual(
            kas_web_e2e.panel_service_port(guest, beta6_sources), 18084)
        # No persisted record: the pinned manage script default is the
        # fallback, never a hard-coded panel assumption.
        guest.command.side_effect = RuntimeError('guest probe returned nonzero')
        self.assertEqual(
            kas_web_e2e.panel_service_port(guest, beta6_sources), 18080)
        # Unparseable record falls back as well.
        guest.command.side_effect = None
        guest.command.return_value = '{not json'
        self.assertEqual(
            kas_web_e2e.panel_service_port(guest, beta6_sources), 18080)
        self.assertEqual(kas_web_e2e.panel_service_port(guest, {}), 18081)

    def test_large_binary_uses_bounded_extended_timeout_and_digest(self):
        payload = b'fake-ELF-for-test'
        digest = __import__('hashlib').sha256(payload).hexdigest()
        guest = Mock()
        guest.command.side_effect = [None, None, digest + '  /opt/lab/.kas-web.new', None]
        kas_web_e2e.put_large_binary(guest, '/opt/lab/kas-web', payload)
        calls = guest.command.call_args_list
        self.assertEqual(calls[1].kwargs['timeout'], 45)
        self.assertEqual(calls[1].kwargs['input_data'], payload)
        self.assertIn('mv /opt/lab/.kas-web.new', calls[-1].args[0])
        with self.assertRaises(RuntimeError):
            kas_web_e2e.put_large_binary(guest, '/untrusted/path', payload)

    def test_opt_in_native_lifecycle_is_not_run_without_managed_script(self):
        evidence = Evidence()
        mirror = Mock()
        mirror.sources = {}
        result = kas_web_e2e.test_opt_in_service(Mock(), mirror, evidence)
        self.assertTrue(result)
        self.assertEqual(evidence.checks['KAS_WEB_LIFECYCLE']['status'], 'NOT_TESTED')

    def test_prepares_verified_source_before_install(self):
        evidence = Evidence()
        guest, mirror = Mock(), Mock()
        calls = []
        with patch.object(kas_web_e2e, 'prepare',
                          side_effect=lambda *args: calls.append('prepare')), \
             patch.object(kas_web_e2e, 'install',
                          side_effect=lambda *args: (calls.append('install'), False)[1]):
            result = kas_web_e2e.lifecycle(guest, mirror, 'a' * 40, evidence, '/missing/not/used')
        self.assertFalse(result)
        self.assertEqual(calls, ['prepare', 'install'])
        self.assertEqual(evidence.checks['KAS_WEB_IMPORT']['status'], 'BLOCKED')

    def test_preparation_failure_does_not_run_installer(self):
        evidence = Evidence()
        with patch.object(kas_web_e2e, 'prepare', side_effect=RuntimeError('offline')), \
             patch.object(kas_web_e2e, 'install') as installer:
            result = kas_web_e2e.lifecycle(Mock(), Mock(), 'b' * 40, evidence, '/unused')
        self.assertFalse(result)
        installer.assert_not_called()
        self.assertEqual(evidence.gates['KAS_INSTALL']['status'], 'BLOCKED')
        self.assertEqual(evidence.checks['KAS_WEB_IMPORT']['status'], 'BLOCKED')


if __name__ == '__main__':
    unittest.main()

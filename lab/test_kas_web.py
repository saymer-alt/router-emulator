"""Fast host regression: native Web guest must use canonical KAS prepare first."""
import unittest
from unittest.mock import patch, Mock
from lab.gates import Evidence
from lab import kas_web_e2e


class WebImporterContractTests(unittest.TestCase):
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


class ProbeClassificationTests(unittest.TestCase):
    """Public category, exit code and elapsed seconds for the FAIL line only."""

    def test_categories_map_without_guest_text(self):
        self.assertEqual(kas_web_e2e.classify_web_error(TimeoutError()), 'TIMEOUT')
        ssh_error = type('SSHException', (Exception,), {})
        self.assertEqual(kas_web_e2e.classify_web_error(ssh_error()), 'SSH_ERROR')
        self.assertEqual(kas_web_e2e.classify_web_error(EOFError()), 'SSH_ERROR')
        self.assertEqual(
            kas_web_e2e.classify_web_error(RuntimeError('guest probe returned nonzero')),
            'COMMAND_NONZERO')
        # Any other exception must stay a generic category, never carry text.
        self.assertEqual(kas_web_e2e.classify_web_error(ValueError('secret-data')), 'PROBE_ERROR')

    def test_nonzero_exit_is_recorded_publicly_and_raised(self):
        guest = Mock()
        guest.execute.return_value = (7, b'', b'private stderr stays private')
        classified = kas_web_e2e.ClassifiedGuest(guest)
        with self.assertRaises(RuntimeError):
            classified.command('iptables -t mangle -S _CUST_BYPASS_WA_')
        self.assertIn('COMMAND_NONZERO exit=7', classified.last_failure)
        self.assertIn('after', classified.last_failure)
        self.assertNotIn('private', classified.last_failure)

    def test_success_returns_output_and_timeout_is_classified(self):
        guest = Mock()
        guest.execute.return_value = (0, b'-A _CUST_BYPASS_WA_', b'')
        classified = kas_web_e2e.ClassifiedGuest(guest)
        self.assertEqual(classified.command('x'), b'-A _CUST_BYPASS_WA_')
        guest.execute.side_effect = TimeoutError()
        with self.assertRaises(TimeoutError):
            classified.command('x', timeout=5)
        self.assertTrue(classified.last_failure.startswith('TIMEOUT after'))
        guest.execute.side_effect = None
        guest.execute.return_value = (0, b'ok', b'')
        self.assertEqual(classified.execute('y'), (0, b'ok', b''))

    def test_lifecycle_wraps_guest_for_classification(self):
        # The wrapper is applied at the lifecycle entry, so every helper probe
        # records its public category without touching the real guest class.
        self.assertIn('guest = ClassifiedGuest(guest)',
                      __import__('inspect').getsource(kas_web_e2e.lifecycle))

    def test_public_failure_evidence_degrades_and_reports(self):
        guest = Mock()
        guest.execute.side_effect = [(0, b'PANEL_ADDRESS: 192.168.1.1:8081\n', b''), (0, b'alive\n', b'')]
        classified = kas_web_e2e.ClassifiedGuest(guest)
        evidence = classified.public_failure_evidence()
        self.assertIn('startup_records=PANEL_ADDRESS: 192.168.1.1:8081', evidence)
        self.assertIn('panel_process=alive', evidence)
        guest.execute.side_effect = TimeoutError()
        degraded = kas_web_e2e.ClassifiedGuest(guest).public_failure_evidence()
        self.assertEqual(degraded, 'startup_records=unavailable panel_process=unavailable')

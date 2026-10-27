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
        guest.command.side_effect = [None, digest + '  /opt/lab/kas-web']
        kas_web_e2e.put_large_binary(guest, '/opt/lab/kas-web', payload)
        call = guest.command.call_args_list[0]
        self.assertEqual(call.kwargs['timeout'], 180)
        self.assertEqual(call.kwargs['input_data'], payload)
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

"""Fast host regression: native Web guest must use canonical KAS prepare first."""
import unittest
from unittest.mock import patch, Mock
from lab.gates import Evidence
from lab import kas_web_e2e


class WebImporterContractTests(unittest.TestCase):
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

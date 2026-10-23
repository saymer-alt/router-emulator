import unittest
from lab.ported_e2e import login_steps, phase_failure
from lab.gates import Evidence


class ControllerTests(unittest.TestCase):
    def test_later_probe_failure_preserves_proven_entware(self):
        evidence = Evidence()
        evidence.set('ENTWARE_READY', 'PASS', 'ext4_opkg_abi_and_reboot')
        phase_failure(evidence, 'KAS_PREFLIGHT')
        self.assertEqual(evidence.gates['ENTWARE_READY']['status'], 'PASS')
        self.assertEqual(evidence.gates['KAS_PREFLIGHT']['status'], 'FAIL')
        self.assertEqual(evidence.gates['KAS_INSTALL']['status'], 'NOT_TESTED')

    def test_extra_check_exception_is_visible_after_completed_primary_gate(self):
        evidence = Evidence()
        evidence.set('KAS_DOCTOR', 'PASS', 'native_doctor_passed')
        evidence.check('KAS_INSTALL_INTERRUPTION', 'NOT_TESTED', 'unattempted')
        phase_failure(evidence, 'KAS_DOCTOR')
        self.assertEqual(evidence.gates['KAS_DOCTOR']['status'], 'PASS')
        self.assertEqual(evidence.checks['KAS_INSTALL_INTERRUPTION']['status'], 'FAIL')

    def test_login_requires_password_prompt_and_actual_version_response(self):
        steps = login_steps()
        user = next(i for i, s in enumerate(steps) if s.get('credential') == 'username')
        password = next(i for i, s in enumerate(steps) if s.get('credential') == 'password')
        self.assertEqual(steps[user - 1]['pattern'], 'Login:')
        self.assertEqual(steps[password - 1]['pattern'], 'Password:')
        version = next(i for i, s in enumerate(steps) if s.get('text') == 'show version')
        self.assertEqual(steps[version + 1]['pattern'], r'release:\s+5\.01\.C\.8\.0-2')
        self.assertNotIn('password', {s.get('text') for s in steps})

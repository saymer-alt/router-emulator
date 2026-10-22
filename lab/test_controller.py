import unittest
from lab.ported_e2e import login_steps


class ControllerTests(unittest.TestCase):
    def test_login_requires_password_prompt_and_actual_version_response(self):
        steps = login_steps()
        user = next(i for i, s in enumerate(steps) if s.get('credential') == 'username')
        password = next(i for i, s in enumerate(steps) if s.get('credential') == 'password')
        self.assertEqual(steps[user - 1]['pattern'], 'Login:')
        self.assertEqual(steps[password - 1]['pattern'], 'Password:')
        version = next(i for i, s in enumerate(steps) if s.get('text') == 'show version')
        self.assertEqual(steps[version + 1]['pattern'], r'release:\s+5\.01\.C\.8\.0-2')
        self.assertNotIn('password', {s.get('text') for s in steps})

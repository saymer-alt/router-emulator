"""#356 unit contracts: no hardware, feed changes, or host /opt writes."""
from pathlib import Path
from contextlib import redirect_stdout
import io
import unittest
from unittest import mock

from lab import busybox_transition as module
from lab.gates import Evidence


class NativeBusyboxTransitionTests(unittest.TestCase):
    def test_package_digest_is_required_before_parsing(self):
        with self.assertRaisesRegex(ValueError, 'pinned BusyBox IPK differs'):
            module.verify_package(b'not-an-ipk')

    def test_restricted_guest_upload_paths(self):
        guest = object()
        with self.assertRaises(ValueError):
            module.upload(guest, '/opt/etc/mihomo/config.yaml', b'no')

    def test_phase_uses_real_old_prerm_and_system_shell(self):
        src = module.phase_script()
        self.assertTrue(src.startswith('#!/bin/sh'))
        self.assertIn(module.PRERM_OLD_SHA, src)
        self.assertIn('OLD_PRERM=/opt/lib/opkg/info/busybox.prerm', src)
        self.assertIn('/bin/sh /opt/lab/kas-entware-preflight.sh', src)
        self.assertIn('trap ', src)
        self.assertNotIn('ln -sf', src)
        self.assertNotIn('opkg upgrade', src)
        self.assertNotIn('opkg update', src)
        self.assertIn('NATIVE_POSTINST_RECOVERY_PASS', src)

    def test_mismatched_native_baseline_fails_before_fetch_or_guest_write(self):
        guest = mock.Mock()
        guest.command.return_value = 'Package: busybox\nVersion: 0.0\nStatus: install ok installed'
        evidence = Evidence()
        with mock.patch.object(module, 'fetch_verified') as fetch:
            self.assertFalse(module.accept(guest, Path('/nonexistent'), evidence))
            fetch.assert_not_called()
        self.assertEqual(evidence.checks['ENTWARE_BUSYBOX_TRANSITION'],
                         {'status': 'FAIL', 'reason': 'native_command_failed'})
        self.assertEqual(guest.command.call_count, 1)

    def test_failure_phase_is_public_and_private_exception_text_is_hidden(self):
        guest = mock.Mock()
        guest.command.return_value = 'SECRET_PRIVATE_GUEST_OUTPUT'
        evidence = Evidence()
        stream = io.StringIO()
        with redirect_stdout(stream):
            self.assertFalse(module.accept(guest, Path('/nonexistent'), evidence))
        printed = stream.getvalue()
        self.assertIn('FAIL phase=baseline-opkg class=RuntimeError', printed)
        self.assertNotIn('SECRET_PRIVATE_GUEST_OUTPUT', printed)

    def test_lab_checks_do_not_mark_kas_release_gates_pass(self):
        evidence = Evidence()
        evidence.check('ENTWARE_BUSYBOX_TRANSITION', 'PASS', 'native_command_verified')
        self.assertEqual(evidence.gates['KAS_INSTALL']['status'], 'NOT_TESTED')
        self.assertEqual(evidence.gates['KAS_UNINSTALL']['status'], 'NOT_TESTED')


if __name__ == '__main__':
    unittest.main()

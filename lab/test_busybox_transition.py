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

    def test_real_ipk_transfer_reuses_native_accepted_put_helper(self):
        guest = mock.Mock()
        payload = b"fixed synthetic IPK bytes\\x00"
        with mock.patch.object(module, 'put_guest_file') as put:
            module.upload(guest, '/opt/lab/busybox-1.38.ipk', payload)
        guest.command.assert_called_once_with('mkdir -p /opt/lab')
        put.assert_called_once_with(
            guest, '/opt/lab/busybox-1.38.ipk', payload, mode='600')

    def test_transfer_errors_propagate_to_fail_closed_gate(self):
        guest = mock.Mock()
        with mock.patch.object(module, 'put_guest_file',
                               side_effect=RuntimeError('digest differs')):
            with self.assertRaises(RuntimeError):
                module.upload(guest, '/opt/lab/busybox-1.38.ipk', b'bad data')

    def test_actual_1_37_and_1_38_opkg_inventory_are_accepted(self):
        guest = mock.Mock()
        guest.command.return_value = (
            'libgcc - 8.4.0-12\n'
            'busybox - 1.37.0-6\n'
            'grep - 3.11-3\n')
        self.assertEqual(module.require_busybox_version(guest, module.VERSION_OLD),
                         module.VERSION_OLD)
        self.assertEqual(guest.command.call_args.args[0],
                         '/opt/bin/opkg list-installed')
        guest.command.return_value = 'busybox - 1.38.0-3\n'
        self.assertEqual(module.require_busybox_version(guest, module.VERSION_NEW),
                         module.VERSION_NEW)

    def test_missing_duplicate_and_unexpected_busybox_versions_fail_closed(self):
        guest = mock.Mock()
        for listing in ('', 'busybox - 1.38.0-3\n', 'busybox - 1.37.0-6\n'
                        'busybox - 1.37.0-6\n', 'busybox - 1.37.0-6-other\n'):
            guest.command.return_value = listing
            with self.assertRaisesRegex(RuntimeError, 'unexpected installed'):
                module.require_busybox_version(guest, module.VERSION_OLD)

    def test_phase_uses_real_old_prerm_and_system_shell(self):
        src = module.phase_script()
        self.assertTrue(src.startswith('#!/bin/sh'))
        self.assertIn(module.PRERM_OLD_SHA, src)
        self.assertIn('OLD_PRERM=/opt/lib/opkg/info/busybox.prerm', src)
        self.assertIn('"$BB" sh /opt/lab/kas-entware-preflight.sh', src)
        self.assertIn('trap ', src)
        self.assertNotIn('ln -sf', src)
        self.assertNotIn('opkg upgrade', src)
        self.assertNotIn('opkg update', src)
        self.assertIn('NATIVE_POSTINST_RECOVERY_PASS', src)

    def test_upgrade_script_tracks_real_foreign_grep_and_observed_shell_state(self):
        src = module.upgrade_script()
        self.assertTrue(src.startswith('#!/bin/sh'))
        # The digest target is the real package-owned GNU grep ELF; the
        # Alternatives symlink /opt/bin/grep must never be asserted as a file.
        self.assertNotIn('/opt/bin/grep', src)
        self.assertIn(module.POSTINST_OLD_SHA, src)
        self.assertIn('/opt/lib/opkg/info/busybox.postinst', src)
        self.assertIn('install /opt/lab/busybox-1.38.ipk', src)
        self.assertIn('NATIVE_UPGRADE_SH_MISSING_REPRODUCED', src)
        self.assertIn('NATIVE_UPGRADE_SH_RESTORED_BY_POSTINST', src)
        self.assertIn('NATIVE_UPGRADE_RECOVERY_PASS', src)
        self.assertNotIn('ln -sf', src)
        self.assertNotIn('opkg upgrade', src)
        self.assertNotIn('opkg update', src)

    def test_foreign_grep_digest_targets_the_real_libexec_file(self):
        # grep 3.12-1 exposes GNU grep via opkg Alternatives: /opt/bin/grep is
        # a symlink, so the baseline must assert the real ELF instead (the
        # previous head failed run 41910981207 exactly at foreign-grep-baseline).
        self.assertEqual(module.FOREIGN_GREP, '/opt/libexec/grep-gnu')

    def test_upgrade_script_is_in_the_upload_allowlist(self):
        guest = mock.Mock()
        with mock.patch.object(module, 'put_guest_file') as put:
            module.upload(guest, '/opt/lab/busybox-upgrade.sh', b'#!/bin/sh\n')
        guest.command.assert_called_once_with('mkdir -p /opt/lab')
        put.assert_called_once_with(
            guest, '/opt/lab/busybox-upgrade.sh', b'#!/bin/sh\n', mode='600')

    def test_mismatched_native_baseline_fails_before_fetch_or_guest_write(self):
        guest = mock.Mock()
        guest.command.return_value = 'busybox - 0.0\n'
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

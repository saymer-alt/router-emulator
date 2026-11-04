import json
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET
from lab.gates import Evidence


class GateTests(unittest.TestCase):
    def test_expected_uninstall_refusal_keeps_aggregate_blocked(self):
        evidence = Evidence()
        for name in evidence.gates:
            evidence.set(name, 'PASS', 'native_command_verified')
        evidence.set('KAS_UNINSTALL', 'BLOCKED', 'native_remove_not_implemented')
        evidence.check('KAS_UNINSTALL_GUARD', 'PASS', 'native_command_verified')
        with self.assertRaises(ValueError):
            evidence.reference('kas_commit', 'private secret')
        with tempfile.TemporaryDirectory() as directory:
            evidence.write(directory)
            report = json.loads((Path(directory) / 'gates.json').read_text())
            self.assertEqual(report['outcome'], 'BLOCKED')
            root = ET.parse(Path(directory) / 'junit.xml').getroot()
            self.assertEqual(len(root.findall('testcase/failure')), 0)
            self.assertEqual(len(root.findall('testcase/skipped')), 1)

    def test_boot_pass_never_implies_auth_or_entware(self):
        evidence = Evidence()
        evidence.set('BOOT_READY', 'PASS', 'boot_stages_observed')
        self.assertEqual(evidence.gates['AUTH_READY']['status'], 'NOT_TESTED')
        self.assertEqual(evidence.gates['ENTWARE_READY']['status'], 'NOT_TESTED')

    def test_reports_skip_blocked_and_reject_guest_text(self):
        evidence = Evidence()
        evidence.set('AUTH_READY', 'FAIL', 'authentication_rejected')
        evidence.set('ENTWARE_READY', 'BLOCKED', 'prerequisite_blocked')
        with self.assertRaises(ValueError):
            evidence.set('AUTH_READY', 'FAIL', 'private UART with a secret')
        with tempfile.TemporaryDirectory() as directory:
            evidence.write(directory)
            report = json.loads((Path(directory) / 'gates.json').read_text())
            self.assertEqual(report['gates']['AUTH_READY']['status'], 'FAIL')
            root = ET.parse(Path(directory) / 'junit.xml').getroot()
            self.assertEqual(len(root.findall('testcase/failure')), 1)
            self.assertEqual(len(root.findall('testcase/skipped')), 10)

    def test_kas_update_stage_labels_are_public(self):
        evidence = Evidence()
        for check, reason in (
                ('KAS_UPDATE_RELEASE_UPGRADE', 'kas_update_release_upgrade_passed'),
                ('KAS_UPDATE_NOOP', 'kas_update_noop_passed'),
                ('KAS_UPDATE_IDEMPOTENT', 'kas_update_idempotent_passed'),
                ('KAS_UPDATE_NETFAIL_REFUSAL', 'kas_update_netfail_refused'),
                ('KAS_UPDATE_MODIFIED_REFUSAL', 'kas_update_modified_refused'),
                ('KAS_UPDATE_FOREIGN_CRON_REVIEW', 'kas_update_foreign_cron_reviewed'),
                ('KAS_UPDATE_COMMIT_FAILURE', 'kas_update_commit_failure_verified'),
                ('KAS_UPDATE_BUSY_REFUSAL', 'kas_update_busy_refused'),
                ('KAS_UPDATE_INERTNESS', 'kas_update_inertness_passed'),
                ('KAS_UPDATE_EXITCODES', 'kas_update_exitcodes_passed')):
            evidence.check(check, 'PASS', reason)
        evidence.set('KAS_UPDATE_E2E', 'PASS', 'kas_update_stage_passed')
        self.assertEqual(evidence.gates['KAS_UPDATE_E2E']['status'], 'PASS')

    def test_kas_update_blocked_is_never_pass(self):
        evidence = Evidence()
        evidence.check('KAS_UPDATE_RELEASE_UPGRADE', 'BLOCKED', 'kas_update_tls_delivery_failed')
        evidence.set('KAS_UPDATE_E2E', 'BLOCKED', 'kas_update_tls_delivery_failed')
        with tempfile.TemporaryDirectory() as directory:
            evidence.write(directory)
            report = json.loads((Path(directory) / 'gates.json').read_text())
            self.assertEqual(report['outcome'], 'BLOCKED')
            self.assertEqual(report['gates']['KAS_UPDATE_E2E']['status'], 'BLOCKED')

    def test_foreign_failure_phase_labels_are_public(self):
        evidence = Evidence()
        for reason in ('foreign_setup_failed', 'foreign_proxy_changed', 'foreign_cron_changed',
                       'native_command_timed_out', 'native_command_failed', 'native_socks_not_ready',
                       'native_services_duplicate', 'native_rules_not_ready', 'serial_timeout'):
            evidence.check('KAS_FOREIGN_RESOURCES', 'FAIL', reason)
        self.assertEqual(evidence.checks['KAS_FOREIGN_RESOURCES']['reason'], 'serial_timeout')

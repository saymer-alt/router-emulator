import json
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET
from lab.gates import Evidence


class GateTests(unittest.TestCase):
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
            self.assertEqual(len(root.findall('testcase/skipped')), 8)

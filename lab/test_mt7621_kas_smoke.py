"""No guest dependencies: public-only contract for MT7621 KAS smoke."""
import json
from pathlib import Path
import tempfile
import unittest

from lab.mt7621.kas_smoke import classify_failure, write_public


class Mt7621KASGuestContracts(unittest.TestCase):
    def test_unambiguous_real_install_exit_and_marker(self):
        self.assertEqual(classify_failure(0, "[OK] Done"), "INSTALLER_COMPLETED")
        self.assertEqual(classify_failure(1, "[OK] Done"), "INSTALLER_EXIT_NONZERO")
        self.assertEqual(classify_failure(0, "No marker"), "SUCCESS_MARKER_MISSING")

    def test_required_components_is_not_success(self):
        detail = "[ERROR] Full required component contract for the current default project profile"
        self.assertEqual(classify_failure(1, detail), "KEENETICOS_COMPONENTS_MISSING")

    def test_offline_dependency_categories(self):
        self.assertEqual(classify_failure(1, "opkg update failed"),
                         "OPKG_FEED_UNAVAILABLE")
        self.assertEqual(classify_failure(1, "Mihomo install failed"),
                         "IPK_UNAVAILABLE")

    def test_report_contains_only_fixed_fields(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "report" / "kas-smoke.json"
            ref = "a" * 40
            write_public(p, ref, "BLOCKED", "KEENETICOS_COMPONENTS_MISSING", 1)
            rec = json.loads(p.read_text())
            self.assertEqual(set(rec), {"schema", "kas_ref", "phase",
                                        "reason", "kas_installed", "exit_code"})
            self.assertFalse(rec["kas_installed"])
            self.assertEqual(rec["kas_ref"], ref)

    def test_no_markdown_banner_is_success(self):
        self.assertNotEqual(classify_failure(0, "=== Keenetic Auto Setup ==="),
                            "INSTALLER_COMPLETED")

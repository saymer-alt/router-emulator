"""No guest dependencies: public-only contract for MT7621 KAS smoke."""
import json
import hashlib
from pathlib import Path
import tempfile
import unittest

from lab.mt7621.kas_smoke import (
    classify_failure, write_public, missing_required_components,
    guarded_refusal_proven, lab_only_without_ext_utils_guard,
    require_exact_lab_component_gap, LAB_EXT_UTILS_GUARD,
    LAB_EXT_UTILS_DISABLED,
)


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
            self.assertEqual(set(rec), {"schema", "kas_ref", "phase", "reason", "kas_installed",
                                        "exit_code", "component_inventory_seen",
                                        "missing_component_ids", "test_expectation",
                                        "guard_contract_verified", "lab_ext_utils_exception_applied",
                                        "guest_source_verified", "source_variant"})
            self.assertFalse(rec["kas_installed"])
            self.assertEqual(rec["kas_ref"], ref)
            self.assertFalse(rec["lab_ext_utils_exception_applied"])
            self.assertFalse(rec["guest_source_verified"])
            self.assertEqual(rec["source_variant"], "PINNED_UNMODIFIED")

    def test_lab_exception_changes_exactly_one_guard_condition(self):
        # Synthetic source contains other active component and storage checks.
        original = (b"#!/bin/sh\n"
                    b'if ! component_list_has "$EXT_COMPONENT_ID"; then\n'
                    b'  exit 1\nfi\n'
                    + LAB_EXT_UTILS_GUARD + b"\n  exit 1\nfi\n"
                    + b'if ! component_list_has "$PROXY_COMPONENT_ID"; then\n'
                      b'  exit 1\nfi\n')
        candidate = lab_only_without_ext_utils_guard(original)
        self.assertEqual(candidate.count(LAB_EXT_UTILS_DISABLED), 1)
        self.assertNotIn(LAB_EXT_UTILS_GUARD + b"\n", candidate)
        self.assertEqual(candidate.replace(LAB_EXT_UTILS_DISABLED,
                                           LAB_EXT_UTILS_GUARD), original)
        self.assertEqual(hashlib.sha256(original).hexdigest(),
                         hashlib.sha256(
                             candidate.replace(LAB_EXT_UTILS_DISABLED,
                                               LAB_EXT_UTILS_GUARD)).hexdigest())
        self.assertIn(b'component_list_has "$EXT_COMPONENT_ID"', candidate)
        self.assertIn(b'component_list_has "$PROXY_COMPONENT_ID"', candidate)

    def test_lab_exception_refuses_upstream_drift_or_duplicate_guard(self):
        for source in (b"#!/bin/sh\n", b"#!/bin/sh\n" + LAB_EXT_UTILS_GUARD * 2,
                       b"#!/bin/sh\n" + LAB_EXT_UTILS_DISABLED):
            with self.subTest(source_length=len(source)):
                with self.assertRaises(ValueError):
                    lab_only_without_ext_utils_guard(source)

    def test_lab_exception_requires_only_missing_ext_utils(self):
        require_exact_lab_component_gap(["ext-utils"])
        for missing in (None, [], ["ext"], ["ext", "ext-utils"],
                        ["proxy", "ext-utils"], ["ext-utils", "proxy"]):
            with self.subTest(missing=missing):
                with self.assertRaises(ValueError):
                    require_exact_lab_component_gap(missing)

    def test_public_report_identifies_lab_only_exception(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "kas-smoke.json"
            write_public(p, "a" * 40, "BLOCKED", "PINNED_ASSET_UNAVAILABLE", 1,
                         component_gap=["ext-utils"], lab_exception_applied=True)
            rec = json.loads(p.read_text())
            self.assertTrue(rec["lab_ext_utils_exception_applied"])
            self.assertFalse(rec["guest_source_verified"])
            self.assertEqual(rec["source_variant"],
                             "DISPOSABLE_LAB_EXT_UTILS_EXCEPTION")
            self.assertFalse(rec["kas_installed"])

    def test_components_parsed_across_line_wrapping(self):
        source = ("release: 5.01.C.8.0-2\n"
                  "  components: proxy,dns-filter,opkg-kmod-\n"
                  "    netfilter,ext,ext-utils,dns-\n"
                  "    tls,system\n"
                  "    model: KN-9999\n")
        self.assertEqual(missing_required_components(source), [])

    def test_exact_missing_public_component_ids(self):
        source = ("components: ext,dns-https,system\n"
                  "  platform: KN-1913\n")
        self.assertEqual(missing_required_components(source),
                         ["proxy", "dns-filter", "opkg-kmod-netfilter", "ext-utils"])

    def test_missing_inventory_is_unknown_not_all_missing(self):
        self.assertIsNone(missing_required_components("release: 5.01.C.8.0-2"))


    def test_only_real_ext_utils_refusal_passes_guard(self):
        package_list = "entware-release - 2026\nbash - 5.2\n"
        self.assertTrue(guarded_refusal_proven(
            1, "KEENETICOS_COMPONENTS_MISSING", ["ext-utils"], package_list, True))
        self.assertFalse(guarded_refusal_proven(
            0, "KEENETICOS_COMPONENTS_MISSING", ["ext-utils"], package_list, True))
        self.assertFalse(guarded_refusal_proven(
            1, "KEENETICOS_COMPONENTS_MISSING", ["ext", "ext-utils"], package_list, True))
        self.assertFalse(guarded_refusal_proven(
            1, "IPK_UNAVAILABLE", ["ext-utils"], package_list, True))
        self.assertFalse(guarded_refusal_proven(
            1, "KEENETICOS_COMPONENTS_MISSING", ["ext-utils"],
            package_list + "mihomo - 1.0\n", True))
        self.assertFalse(guarded_refusal_proven(
            1, "KEENETICOS_COMPONENTS_MISSING", ["ext-utils"], package_list, False))

    def test_no_markdown_banner_is_success(self):
        self.assertNotEqual(classify_failure(0, "=== Keenetic Auto Setup ==="),
                            "INSTALLER_COMPLETED")

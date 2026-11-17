"""No guest dependencies: public-only contract for MT7621 KAS smoke."""
import json
import hashlib
from pathlib import Path
import tempfile
import unittest

from lab.mt7621.kas_smoke import (
    classify_failure, classify_installer_failure, write_public,
    missing_required_components, guarded_refusal_proven,
    lab_only_without_ext_utils_guard, verified_mihomo_version,
    matched_installer_error, mihomo_output_categories, mihomo_output_marker,
    mihomo_head_marker, MIHOMO_HEAD_MARKER_VALUES,
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
                                        "guest_source_verified", "source_variant",
                                        "kas_base_deps_feed", "base_packages_installed",
                                        "kas_mihomo_feed", "mihomo_package_installed",
                                        "mihomo_binary_version_verified",
                                        "installer_error_prefix", "mihomo_binary_path",
                                        "mihomo_probe_exit_code", "mihomo_output_has_name",
                                        "mihomo_output_has_version", "mihomo_output_marker",
                                        "kas_magitrickle_feed",
                                        "socat_package_installed", "magitrickle_package_installed",
                                        "magitrickle_daemon_running",
                                        "magitrickle_init_script_present",
                                        "mihomo_output_head_marker",
                                        "mihomo_stdout_size", "mihomo_stderr_size",
                                        "kas_https_stage", "kas_https_dns_configured",
                                        "kas_https_clock_synced",
                                        "kas_https_ca_trust_applied",
                                        "kas_https_helper_tls_verified"})
            self.assertEqual(rec["schema"], "mt7621-kas-smoke-v3")
            self.assertFalse(rec["kas_installed"])
            self.assertEqual(rec["kas_ref"], ref)
            self.assertFalse(rec["lab_ext_utils_exception_applied"])
            self.assertFalse(rec["guest_source_verified"])
            self.assertEqual(rec["source_variant"], "PINNED_UNMODIFIED")
            self.assertFalse(rec["kas_base_deps_feed"])
            self.assertEqual(rec["base_packages_installed"], [])
            self.assertFalse(rec["kas_mihomo_feed"])
            self.assertFalse(rec["mihomo_package_installed"])
            self.assertFalse(rec["mihomo_binary_version_verified"])
            self.assertIsNone(rec["installer_error_prefix"])
            self.assertEqual(rec["mihomo_binary_path"], "none")
            self.assertIsNone(rec["mihomo_probe_exit_code"])
            self.assertFalse(rec["mihomo_output_has_name"])
            self.assertFalse(rec["mihomo_output_has_version"])
            self.assertIsNone(rec["mihomo_output_marker"])
            self.assertFalse(rec["kas_magitrickle_feed"])
            self.assertFalse(rec["socat_package_installed"])
            self.assertFalse(rec["magitrickle_package_installed"])
            self.assertFalse(rec["magitrickle_daemon_running"])
            self.assertFalse(rec["magitrickle_init_script_present"])
            self.assertIsNone(rec["mihomo_output_head_marker"])
            self.assertIsNone(rec["mihomo_stdout_size"])
            self.assertIsNone(rec["mihomo_stderr_size"])
            self.assertFalse(rec["kas_https_stage"])
            self.assertFalse(rec["kas_https_dns_configured"])
            self.assertFalse(rec["kas_https_clock_synced"])
            self.assertFalse(rec["kas_https_ca_trust_applied"])
            self.assertFalse(rec["kas_https_helper_tls_verified"])

    def test_report_exposes_only_allowed_actual_base_install_ids(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "kas.json"
            write_public(path, "a" * 40, "BLOCKED", "PINNED_ASSET_UNAVAILABLE", 1,
                         kas_base_deps_feed=True,
                         installed_base_packages=["jq", "curl", "untrusted-field",
                                                  "ca-bundle", "jq"])
            record = json.loads(path.read_text())
            self.assertTrue(record["kas_base_deps_feed"])
            self.assertEqual(record["base_packages_installed"],
                             ["ca-bundle", "curl", "jq"])
            self.assertFalse(record["kas_installed"])

    def test_real_mihomo_version_parser_rejects_false_or_malformed_proof(self):
        self.assertTrue(verified_mihomo_version(
            0, "Mihomo Meta v1.19.33 linux mipsle go1.26", ""))
        self.assertTrue(verified_mihomo_version(
            0, "", "Mihomo Meta v1.19.33 linux mipsle"))
        for code, stdout, stderr in (
                (1, "Mihomo Meta v1.19.33", ""),
                (0, "Mihomo Meta v1.19.330", ""),
                (0, "Mihomo Meta v1.19.34", ""),
                (0, "another 1.19.33", ""),
                (0, "literal mihomox v1.19.33", ""),
                (127, "", "mihomo: not found")):
            with self.subTest(code=code, stdout=stdout):
                self.assertFalse(verified_mihomo_version(code, stdout, stderr))

    def test_mihomo_guest_evidence_fields_do_not_claim_kas_pass(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "mihomo.json"
            write_public(path, "a" * 40, "BLOCKED", "IPK_UNAVAILABLE", 1,
                         lab_exception_applied=True, kas_base_deps_feed=True,
                         kas_mihomo_feed=True, mihomo_package_installed=True,
                         mihomo_binary_version_verified=True)
            data = json.loads(path.read_text())
            self.assertTrue(data["kas_mihomo_feed"])
            self.assertTrue(data["mihomo_package_installed"])
            self.assertTrue(data["mihomo_binary_version_verified"])
            self.assertFalse(data["kas_installed"])
            self.assertEqual(data["reason"], "IPK_UNAVAILABLE")

    def test_mihomo_not_installed_is_not_inferred_from_local_mirror(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "mihomo.json"
            write_public(path, "a" * 40, "BLOCKED", "INSTALLER_NOT_COMPLETED",
                         kas_mihomo_feed=True)
            data = json.loads(path.read_text())
            self.assertTrue(data["kas_mihomo_feed"])
            self.assertFalse(data["mihomo_package_installed"])
            self.assertFalse(data["mihomo_binary_version_verified"])

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

    def test_matched_installer_error_uses_only_pinned_source_constants(self):
        source = (b'#!/bin/sh\n'
                  b'err "Mihomo install failed: GitHub package unavailable and '
                  b'Entware feed fallback failed"\n'
                  b'err "Failed to install $_pkg"\n'
                  b'err "MagiTrickle start failed"\n')
        matched = matched_installer_error(
            "[ERROR] Failed to install magitrickle\n", source)
        self.assertEqual(matched, "Failed to install")
        matched = matched_installer_error(
            "[ERROR] Mihomo install failed: GitHub package unavailable and "
            "Entware feed fallback failed\n", source)
        self.assertEqual(
            matched, "Mihomo install failed: GitHub package unavailable and "
                     "Entware feed fallback failed")
        self.assertIsNone(matched_installer_error("clean output", source))
        self.assertIsNone(matched_installer_error(None, source))
        # A guest-echoed installer-like string without matching pinned
        # constant must never be returned.
        self.assertIsNone(matched_installer_error(
            "[ERROR] something completely different", source))

    def test_mihomo_output_categories_are_fixed_booleans(self):
        self.assertEqual(mihomo_output_categories(
            "Mihomo Meta v1.19.33 linux mipsle", ""), (True, True))
        self.assertEqual(mihomo_output_categories(
            "", "sh: /opt/bin/mihomo: Exec format error"), (True, False))
        self.assertEqual(mihomo_output_categories("", ""), (False, False))
        self.assertEqual(mihomo_output_categories(None, ""), (False, False))

    def test_mihomo_output_marker_matches_only_pinned_constants(self):
        self.assertEqual(mihomo_output_marker(
            "Mihomo Meta v1.19.33 linux mipsle", ""), "Mihomo Meta")
        self.assertEqual(mihomo_output_marker(
            "", "panic: should never be called\n\ngoroutine 1 [running]:"),
            "panic: should never be called")
        self.assertEqual(mihomo_output_marker(
            "", "flag provided but not defined: -v\nUsage of mihomo:"),
            "flag provided but not defined")
        self.assertEqual(mihomo_output_marker(
            "runtime: out of memory: cannot allocate", ""), "runtime: out of memory")
        self.assertEqual(mihomo_output_marker(
            "", "fatal error: unexpected signal"), "fatal error:")
        self.assertIsNone(mihomo_output_marker("", "completely unrelated"))
        self.assertIsNone(mihomo_output_marker(None, None))

    def test_mihomo_head_marker_matches_only_pinned_constants(self):
        self.assertEqual(mihomo_head_marker(
            "Mihomo Meta 1.19.33 linux mipsle"), "Mihomo Meta")
        self.assertEqual(mihomo_head_marker(
            "panic: should never be called\n\ngoroutine 1 [running]:"),
            "panic: should never be called")
        self.assertEqual(mihomo_head_marker(
            "panic: runtime error: invalid memory address"), "panic:")
        self.assertEqual(mihomo_head_marker(
            "fatal error: unexpected signal"), "fatal error:")
        self.assertEqual(mihomo_head_marker(
            "runtime: out of memory: cannot allocate"), "runtime:")
        self.assertEqual(mihomo_head_marker(
            "futexwakeup addr=0x1006 returned -38"), "futexwakeup addr=")
        self.assertEqual(mihomo_head_marker(
            "goroutine 1 [running]:\nmain.main()"), "goroutine")
        self.assertIsNone(mihomo_head_marker("completely unrelated"))
        self.assertIsNone(mihomo_head_marker(None))

    def test_head_marker_values_are_stripped_constants(self):
        self.assertIn("panic:", MIHOMO_HEAD_MARKER_VALUES)
        self.assertIn("Mihomo Meta", MIHOMO_HEAD_MARKER_VALUES)
        self.assertTrue(all(value == value.strip()
                            for value in MIHOMO_HEAD_MARKER_VALUES))

    def test_final_err_wins_over_early_mihomo_warn(self):
        # Run 44120337317: the early GitHub Mihomo WARN printed before the
        # successful Entware fallback made the legacy classifier report
        # IPK_UNAVAILABLE although the installer died later on the MagiTrickle
        # HTTPS helper. The final pinned err literal must decide the category.
        output = ("[WARN] Mihomo install from the downloaded GitHub package failed\n"
                  "[WARN] GitHub Mihomo package unavailable; Entware fallback used\n"
                  "[ERROR] Failed to download MagiTrickle repository helper through curl/wget\n")
        self.assertEqual(classify_installer_failure(
            1, output,
            "Failed to download MagiTrickle repository helper through curl/wget"),
            "MAGITRICKLE_HELPER_DOWNLOAD_FAILED")

    def test_mihomo_service_start_failure_is_not_ipk_unavailable(self):
        # Run 44162737407: the pinned installer aborted on its own mihomo
        # start/restart confirmation while the earlier GitHub WARN text was
        # still in the captured output; the legacy fallback mislabelled the
        # terminal stage IPK_UNAVAILABLE. The pinned err literal now wins.
        output = ("[WARN] GitHub Mihomo package unavailable, trying Entware feed "
                  "fallback (last resort)...\n"
                  "[ERROR] Mihomo start/restart could not be confirmed.\n")
        self.assertEqual(classify_installer_failure(
            1, output, "Mihomo start/restart could not be confirmed."),
            "MIHOMO_START_NOT_CONFIRMED")

    def test_classifier_maps_every_pinned_final_err_literal(self):
        pairs = {
            "Mihomo install failed: GitHub package unavailable and Entware feed fallback failed":
                "MIHOMO_INSTALL_FAILED",
            "Mihomo package installation completed but no executable canonical binary was found":
                "MIHOMO_BINARY_MISSING",
            "Downloaded MagiTrickle repository helper failed shell syntax validation":
                "MAGITRICKLE_HELPER_SYNTAX_FAILED",
            "Failed to add MagiTrickle package repository":
                "MAGITRICKLE_HELPER_EXEC_FAILED",
            "opkg update after changing the MagiTrickle repository failed":
                "MAGITRICKLE_FEED_UPDATE_FAILED",
            "MagiTrickle package is not present after installation":
                "MAGITRICKLE_INSTALL_FAILED",
            "MagiTrickle init script missing after installation":
                "MAGITRICKLE_INSTALL_FAILED",
            "MagiTrickle start failed":
                "MAGITRICKLE_START_FAILED",
            "Mihomo start/restart could not be confirmed.":
                "MIHOMO_START_NOT_CONFIRMED",
        }
        for prefix, category in pairs.items():
            with self.subTest(prefix=prefix):
                self.assertEqual(classify_installer_failure(
                    1, "[ERROR] " + prefix, prefix), category)

    def test_failed_to_install_is_disambiguated_by_pinned_package_names(self):
        self.assertEqual(classify_installer_failure(
            1, "[ERROR] Failed to install magitrickle", "Failed to install"),
            "MAGITRICKLE_INSTALL_FAILED")
        self.assertEqual(classify_installer_failure(
            1, "[ERROR] Failed to install mihomo", "Failed to install"),
            "MIHOMO_INSTALL_FAILED")
        # Base deps (pkg_ensure) keep the legacy category.
        self.assertEqual(classify_installer_failure(
            1, "[ERROR] Failed to install curl", "Failed to install"),
            "IPK_UNAVAILABLE")
        # A guest-echoed installer-like line for an unknown package never
        # mints a new category.
        self.assertEqual(classify_installer_failure(
            1, "[ERROR] Failed to install untrusted", "Failed to install"),
            "IPK_UNAVAILABLE")

    def test_early_warn_without_final_err_keeps_legacy_category(self):
        # The exact WARN literal from the pinned installer (line 1904) that
        # the legacy classifier mapped to IPK_UNAVAILABLE.
        self.assertEqual(classify_installer_failure(
            1, "[WARN] GitHub Mihomo package unavailable, trying Entware feed "
               "fallback (last resort)...", None), "IPK_UNAVAILABLE")
        self.assertEqual(classify_installer_failure(
            1, "opkg update failed", None), "OPKG_FEED_UNAVAILABLE")
        self.assertEqual(classify_installer_failure(
            1, "unrelated output", None), "INSTALLER_EXIT_NONZERO")

    def test_success_with_early_warn_is_completed(self):
        self.assertEqual(classify_installer_failure(
            0, "[WARN] Mihomo install from the downloaded GitHub package failed\n"
               "=== Keenetic Auto Setup ===\n[OK] Done", None),
            "INSTALLER_COMPLETED")

    def test_classifier_never_returns_unpinned_values(self):
        allowed = {"INSTALLER_COMPLETED", "KEENETICOS_COMPONENTS_MISSING",
                   "OPKG_FEED_UNAVAILABLE", "IPK_UNAVAILABLE",
                   "MIPSEL_ABI_UNSUPPORTED", "PINNED_ASSET_UNAVAILABLE",
                   "SUCCESS_MARKER_MISSING", "INSTALLER_EXIT_NONZERO",
                   "MIHOMO_INSTALL_FAILED", "MIHOMO_BINARY_MISSING",
                   "MAGITRICKLE_HELPER_DOWNLOAD_FAILED",
                   "MAGITRICKLE_HELPER_SYNTAX_FAILED",
                   "MAGITRICKLE_HELPER_EXEC_FAILED",
                   "MAGITRICKLE_FEED_UPDATE_FAILED",
                   "MAGITRICKLE_INSTALL_FAILED", "MAGITRICKLE_START_FAILED",
                   "MIHOMO_START_NOT_CONFIRMED"}
        probes = [
            (0, "[OK] Done", None), (1, "[ERROR] Failed to install mihomo",
                                     "Failed to install"),
            (1, "[ERROR] MagiTrickle start failed", "MagiTrickle start failed"),
            (1, "[WARN] GitHub Mihomo package unavailable", None),
            (1, "architecture cannot be supported", None),
            (0, "no marker", None), (1, "", ""),
            (1, "download failed for unknown thing", None),
        ]
        for code, output, prefix in probes:
            with self.subTest(code=code, output=output):
                self.assertIn(classify_installer_failure(code, output, prefix),
                              allowed)

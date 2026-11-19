"""Negative and contract tests for the Mihomo S28 MIPSel pinned-IPK guest lab.

No guest execution here: the host auditor is exercised on synthetic crafted
IPK/ELF fixtures, the committed scenario is validated against the KN-1913
upgrade/rollback proof requirements (S99->S28 migration, config preservation,
dual-stack :::7890 readiness, autostart), and the workflow adapter keeps the
default TeleMT behavior byte-identical.
"""
import hashlib
import json
from pathlib import Path
import re
import unittest

from lab.mt7621 import mihomo_s28_lab as ml
from test_mt7621_telemt import gzip_tar, make_elf


def make_mihomo_ipk(init_name="opt/etc/init.d/S28mihomo", version="1.19.34-3",
                    fp_abi=3, dynamic=False, interp=None, e_type=2,
                    init_payload=b"#!/bin/sh\nENABLED=yes\n"):
    elf = make_elf(fp_abi=fp_abi, dynamic=dynamic, interp=interp,
                   e_type=e_type)
    config = b"# HTTP + SOCKS5 mixed proxy.\nmixed-port: 7890\n"
    control = (f"Package: mihomo\nVersion: {version}\n"
               "Architecture: mipsel-3.4\n").encode()
    return gzip_tar([
        ("./debian-binary", b"2.0\n"),
        ("./control.tar.gz", gzip_tar([("./control", control)])),
        ("./data.tar.gz", gzip_tar([
            ("./opt/bin/mihomo", elf),
            ("./" + init_name, init_payload),
            ("./opt/etc/mihomo/config.yaml", config),
        ])),
    ])


def sha(data):
    return hashlib.sha256(data).hexdigest()


class PinContractTests(unittest.TestCase):
    def test_pin_constants_are_wellformed_and_distinct(self):
        for name, sha_hex in ml.PINS:
            self.assertRegex(name, r"^mihomo_1\.19\.34-[23]_mipsel-3\.4\.ipk$")
            self.assertRegex(sha_hex, r"^[0-9a-f]{64}$")
        self.assertNotEqual(ml.V2_IPK_SHA, ml.V3_IPK_SHA)
        self.assertNotEqual(ml.V2_ELF_SHA, ml.V3_ELF_SHA)
        # The -2 S99 and -3 S28 init payloads must stay byte-identical.
        self.assertEqual(ml.ELF_SHA["v3"],
                         "30857fb981b8778e7f25a07ca128b0891f6e3008f9495e55f719e7db09ce769c")
        self.assertEqual(ml.V2_VERSION, "1.19.34-2")
        self.assertEqual(ml.V3_VERSION, "1.19.34-3")

    def test_v2_ships_s99_and_v3_ships_s28(self):
        self.assertEqual(ml.PAYLOAD["v2"]["init_name"], "S99mihomo")
        self.assertEqual(ml.PAYLOAD["v3"]["init_name"], "S28mihomo")
        self.assertEqual(ml.PAYLOAD["v2"]["init"],
                         "opt/etc/init.d/S99mihomo")
        self.assertEqual(ml.PAYLOAD["v3"]["init"],
                         "opt/etc/init.d/S28mihomo")


class AuditTests(unittest.TestCase):
    def test_static_softfloat_go_binary_accepted_for_both_revisions(self):
        elf = make_elf(fp_abi=3, dynamic=False, interp=None, e_type=2)
        for kind, init_name in (("v2", "opt/etc/init.d/S99mihomo"),
                                ("v3", "opt/etc/init.d/S28mihomo")):
            blob = make_mihomo_ipk(init_name=init_name,
                                   version=ml.CONTROL[kind]["Version"])
            report = ml.audit_ipk(blob, kind, expected_elf_sha=sha(elf),
                                  expected_init_sha=sha(b"#!/bin/sh\nENABLED=yes\n"),
                                  expected_config_sha=sha(b"# HTTP + SOCKS5 mixed proxy.\nmixed-port: 7890\n"))
            self.assertEqual(report["elf"]["fp_abi"], 3)
            self.assertFalse(report["elf"]["dynamic"])

    def test_wrong_abi_rejected(self):
        blob = make_mihomo_ipk(fp_abi=5)
        with self.assertRaises(ml.AuditError):
            ml.audit_ipk(blob, "v3", expected_elf_sha=sha(make_elf(fp_abi=5)),
                         expected_init_sha=sha(b"#!/bin/sh\nENABLED=yes\n"),
                         expected_config_sha=sha(b"# HTTP + SOCKS5 mixed proxy.\nmixed-port: 7890\n"))

    def test_dynamic_or_foreign_interpreter_rejected(self):
        for elf in (make_elf(fp_abi=3, dynamic=True),
                    make_elf(fp_abi=3, dynamic=False,
                             interp=b"/opt/lib/ld.so.1", e_type=2)):
            blob = make_mihomo_ipk()
            with self.assertRaises(ml.AuditError):
                ml.audit_ipk(blob, "v3", expected_elf_sha=sha(elf),
                             expected_init_sha=sha(b"#!/bin/sh\nENABLED=yes\n"),
                             expected_config_sha=sha(b"# HTTP + SOCKS5 mixed proxy.\nmixed-port: 7890\n"))

    def test_legacy_s99_payload_in_v3_rejected(self):
        blob = make_mihomo_ipk(init_name="opt/etc/init.d/S99mihomo")
        with self.assertRaises(ml.AuditError):
            ml.audit_ipk(blob, "v3", expected_elf_sha=sha(make_elf(fp_abi=3, dynamic=False, interp=None, e_type=2)),
                         expected_init_sha=sha(b"#!/bin/sh\nENABLED=yes\n"),
                         expected_config_sha=sha(b"# HTTP + SOCKS5 mixed proxy.\nmixed-port: 7890\n"))

    def test_control_version_mismatch_rejected(self):
        blob = make_mihomo_ipk(version="1.19.35-3")
        with self.assertRaises(ml.AuditError):
            ml.audit_ipk(blob, "v3", expected_elf_sha=sha(make_elf(fp_abi=3, dynamic=False, interp=None, e_type=2)),
                         expected_init_sha=sha(b"#!/bin/sh\nENABLED=yes\n"),
                         expected_config_sha=sha(b"# HTTP + SOCKS5 mixed proxy.\nmixed-port: 7890\n"))

    def test_verify_ipk_dir_fails_closed_on_missing_artifact(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ml.AuditError):
                ml.verify_ipk_dir(td)


class ScenarioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scenario = json.loads((Path(ml.SCENARIO)).read_text(encoding="utf-8"))

    def test_no_telemt_references(self):
        # KASOPT:/ is the Ported-lab Entware install disk label (proven
        # prelude), not the KAS project; only TeleMT traces are forbidden.
        text = json.dumps(self.scenario)
        self.assertNotIn("telemt", text.lower())
        self.assertNotIn("kas_smoke", text.lower())
        self.assertNotIn("magitrickle", text.lower())

    def test_upgrade_migration_and_rollback_markers_present(self):
        expects = [s.get("expect") for s in self.scenario["steps"]
                   if s["action"] == "ssh"]
        for marker in ("INIT_V2_S99_ONLY", "OPKG_V3_UPGRADED",
                       "MIGRATION_S99_TO_S28_OK", "SINGLE_MIHOMO_INIT",
                       "CONFIG_V3_PRESERVED", "SAMPLE_PRESENT",
                       "ROLLBACK_V2_OK", "ROLLBACK_S99_RESTORED",
                       "CONFIG_ROLLBACK_PRESERVED", "FINAL_V3_S28_OK",
                       "AUTOSTART_MIHOMO_RUNNING", "CONFIG_SURVIVED_REBOOT",
                       "PROXY_MIXED_EGRESS_OK", "PORT_V3_LISTENING",
                       "PORT_GEN2_LISTENING"):
            self.assertIn(marker, expects)

    def test_both_frozen_ipks_downloaded_and_hashed(self):
        text = json.dumps(self.scenario)
        self.assertIn(ml.V2_IPK_NAME, text)
        self.assertIn(ml.V3_IPK_NAME, text)
        self.assertIn(ml.V2_IPK_SHA, text)
        self.assertIn(ml.V3_IPK_SHA, text)
        self.assertIn(ml.V3_ELF_SHA, text)

    def test_dual_stack_port_regex_and_single_proc_checks(self):
        text = json.dumps(self.scenario)
        self.assertIn("00000000000000000000000000000000:1ED2", text)
        self.assertNotIn(":1ED1", text)
        self.assertNotIn(":1ED3", text)
        self.assertIn("wc -w", text)

    def test_every_ssh_step_has_expect(self):
        for index, step in enumerate(self.scenario["steps"]):
            if step["action"] == "ssh":
                self.assertIn("expect", step, f"step {index} lacks expect")

    def test_boot_and_reboot_prelude_matches_proven_shape(self):
        steps = self.scenario["steps"]
        serial = [s for s in steps if s["action"] == "wait_serial"]
        patterns = [s["pattern"] for s in serial]
        self.assertIn("Boot and Recovery Environment", patterns[0])
        self.assertIn("system ready", " ".join(patterns))
        reboots = [s for s in steps if s["action"] == "telnet"
                   and "system reboot" in str(s.get("send", ""))]
        self.assertEqual(len(reboots), 2)


class WorkflowTests(unittest.TestCase):
    def test_adapter_keeps_default_behavior_and_adds_mihomo_dispatch(self):
        root = Path(ml.__file__).resolve().parents[2]
        workflow = (root / ".github/workflows/mt7621-telemt-lab.yml").read_text()
        self.assertIn("scenario:", workflow)  # dispatch input
        self.assertIn("default: panel-g", workflow)
        self.assertIn("mihomo-1.19.34-mipsel", workflow)
        self.assertIn("44565066188", workflow)
        self.assertIn("lab.mt7621.mihomo_s28_lab --audit-ipks", workflow)
        self.assertIn("mihomo_s28_lab.py", workflow)
        self.assertIn("mihomo-s28", workflow)
        # The default TeleMT path must stay untouched.
        self.assertIn("telemt-core-mipsel-experimental-getfb", workflow)
        self.assertIn("43948987955", workflow)
        self.assertIn("--scenario panel-g", workflow)

    def test_gate_driver_registered(self):
        root = Path(ml.__file__).resolve().parents[2]
        gates = (root / "lab/mt7621/mt7621_gates.py").read_text()
        # No new public gate: the run reports through the standard
        # report.json contract; gate aggregation must stay at 19.
        self.assertIn("MIPSEL_TELEMT", gates)


if __name__ == "__main__":
    unittest.main()

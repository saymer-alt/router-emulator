"""Negative and contract tests for the TeleMT MIPSel pinned-IPK guest lab.

No guest execution here: the host auditor is exercised on synthetic crafted
IPK/ELF fixtures, and the committed scenario/fixtures/gate are validated
against the proof requirements (verbatim baseline, in-guest pins, sourcing
safety, UART marker synchronization, self-match-safe /proc scans).
"""
import base64
import hashlib
import io
import json
from pathlib import Path
import re
import struct
import tarfile
import unittest

from lab.mt7621 import mt7621_gates as mg
from lab.mt7621 import telemt_lab as tl


def row(action, outcome="PASS"):
    return {"action": action, "outcome": outcome, "elapsed_seconds": 0.1}


def scenario(name="s", steps=()):
    return {"name": name, "steps": list(steps)}


BOOT_STEPS = (
    {"action": "wait_serial", "pattern": "Boot and Recovery Environment"},
    {"action": "wait_serial", "pattern": "Starting kernel"},
    {"action": "wait_serial", "pattern": "system ready"},
)


def gzip_tar(members):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, data in members:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def make_elf(fp_abi=3, machine=8, dynamic=True, interp=b"/opt/lib/ld.so.1",
             e_type=3):
    parts = [b"\x7fELF\x01\x01\x01\x00" + b"\x00" * 8]
    parts.append(struct.pack("<HHI", e_type, machine, 1))          # type/mach/ver
    phnum = 1 + (1 if interp else 0) + (1 if dynamic else 0)
    phoff = 52
    parts.append(struct.pack("<III", 0, phoff, 0))                 # entry/phoff/shoff
    parts.append(struct.pack("<I", 0))                             # flags
    parts.append(struct.pack("<HHH", 52, 32, phnum))               # ehsize/phentsize/phnum
    parts.append(struct.pack("<HHH", 32, 0, 0))                    # shentsize/shnum/shstrndx
    header = b"".join(parts)
    payload = b"A" * 64
    table = b""
    # MIPS .MIPS.abiflags (PT_MIPS_ABIFLAGS, 24 bytes): fp_abi lives at
    # byte offset 7, exactly where the auditor and readelf read it.
    abiflags = bytes([0x00, 0x00, 0x20, 0x00, 0x01, 0x01, 0x00, fp_abi,
                      0x00]) + b"\x00" * 4 + bytes([0x00, 0x00]) + b"\x00" * 9
    table += struct.pack("<IIIIIIII", 0x70000003, 52 + 32 * phnum,
                         0, 0, 24, 24, 4, 4)
    segments = [abiflags]
    offset = 52 + 32 * phnum + 24
    if interp:
        table += struct.pack("<IIIIIIII", 3, offset, 0, 0, len(interp) + 1,
                             len(interp) + 1, 4, 1)
        segments.append(interp + b"\x00")
        offset += len(interp) + 1
    if dynamic:
        table += struct.pack("<IIIIIIII", 2, offset, 0, 0, 16, 16, 6, 8)
        segments.append(b"\x00" * 16)
    blob = header + table + b"".join(segments) + payload
    return blob


def make_ipk(binary, control_text=b"Package: telemt\nVersion: 3.5.17-1\n"
             b"Architecture: mipsel-3.4\n", payload_path="./opt/bin/telemt"):
    outer = gzip_tar([
        ("./debian-binary", b"2.0\n"),
        ("./control.tar.gz", gzip_tar([("./control", control_text)])),
        ("./data.tar.gz", gzip_tar([(payload_path, binary)])),
    ])
    return outer


class AuditorPositiveTests(unittest.TestCase):
    def test_synthetic_core_ipk_passes_audit(self):
        elf = make_elf()
        digest = hashlib.sha256(elf).hexdigest()
        report = tl.audit_ipk(make_ipk(elf), "core", expected_elf_sha=digest)
        self.assertEqual(report["elf"]["fp_abi"], 3)
        self.assertEqual(report["elf"]["interp"], "/opt/lib/ld.so.1")
        self.assertTrue(report["elf"]["dynamic"])

    def test_synthetic_static_panel_ipk_passes_audit(self):
        elf = make_elf(dynamic=False, interp=None, e_type=2)
        digest = hashlib.sha256(elf).hexdigest()
        panel_control = (b"Package: telemt-panel-lite\n"
                         b"Version: 1.0.0~rc.4-1\nArchitecture: mipsel-3.4\n")
        report = tl.audit_ipk(
            make_ipk(elf, panel_control, payload_path="./opt/bin/telemt-panel"),
            "panel", expected_elf_sha=digest)
        self.assertFalse(report["elf"]["dynamic"])
        self.assertIsNone(report["elf"]["interp"])
        self.assertEqual(report["elf"]["e_type"], 2)


class AuditorNegativeTests(unittest.TestCase):
    def test_wrong_fp_abi_is_rejected(self):
        elf = make_elf(fp_abi=5)
        with self.assertRaises(tl.AuditError):
            tl.audit_ipk(make_ipk(elf), "core", expected_elf_sha="0" * 64)

    def test_non_mips_machine_is_rejected(self):
        elf = make_elf(machine=0x3E)
        with self.assertRaises(tl.AuditError):
            tl.audit_ipk(make_ipk(elf), "core", expected_elf_sha="0" * 64)

    def test_core_without_entware_interpreter_is_rejected(self):
        elf = make_elf(interp=None)
        with self.assertRaises(tl.AuditError):
            tl.audit_ipk(make_ipk(elf), "core", expected_elf_sha="0" * 64)

    def test_dynamic_panel_is_rejected(self):
        elf = make_elf(dynamic=True, e_type=2)
        control = (b"Package: telemt-panel-lite\nVersion: 1.0.0~rc.4-1\n"
                   b"Architecture: mipsel-3.4\n")
        with self.assertRaises(tl.AuditError):
            tl.audit_ipk(make_ipk(elf, control), "panel",
                         expected_elf_sha="0" * 64)

    def test_wrong_control_fields_are_rejected(self):
        elf = make_elf()
        control = b"Package: telemt\nVersion: 9.9.9\nArchitecture: mipsel-3.4\n"
        with self.assertRaises(tl.AuditError):
            tl.audit_ipk(make_ipk(elf, control), "core",
                         expected_elf_sha="0" * 64)

    def test_extra_payload_member_is_rejected(self):
        elf = make_elf()
        blob = gzip_tar([
            ("./debian-binary", b"2.0\n"),
            ("./control.tar.gz", gzip_tar([("./control", b"Package: telemt\n"
                                            b"Version: 3.5.17-1\n"
                                            b"Architecture: mipsel-3.4\n")])),
            ("./data.tar.gz", gzip_tar([("./opt/bin/telemt", elf),
                                        ("./opt/etc/extra.conf", b"x")])),
        ])
        with self.assertRaises(tl.AuditError):
            tl.audit_ipk(blob, "core", expected_elf_sha="0" * 64)

    def test_unsafe_tar_path_is_rejected(self):
        elf = make_elf()
        blob = gzip_tar([
            ("./debian-binary", b"2.0\n"),
            ("./control.tar.gz", gzip_tar([("./control", b"Package: telemt\n"
                                            b"Version: 3.5.17-1\n"
                                            b"Architecture: mipsel-3.4\n")])),
            ("./data.tar.gz", gzip_tar([("../escape", elf)])),
        ])
        with self.assertRaises(tl.AuditError):
            tl.audit_ipk(blob, "core", expected_elf_sha="0" * 64)

    def test_missing_elf_sha_pin_is_rejected(self):
        elf = make_elf()
        with self.assertRaises(tl.AuditError):
            tl.audit_ipk(make_ipk(elf), "core", expected_elf_sha="f" * 64)

    def test_verify_ipk_dir_requires_both_pinned_artifacts(self):
        with self.assertRaises(tl.AuditError):
            tl.verify_ipk_dir("/nonexistent-telemt-ipks")


class CommittedFixtureContractTests(unittest.TestCase):
    @staticmethod
    def fixtures():
        root = Path(__file__).resolve().parents[1] / "lab" / "mt7621" / "fixtures"
        return (root / "S99telemtlab").read_bytes(), \
            (root / "telemt-lab.toml").read_bytes(), \
            (root / "broken-telemt.toml").read_bytes()

    def test_wrapper_is_sourcing_safe_and_self_identifying(self):
        fixture, _, _ = self.fixtures()
        self.assertTrue(fixture.startswith(b"#!/bin/sh"))
        # rc.unslung (opt-ndmsv2 1.0-17) SOURCES every executable S* script in
        # one shell: no shell-option changes, no exit, wrapped main call.
        self.assertNotIn(b"set -e", fixture)
        self.assertNotIn(b"set -u", fixture)
        self.assertNotIn(b"exit ", fixture)
        self.assertIn(b'telemtlab_main "$@"', fixture)
        # Liveness proof is the /proc/<pid>/exe identity, not a pidfile echo.
        self.assertIn(b"readlink \"/proc/$1/exe\"", fixture)
        self.assertIn(b"grep -q '^/opt/bin/telemt$'", fixture)
        # Persistent boot-generation marker drives the UART autostart proof.
        self.assertIn(b"telemt-lab.gen", fixture)
        self.assertIn(b"telemtlab-start gen=$gen pid=$p", fixture)
        self.assertIn(b"> /dev/console", fixture)
        # Loopback-only daemon start with the synthetic lab configuration.
        self.assertIn(b"--pid-file \"$pidfile\" \"$conf\"", fixture)
        self.assertNotIn(b"curl", fixture)
        self.assertNotIn(b"wget", fixture)

    def test_config_is_loopback_only_synthetic(self):
        _, config, _ = self.fixtures()
        self.assertIn(b"use_middle_proxy = false", config)
        self.assertIn(b"tls = false", config)
        self.assertIn(b'ip = "127.0.0.1"', config)
        self.assertIn(b'listen = "127.0.0.1:9091"', config)
        self.assertIn(b'whitelist = ["127.0.0.1/32"]', config)
        self.assertIn(b'lab = "00000000000000000000000000000000"', config)
        self.assertNotIn(b"petrovich", config)

    def test_broken_toml_is_the_pr45_payload(self):
        _, _, broken = self.fixtures()
        self.assertEqual(broken.replace(b"\r\n", b"\n"),
                         b"[broken-toml-table\n")


class CommittedScenarioContractTests(unittest.TestCase):
    @staticmethod
    def inputs():
        from lab import run as runner
        folder = Path(__file__).resolve().parents[1] / "lab" / "scenarios"
        baseline = runner.load_scenario(
            str(folder / "ported-mt7621-entware-tz-skip.json"))
        stage = runner.load_scenario(str(folder / "ported-mt7621-telemt.json"))
        return baseline, stage

    @staticmethod
    def report(data, failed=None, outcome=None):
        rows = [row(s["action"]) for s in data["steps"]]
        if failed is not None:
            rows = rows[:failed + 1]
            rows[-1]["outcome"] = "FAIL"
        return {"scenario": data["name"],
                "outcome": outcome or ("FAIL" if failed is not None else "PASS"),
                "steps": rows}

    def test_baseline_is_verbatim_and_chain_is_complete(self):
        baseline, stage = self.inputs()
        self.assertEqual(len(baseline["steps"]), 33)
        self.assertEqual(len(stage["steps"]), 72)
        self.assertEqual(stage["steps"][:33], baseline["steps"][:33])

    def test_seed_step_carries_all_three_fixture_payloads(self):
        _, stage = self.inputs()
        seed = stage["steps"][34]["send"]
        self.assertIn("base64 -d > /opt/etc/init.d/S99telemtlab", seed)
        self.assertIn("base64 -d > /opt/etc/telemt-lab.toml", seed)
        self.assertIn("base64 -d > /opt/lab/broken-telemt.toml", seed)
        self.assertIn("chmod 700 /opt/etc/init.d/S99telemtlab", seed)
        encoded = re.findall(r"printf '%s' '([A-Za-z0-9+/=]+)'", seed)
        self.assertEqual(len(encoded), 3)
        fixtures = CommittedFixtureContractTests.fixtures()
        for chunk, fixture in zip(encoded, fixtures):
            self.assertEqual(base64.b64decode(chunk, validate=True),
                             fixture.replace(b"\r\n", b"\n"))

    def test_delivery_pins_both_frozen_artifacts_in_guest(self):
        _, stage = self.inputs()
        send = stage["steps"][35]["send"]
        self.assertIn("http://192.168.1.100/entware/telemt_3.5.17-1_mipsel-3.4.ipk",
                      send)
        self.assertIn("http://192.168.1.100/entware/"
                      "telemt-panel-lite_1.0.0.rc.4-1_mipsel-3.4.ipk", send)
        self.assertIn(tl.CORE_IPK_SHA, send)
        self.assertIn(tl.PANEL_IPK_SHA, send)
        self.assertIn("sha256sum -c expected.sha", send)
        # These two pins must be emitted by ONE printf redirected to the
        # manifest, not by two commands where only the second is redirected.
        self.assertIn(
            "printf '%s  %s\\n' '" + tl.CORE_IPK_SHA +
            "' telemt.ipk '" + tl.PANEL_IPK_SHA +
            "' telemt-panel.ipk > /opt/lab/expected.sha", send)
        self.assertEqual(send.count("printf '%s  %s\\n'"), 1)

    def test_opkg_and_elf_pin_steps_are_native_and_pinned(self):
        _, stage = self.inputs()
        core = stage["steps"][36]["send"]
        panel = stage["steps"][37]["send"]
        self.assertIn("/opt/bin/opkg install /opt/lab/telemt.ipk", core)
        self.assertIn("Version: 3.5.17-1", core)
        self.assertIn("/opt/bin/opkg install /opt/lab/telemt-panel.ipk", panel)
        self.assertIn("Version: 1.0.0~rc.4-1", panel)
        pinned = stage["steps"][38]["send"]
        self.assertIn(tl.CORE_ELF_SHA, pinned)
        self.assertIn(tl.PANEL_ELF_SHA, pinned)
        self.assertIn(
            "printf '%s  %s\\n' '" + tl.CORE_ELF_SHA +
            "' /opt/bin/telemt '" + tl.PANEL_ELF_SHA +
            "' /opt/bin/telemt-panel > /opt/lab/elf.sha", pinned)
        self.assertEqual(pinned.count("printf '%s  %s\\n'"), 1)
        self.assertIn("sha256sum -c /opt/lab/elf.sha", pinned)

    def test_negative_cli_probes_check_exit_codes_and_exact_text(self):
        _, stage = self.inputs()
        cli = stage["steps"][39]["send"]
        self.assertIn("healthcheck --mode=lab-probe-invalid", cli)
        self.assertIn('[ "$rc" = 2 ]', cli)
        self.assertEqual(stage["steps"][40]["expect"],
                         "Invalid --mode value 'lab-probe-invalid' "
                         "\\(supported: liveness, ready\\)")
        toml = stage["steps"][41]["send"]
        self.assertIn("telemt run /opt/lab/broken-telemt.toml", toml)
        self.assertIn('[ "$rc" = 1 ]', toml)
        self.assertIn("grep -q 'unclosed table'", toml)
        self.assertIn("grep -qi 'panicked'", toml)

    def test_daemon_liveness_and_log_emit_steps_precede_the_health_probe(self):
        _, stage = self.inputs()
        alive = stage["steps"][43]["send"]
        self.assertIn("cat /opt/var/run/telemt-lab.pid", alive)
        self.assertIn("[ -r /proc/$pid/cmdline ]", alive)
        self.assertEqual(stage["steps"][43]["expect"], "DAEMON_PROC_ALIVE")
        emit = stage["steps"][44]["send"]
        # The daemon log goes to the private UART for post-mortem diagnosis;
        # the step passes whether or not the log exists.
        self.assertIn("cat /opt/var/log/telemt-lab.log > /dev/console", emit)
        self.assertEqual(stage["steps"][44]["expect"], "LOG_(EMITTED|ABSENT)")

    def test_proc_scan_cannot_match_its_own_command_line(self):
        _, stage = self.inputs()
        scan = stage["steps"][67]["send"]
        # The concatenation-built needle cannot appear contiguously in the
        # scanner's own command text (guest runs each SSH send via sh -c).
        self.assertIn('"/opt/etc/telemt-lab.""toml"', scan)
        self.assertNotIn("telemt-lab.toml", scan)
        self.assertIn("[ \"$n\" = 1 ]", scan)

    def test_identity_proofs_use_proc_exe_not_pidfile_echoes(self):
        _, stage = self.inputs()
        for index in (46, 66):
            send = stage["steps"][index]["send"]
            self.assertIn("readlink /proc/$pid/exe", send)
            self.assertIn("/proc/$pid/cmdline", send)
            self.assertIn("cat /opt/var/run/telemt-lab.pid", send)

    def test_boot_chain_has_exactly_two_native_reboots_and_one_marker(self):
        _, stage = self.inputs()
        reboots = [s for s in stage["steps"]
                   if s.get("action") == "wait_serial"
                   and s.get("pattern") == "reboot: Restarting system"]
        self.assertEqual(len(reboots), 2)
        resets = [s for s in stage["steps"] if s.get("action") == "reset"]
        self.assertEqual(resets, [])
        markers = [s for s in stage["steps"]
                   if s.get("action") == "wait_serial"
                   and s.get("pattern") == "telemtlab-start gen=2 pid=[0-9]+"]
        self.assertEqual(len(markers), 1)
        # The autostart SSH proofs come only after the UART marker (ndm runs
        # rc.unslung asynchronously after the disk mounts).
        index_marker = next(i for i, s in enumerate(stage["steps"])
                            if s.get("pattern") == "telemtlab-start gen=2 pid=[0-9]+")
        index_health = next(i for i, s in enumerate(stage["steps"])
                            if s.get("expect") == "AUTOSTART_HEALTH_OK")
        self.assertLess(index_marker, index_health)


class TelemtGateContractTests(unittest.TestCase):
    @staticmethod
    def inputs():
        return CommittedScenarioContractTests.inputs()

    report = staticmethod(CommittedScenarioContractTests.report)

    def test_full_pass_report_derives_pass(self):
        _, s = self.inputs()
        gate = mg.derive_telemt_gate(s, self.report(s))["MIPSEL_TELEMT"]
        self.assertEqual(gate["status"], "PASS")
        self.assertEqual(gate["reason"],
                         "opkg_install_native_reboot_then_rc_unslung_autostart")

    def test_wrong_scenario_name_is_fail_not_pass(self):
        _, s = self.inputs()
        report = self.report(s)
        report["scenario"] = "some other scenario"
        gate = mg.derive_telemt_gate(s, report)["MIPSEL_TELEMT"]
        self.assertEqual(gate["status"], "FAIL")
        self.assertEqual(gate["reason"], "report_scenario_mismatch")

    def test_chain_step_removal_is_not_tested(self):
        _, s = self.inputs()
        trimmed = {"name": s["name"],
                   "steps": [x for x in s["steps"]
                             if x.get("expect") != "PERSISTED_FILES"]}
        gate = mg.derive_telemt_gate(trimmed, self.report(trimmed))
        self.assertEqual(gate["MIPSEL_TELEMT"]["status"], "NOT_TESTED")
        self.assertEqual(gate["MIPSEL_TELEMT"]["reason"],
                         "scenario_lacks_telemt_chain")

    def test_reordered_chain_is_not_tested(self):
        _, s = self.inputs()
        steps = list(s["steps"])
        index_down = next(i for i, x in enumerate(steps)
                          if x.get("expect") == "HEALTH_DOWN_AFTER_STOP")
        index_off = next(i for i, x in enumerate(steps)
                         if x.get("expect") == "TELEMT_OFF_PREBOOT")
        steps[index_down], steps[index_off] = steps[index_off], steps[index_down]
        swapped = {"name": s["name"], "steps": steps}
        gate = mg.derive_telemt_gate(swapped, self.report(swapped))
        self.assertEqual(gate["MIPSEL_TELEMT"]["status"], "NOT_TESTED")
        self.assertEqual(gate["MIPSEL_TELEMT"]["reason"],
                         "telemt_chain_not_ordered")

    def test_chain_failure_is_fail(self):
        _, s = self.inputs()
        gate = mg.derive_telemt_gate(
            s, self.report(s, failed=47))["MIPSEL_TELEMT"]
        self.assertEqual(gate["status"], "FAIL")
        self.assertEqual(gate["reason"], "telemt_chain_step_failed")

    def test_baseline_failure_is_not_tested(self):
        _, s = self.inputs()
        gate = mg.derive_telemt_gate(
            s, self.report(s, failed=10))["MIPSEL_TELEMT"]
        self.assertEqual(gate["status"], "NOT_TESTED")
        self.assertEqual(gate["reason"], "prerequisite_failed")

    def test_all_pass_rows_with_nonpass_report_is_fail(self):
        _, s = self.inputs()
        gate = mg.derive_telemt_gate(
            s, self.report(s, outcome="BLOCKED"))["MIPSEL_TELEMT"]
        self.assertEqual(gate["status"], "FAIL")
        self.assertEqual(gate["reason"], "report_outcome_mismatch")

    def test_telemt_gate_stays_outside_the_19_core_gates(self):
        self.assertNotIn("MIPSEL_TELEMT", mg.GATES)
        self.assertNotIn("MIPSEL_TELEMT", mg.NATIVE_GATES)
        with self.assertRaises(ValueError):
            mg.apply_labels({g: mg._gate("NOT_TESTED", "unattempted")
                             for g in mg.GATES},
                            [("MIPSEL_TELEMT", "PASS")])

    def test_telemt_report_adds_a_gate_without_changing_the_19(self):
        _, s = self.inputs()
        openwrt = scenario(steps=BOOT_STEPS)
        boot_report = {"scenario": openwrt["name"], "outcome": "PASS",
                       "steps": [row(x["action"]) for x in openwrt["steps"]]}
        without = mg.evaluate(openwrt, boot_report)
        self.assertEqual(len(without), 19)
        self.assertNotIn("MIPSEL_TELEMT", without)
        gates = mg.evaluate(openwrt, boot_report,
                            telemt_scenario=s,
                            telemt_report=self.report(s))
        self.assertEqual(len(gates), 20)
        self.assertEqual(gates["MIPSEL_TELEMT"]["status"], "PASS")
        self.assertEqual(gates["MIPSEL_TELEMT"]["reason"],
                         "opkg_install_native_reboot_then_rc_unslung_autostart")


if __name__ == "__main__":
    unittest.main()

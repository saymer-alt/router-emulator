"""Negative and derivation tests for the MT7621 gate aggregator.

Native gate statuses must come from scenario reports, never from
controller labels.  These tests are pure (no QEMU, no fixtures on disk).
"""
from collections import Counter
import unittest

from lab.mt7621 import mt7621_gates as mg


def row(action, outcome="PASS"):
    return {"action": action, "outcome": outcome, "elapsed_seconds": 0.1}


def scenario(name="s", steps=()):
    return {"name": name, "steps": list(steps)}


BOOT_STEPS = (
    {"action": "wait_serial", "pattern": "Boot and Recovery Environment"},
    {"action": "wait_serial", "pattern": "Starting kernel"},
    {"action": "wait_serial", "pattern": "Linux version"},
    {"action": "send_serial", "text": "uname -m", "enter": True},
    {"action": "wait_serial", "pattern": "mips"},
    {"action": "send_serial", "text": "/bin/busybox uname -m", "enter": True},
    {"action": "wait_serial", "pattern": "mips"},
    {"action": "reset"},
    {"action": "wait_serial", "pattern": "Boot and Recovery Environment"},
    {"action": "wait_serial", "pattern": "Linux version"},
)


class DerivationTests(unittest.TestCase):
    @staticmethod
    def report(rows, name="s", outcome="PASS"):
        return {"scenario": name, "outcome": outcome, "steps": rows}

    def test_full_run_passes_all_three_native_gates(self):
        rep = self.report([row(s["action"]) for s in BOOT_STEPS])
        gates = mg.derive_openwrt_gates(scenario(steps=BOOT_STEPS), rep)
        self.assertEqual(gates["MT7621_OPENWRT_BOOT"]["status"], "PASS")
        self.assertEqual(gates["MT7621_REBOOT"]["status"], "PASS")
        self.assertEqual(gates["MT7621_REBOOT"]["reason"],
                         "reset_then_second_kernel_boot_observed")
        self.assertEqual(gates["MIPSEL_NATIVE_ELF"]["status"], "PASS")
        self.assertEqual(gates["MIPSEL_NATIVE_ELF"]["reason"],
                         "busybox_uname_answered_mips_in_guest")

    def test_reboot_requires_reset_step_in_scenario(self):
        steps = [s for s in BOOT_STEPS if s["action"] != "reset"]
        rep = self.report([row(s["action"]) for s in steps])
        gates = mg.derive_openwrt_gates(scenario(steps=steps), rep)
        self.assertEqual(gates["MT7621_REBOOT"]["status"], "NOT_TESTED")
        self.assertEqual(gates["MT7621_REBOOT"]["reason"],
                         "scenario_has_no_reset_step")

    def test_reboot_requires_post_reset_second_boot(self):
        rows = [row(s["action"]) for s in BOOT_STEPS]
        rows[-1]["outcome"] = "FAIL"  # second-boot "Linux version" timed out
        rep = self.report(rows, outcome="FAIL")
        gates = mg.derive_openwrt_gates(scenario(steps=BOOT_STEPS), rep)
        self.assertEqual(gates["MT7621_REBOOT"]["status"], "FAIL")
        self.assertEqual(gates["MT7621_REBOOT"]["reason"],
                         "second_boot_marker_timeout")
        # the first boot itself was fully observed before the reset
        self.assertEqual(gates["MT7621_OPENWRT_BOOT"]["status"], "PASS")

    def test_reset_alone_is_not_a_reboot(self):
        steps = BOOT_STEPS[:8]  # scenario stops right after the reset
        rep = self.report([row(s["action"]) for s in steps])
        gates = mg.derive_openwrt_gates(scenario(steps=steps), rep)
        self.assertEqual(gates["MT7621_REBOOT"]["status"], "NOT_TESTED")
        self.assertEqual(gates["MT7621_REBOOT"]["reason"],
                         "scenario_has_no_second_boot_step")

    def test_failure_before_reset_keeps_reboot_untested(self):
        rows = [row(s["action"]) for s in BOOT_STEPS[:5]]
        rows[-1]["outcome"] = "FAIL"
        rep = self.report(rows, outcome="FAIL")
        gates = mg.derive_openwrt_gates(scenario(steps=BOOT_STEPS), rep)
        self.assertEqual(gates["MT7621_OPENWRT_BOOT"]["status"], "FAIL")
        self.assertEqual(gates["MT7621_REBOOT"]["status"], "NOT_TESTED")
        self.assertEqual(gates["MT7621_REBOOT"]["reason"], "reset_not_reached")
        self.assertEqual(gates["MIPSEL_NATIVE_ELF"]["status"], "NOT_TESTED")
        self.assertEqual(gates["MIPSEL_NATIVE_ELF"]["reason"],
                         "elf_step_not_reached")

    def test_native_elf_requires_explicit_execution_not_magic_bytes(self):
        steps = list(BOOT_STEPS[:5]) + [
            {"action": "send_serial", "text": "head -c 4 /bin/busybox", "enter": True},
            {"action": "wait_serial", "pattern": "\x7fELF"},
        ] + list(BOOT_STEPS[7:])
        rep = self.report([row(s["action"]) for s in steps])
        gates = mg.derive_openwrt_gates(scenario(steps=steps), rep)
        self.assertEqual(gates["MIPSEL_NATIVE_ELF"]["status"], "NOT_TESTED")
        self.assertEqual(gates["MIPSEL_NATIVE_ELF"]["reason"],
                         "scenario_lacks_explicit_elf_execution")

    def test_native_elf_echo_trap_refused(self):
        # The waited marker occurs in the sent command line, so the UART
        # echo of the command itself could satisfy the wait: no PASS.
        steps = list(BOOT_STEPS[:5]) + [
            {"action": "send_serial", "text": "/bin/busybox uname -m; echo mips",
             "enter": True},
            {"action": "wait_serial", "pattern": "mips"},
        ] + list(BOOT_STEPS[7:])
        rep = self.report([row(s["action"]) for s in steps])
        gates = mg.derive_openwrt_gates(scenario(steps=steps), rep)
        self.assertEqual(gates["MIPSEL_NATIVE_ELF"]["status"], "NOT_TESTED")
        self.assertEqual(gates["MIPSEL_NATIVE_ELF"]["reason"],
                         "elf_marker_matches_uart_echo")

    def test_native_elf_answer_timeout_fails(self):
        rows = [row(s["action"]) for s in BOOT_STEPS]
        rows[6]["outcome"] = "FAIL"  # the busybox answer wait timed out
        rep = self.report(rows, outcome="FAIL")
        gates = mg.derive_openwrt_gates(scenario(steps=BOOT_STEPS), rep)
        self.assertEqual(gates["MIPSEL_NATIVE_ELF"]["status"], "FAIL")
        self.assertEqual(gates["MIPSEL_NATIVE_ELF"]["reason"],
                         "elf_answer_marker_timeout")

    def test_report_of_another_scenario_is_refused(self):
        rep = self.report([row(s["action"]) for s in BOOT_STEPS], name="other")
        gates = mg.derive_openwrt_gates(scenario(steps=BOOT_STEPS), rep)
        self.assertEqual(gates["MT7621_OPENWRT_BOOT"]["status"], "FAIL")
        self.assertEqual(gates["MT7621_OPENWRT_BOOT"]["reason"],
                         "report_scenario_mismatch")
        self.assertEqual(gates["MT7621_REBOOT"]["status"], "NOT_TESTED")

    def test_truncated_all_pass_report_is_refused(self):
        rows = [row(s["action"]) for s in BOOT_STEPS[:5]]
        rep = self.report(rows)  # full-PASS prefix is not producible by run.py
        gates = mg.derive_openwrt_gates(scenario(steps=BOOT_STEPS), rep)
        self.assertEqual(gates["MT7621_OPENWRT_BOOT"]["reason"],
                         "report_scenario_mismatch")

    def test_setup_failure_is_reported(self):
        rep = self.report([row("setup", outcome="FAIL")], outcome="FAIL")
        gates = mg.derive_openwrt_gates(scenario(steps=BOOT_STEPS), rep)
        self.assertEqual(gates["MT7621_OPENWRT_BOOT"]["status"], "FAIL")
        self.assertEqual(gates["MT7621_OPENWRT_BOOT"]["reason"], "setup_failed")
        self.assertEqual(gates["MT7621_REBOOT"]["status"], "NOT_TESTED")
        self.assertEqual(gates["MIPSEL_NATIVE_ELF"]["status"], "NOT_TESTED")


class LabelTests(unittest.TestCase):
    def fresh(self):
        return {name: mg._gate("NOT_TESTED", "unattempted") for name in mg.GATES}

    def test_set_pass_refused_for_every_native_gate(self):
        for name in mg.NATIVE_GATES:
            gates = self.fresh()
            with self.assertRaises(ValueError) as cm:
                mg.apply_labels(gates, [(name, "PASS")], {})
            self.assertIn(name, str(cm.exception))
            self.assertEqual(gates[name]["status"], "NOT_TESTED")

    def test_non_pass_labels_for_native_gates_allowed(self):
        gates = mg.apply_labels(self.fresh(),
                                [("PORTED_KERNEL_BOOT", "BLOCKED"),
                                 ("MIPSEL_ENTWARE_READY", "NOT_TESTED"),
                                 ("MIPSEL_PERSISTENCE", "FAIL")],
                                {"PORTED_KERNEL_BOOT": "smp_ipi_hang"})
        self.assertEqual(gates["PORTED_KERNEL_BOOT"]["status"], "BLOCKED")
        self.assertEqual(gates["PORTED_KERNEL_BOOT"]["reason"], "smp_ipi_hang")
        self.assertEqual(gates["MIPSEL_PERSISTENCE"]["status"], "FAIL")

    def test_set_pass_on_host_gate_allowed(self):
        gates = mg.apply_labels(self.fresh(),
                                [("HOST_ISOLATION", "PASS")],
                                {"HOST_ISOLATION": "offline_wan_loopback_only"})
        self.assertEqual(gates["HOST_ISOLATION"]["status"], "PASS")
        self.assertEqual(gates["HOST_ISOLATION"]["reason"],
                         "offline_wan_loopback_only")

    def test_unknown_gate_or_status_refused(self):
        with self.assertRaises(ValueError):
            mg.apply_labels(self.fresh(), [("NOT_A_GATE", "PASS")], {})
        with self.assertRaises(ValueError):
            mg.apply_labels(self.fresh(), [("HOST_ISOLATION", "MAYBE")], {})


class ArithmeticTests(unittest.TestCase):
    def test_golden_thirteen_gate_counts(self):
        labels = [("MT7621_QEMU_BUILD", "PASS"),
                  ("PORTED_IMAGE_VALIDATION", "PASS"),
                  ("PORTED_KERNEL_BOOT", "BLOCKED"),
                  ("PORTED_NDMC_READY", "NOT_TESTED"),
                  ("MIPSEL_ENTWARE_READY", "NOT_TESTED"),
                  ("MIPSEL_SERVICE_RESTART", "NOT_TESTED"),
                  ("MIPSEL_PERSISTENCE", "NOT_TESTED"),
                  ("NEGATIVE_IMAGE_REJECT", "PASS"),
                  ("HOST_ISOLATION", "PASS"),
                  ("CLEANUP", "PASS")]
        rep = {"scenario": "s", "outcome": "PASS",
               "steps": [row(s["action"]) for s in BOOT_STEPS]}
        gates = mg.evaluate(scenario(steps=BOOT_STEPS), rep, labels)
        counts = Counter(v["status"] for v in gates.values())
        self.assertEqual(counts["PASS"], 8)
        self.assertEqual(counts["BLOCKED"], 1)
        self.assertEqual(counts["NOT_TESTED"], 4)
        self.assertEqual(sum(counts.values()), len(mg.GATES))
        self.assertEqual(mg.outcome_of(gates), "BLOCKED")
        # the three derived gates are the expected PASSes
        for name in ("MT7621_OPENWRT_BOOT", "MT7621_REBOOT", "MIPSEL_NATIVE_ELF"):
            self.assertEqual(gates[name]["status"], "PASS", name)


if __name__ == "__main__":
    unittest.main()

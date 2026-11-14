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

PORTED_STEPS = (
    {"action": "wait_serial", "pattern": "Boot and Recovery Environment"},
    {"action": "wait_serial", "pattern": "Starting kernel"},
    {"action": "wait_serial", "pattern": "Linux version"},
    {"action": "wait_serial", "pattern": "Brought up 4 CPUs"},
    {"action": "wait_serial", "pattern": "init process was started"},
    {"action": "wait_serial", "pattern": "Network Device Manager"},
    {"action": "wait_serial", "pattern": "system ready"},
    {"action": "wait_serial", "pattern": "waiting for connections"},
    {"action": "wait_serial", "pattern": "Started version"},
    {"action": "probe_http", "url": "http://127.0.0.1:8080/"},
    {"action": "reset"},
    {"action": "wait_serial", "pattern": "Boot and Recovery Environment"},
    {"action": "wait_serial", "pattern": "Starting kernel"},
    {"action": "wait_serial", "pattern": "Linux version"},
    {"action": "wait_serial", "pattern": "Brought up 4 CPUs"},
    {"action": "wait_serial", "pattern": "system ready"},
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
    def test_golden_gate_counts(self):
        labels = [("MT7621_QEMU_BUILD", "PASS"),
                  ("PORTED_IMAGE_VALIDATION", "PASS"),
                  ("PORTED_NDMC_READY", "NOT_TESTED"),
                  ("MIPSEL_ENTWARE_READY", "NOT_TESTED"),
                  ("MIPSEL_SERVICE_RESTART", "NOT_TESTED"),
                  ("MIPSEL_PERSISTENCE", "NOT_TESTED"),
                  ("NEGATIVE_IMAGE_REJECT", "PASS"),
                  ("HOST_ISOLATION", "PASS"),
                  ("CLEANUP", "PASS")]
        rep = {"scenario": "s", "outcome": "PASS",
               "steps": [row(s["action"]) for s in BOOT_STEPS]}
        ported_rep = {"scenario": "p", "outcome": "PASS",
                      "steps": [row(s["action"]) for s in PORTED_STEPS]}
        gates = mg.evaluate(scenario(steps=BOOT_STEPS), rep, labels,
                            ported_scenario=scenario(steps=PORTED_STEPS,
                                                     name="p"),
                            ported_report=ported_rep)
        counts = Counter(v["status"] for v in gates.values())
        self.assertEqual(counts["PASS"], 15)
        self.assertEqual(counts["BLOCKED"], 0)
        self.assertEqual(counts["NOT_TESTED"], 4)
        self.assertEqual(sum(counts.values()), len(mg.GATES))
        self.assertEqual(mg.outcome_of(gates), "BLOCKED")
        # the derived gates are the expected PASSes
        for name in ("MT7621_OPENWRT_BOOT", "MT7621_REBOOT", "MIPSEL_NATIVE_ELF",
                     "PORTED_KERNEL_BOOT", "PORTED_KERNEL_SMP_4VPE",
                     "PORTED_USERSPACE_READY", "PORTED_NDMS_READY",
                     "PORTED_LAN_READY", "PORTED_REBOOT",
                     "MIPSEL_NATIVE_ELF_ON_NDMS"):
            self.assertEqual(gates[name]["status"], "PASS", name)
        # Entware/CLI/persistence stay honestly untested
        for name in ("PORTED_NDMC_READY", "MIPSEL_ENTWARE_READY",
                     "MIPSEL_SERVICE_RESTART", "MIPSEL_PERSISTENCE"):
            self.assertEqual(gates[name]["status"], "NOT_TESTED", name)


class PortedTests(unittest.TestCase):
    @staticmethod
    def report(rows, name="p", outcome="PASS"):
        return {"scenario": name, "outcome": outcome, "steps": rows}

    def full(self, steps=None):
        steps = PORTED_STEPS if steps is None else steps
        return mg.derive_ported_gates(scenario(steps=steps, name="p"),
                                      self.report([row(s["action"])
                                                   for s in steps]))

    def test_full_run_passes_all_ported_gates(self):
        gates = self.full()
        for name in mg.PORTED_DERIVED_GATES:
            self.assertEqual(gates[name]["status"], "PASS", name)
        self.assertEqual(gates["PORTED_KERNEL_SMP_4VPE"]["reason"],
                         "four_vpes_brought_up")
        self.assertEqual(gates["PORTED_NDMS_READY"]["reason"],
                         "ndm_ready_http_answered_on_lan")
        self.assertEqual(gates["PORTED_LAN_READY"]["reason"],
                         "ndms_http_answered_on_loopback_forward")
        self.assertEqual(gates["MIPSEL_NATIVE_ELF_ON_NDMS"]["reason"],
                         "native_ndms_daemons_running")
        self.assertEqual(gates["PORTED_REBOOT"]["reason"],
                         "reset_then_second_full_boot_to_ndms")

    def test_smp_marker_timeout_fails_boot_gates(self):
        steps = list(PORTED_STEPS)
        rows = [row(s["action"]) for s in steps]
        rows[3]["outcome"] = "FAIL"  # "Brought up 4 CPUs" timed out
        gates = mg.derive_ported_gates(scenario(steps=steps, name="p"),
                                       self.report(rows, outcome="FAIL"))
        self.assertEqual(gates["PORTED_KERNEL_BOOT"]["status"], "FAIL")
        self.assertEqual(gates["PORTED_KERNEL_SMP_4VPE"]["status"], "FAIL")
        for name in ("PORTED_USERSPACE_READY", "PORTED_NDMS_READY",
                     "PORTED_LAN_READY", "MIPSEL_NATIVE_ELF_ON_NDMS"):
            self.assertEqual(gates[name]["status"], "NOT_TESTED", name)

    def test_lan_probe_failure_fails_ndms_ready_and_lan(self):
        steps = list(PORTED_STEPS)
        rows = [row(s["action"]) for s in steps]
        rows[9]["outcome"] = "FAIL"  # the HTTP probe timed out
        gates = mg.derive_ported_gates(scenario(steps=steps, name="p"),
                                       self.report(rows, outcome="FAIL"))
        self.assertEqual(gates["PORTED_LAN_READY"]["status"], "FAIL")
        self.assertEqual(gates["PORTED_LAN_READY"]["reason"],
                         "ndms_lan_probe_failed")
        self.assertEqual(gates["PORTED_NDMS_READY"]["status"], "FAIL")
        self.assertEqual(gates["PORTED_NDMS_READY"]["reason"],
                         "ndms_lan_probe_failed")
        # the kernel/SMP evidence stands
        self.assertEqual(gates["PORTED_KERNEL_SMP_4VPE"]["status"], "PASS")

    def test_daemon_wait_timeout_fails_native_elf_on_ndms(self):
        steps = list(PORTED_STEPS)
        rows = [row(s["action"]) for s in steps]
        rows[8]["outcome"] = "FAIL"  # dropbear "Started version" timed out
        gates = mg.derive_ported_gates(scenario(steps=steps, name="p"),
                                       self.report(rows, outcome="FAIL"))
        self.assertEqual(gates["MIPSEL_NATIVE_ELF_ON_NDMS"]["status"], "FAIL")
        self.assertEqual(gates["MIPSEL_NATIVE_ELF_ON_NDMS"]["reason"],
                         "ndms_daemon_startup_failed")
        self.assertEqual(gates["PORTED_NDMS_READY"]["status"], "FAIL")

    def test_reboot_requires_reset_and_second_full_boot(self):
        steps = [s for s in PORTED_STEPS if s["action"] != "reset"]
        gates = self.full(steps)
        self.assertEqual(gates["PORTED_REBOOT"]["status"], "NOT_TESTED")
        self.assertEqual(gates["PORTED_REBOOT"]["reason"],
                         "scenario_has_no_reset_step")

    def test_reboot_requires_second_ndms_ready(self):
        steps = PORTED_STEPS[:11]  # stops right after the reset
        gates = self.full(steps)
        self.assertEqual(gates["PORTED_REBOOT"]["status"], "NOT_TESTED")
        self.assertEqual(gates["PORTED_REBOOT"]["reason"],
                         "scenario_has_no_second_ready_step")

    def test_second_boot_timeout_fails_reboot_only(self):
        steps = list(PORTED_STEPS)
        rows = [row(s["action"]) for s in steps]
        rows[-1]["outcome"] = "FAIL"  # the post-reset "system ready" timed out
        gates = mg.derive_ported_gates(scenario(steps=steps, name="p"),
                                       self.report(rows, outcome="FAIL"))
        self.assertEqual(gates["PORTED_REBOOT"]["status"], "FAIL")
        self.assertEqual(gates["PORTED_REBOOT"]["reason"],
                         "second_boot_marker_timeout")
        # the first boot evidence stands
        self.assertEqual(gates["PORTED_NDMS_READY"]["status"], "PASS")

    def test_foreign_scenario_is_refused(self):
        gates = mg.derive_ported_gates(scenario(steps=PORTED_STEPS, name="p"),
                                       self.report([row(s["action"])
                                                    for s in PORTED_STEPS],
                                                   name="other"))
        self.assertEqual(gates["PORTED_KERNEL_BOOT"]["status"], "FAIL")
        self.assertEqual(gates["PORTED_KERNEL_BOOT"]["reason"],
                         "report_scenario_mismatch")
        self.assertEqual(gates["PORTED_REBOOT"]["status"], "NOT_TESTED")

    def test_missing_daemon_waits_are_not_tested(self):
        steps = [s for s in PORTED_STEPS
                 if s.get("pattern") not in mg.DAEMON_WAIT_MARKERS]
        gates = self.full(steps)
        self.assertEqual(gates["MIPSEL_NATIVE_ELF_ON_NDMS"]["status"],
                         "NOT_TESTED")
        self.assertEqual(gates["MIPSEL_NATIVE_ELF_ON_NDMS"]["reason"],
                         "scenario_lacks_daemon_waits")
        # ndms ready without the daemon rows: still probe-based
        self.assertEqual(gates["PORTED_NDMS_READY"]["status"], "PASS")

    def test_setup_failure_leaves_everything_untested(self):
        gates = mg.derive_ported_gates(
            scenario(steps=PORTED_STEPS, name="p"),
            self.report([row("setup", outcome="FAIL")], outcome="FAIL"))
        for name in mg.PORTED_DERIVED_GATES:
            self.assertEqual(gates[name]["status"], "NOT_TESTED", name)

    def test_pass_refused_for_ported_native_gates(self):
        fresh = {name: mg._gate("NOT_TESTED", "unattempted")
                 for name in mg.GATES}
        for name in ("PORTED_KERNEL_BOOT", "PORTED_KERNEL_SMP_4VPE",
                     "PORTED_USERSPACE_READY", "PORTED_NDMS_READY",
                     "PORTED_LAN_READY", "PORTED_REBOOT",
                     "MIPSEL_NATIVE_ELF_ON_NDMS"):
            with self.assertRaises(ValueError) as cm:
                mg.apply_labels(dict(fresh), [(name, "PASS")], {})
            self.assertIn(name, str(cm.exception))


if __name__ == "__main__":
    unittest.main()

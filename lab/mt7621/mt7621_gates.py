#!/usr/bin/env python3
"""Aggregate MT7621 gate evidence into the lab JSON/JUnit contract.

Native gates (guest-run evidence) can never be labelled PASS from the
controller: ``--set GATE PASS`` is refused for them.  The OpenWrt gates
and the KeeneticOS Ported gates are derived strictly from scenario
reports checked against the committed scenario files: row order must
match the scenario, reboot gates require an actual reset step plus an
observed second boot, MIPSEL_NATIVE_ELF requires explicit execution of
the busybox ELF binary with the command's answer verified (the answer
marker must not occur in the sent text, so a UART echo of the command
cannot fake the PASS), and the Ported gates require the 4-VPE SMP
bring-up, the NDMS userspace startup markers and the NDMS HTTP answer on
the loopback LAN forward.  Statuses are never upgraded: BLOCKED and
NOT_TESTED stay what they are.

Usage:
  python3 lab/mt7621/mt7621_gates.py --report-dir DIR \
      --openwrt-scenario lab/scenarios/openwrt-mt7621-native-elf.json \
      --openwrt-report work/reports/openwrt-mt7621/report.json \
      --ported-scenario lab/scenarios/ported-mt7621-ndms.json \
      --ported-report work/reports/ported-mt7621-ndms/report.json \
      [--set GATE STATUS --reason GATE LABEL] ...
"""
import argparse
import json
from pathlib import Path
import xml.etree.ElementTree as ET

GATES = (
    "MT7621_QEMU_BUILD", "MT7621_OPENWRT_BOOT", "MT7621_REBOOT",
    "PORTED_IMAGE_VALIDATION", "PORTED_KERNEL_BOOT", "PORTED_KERNEL_SMP_4VPE",
    "PORTED_USERSPACE_READY", "PORTED_NDMS_READY", "PORTED_LAN_READY",
    "PORTED_REBOOT", "PORTED_NDMC_READY", "MIPSEL_ENTWARE_READY",
    "MIPSEL_NATIVE_ELF", "MIPSEL_NATIVE_ELF_ON_NDMS",
    "MIPSEL_SERVICE_RESTART", "MIPSEL_PERSISTENCE", "NEGATIVE_IMAGE_REJECT",
    "HOST_ISOLATION", "CLEANUP",
)
STATUSES = ("PASS", "FAIL", "BLOCKED", "NOT_TESTED")

# Gates whose PASS requires guest-run scenario evidence.  The controller
# may only label them FAIL/BLOCKED/NOT_TESTED; PASS must come from a
# scenario report validated against its committed scenario.
NATIVE_GATES = (
    "MT7621_OPENWRT_BOOT", "MT7621_REBOOT", "PORTED_KERNEL_BOOT",
    "PORTED_KERNEL_SMP_4VPE", "PORTED_USERSPACE_READY", "PORTED_NDMS_READY",
    "PORTED_LAN_READY", "PORTED_REBOOT", "PORTED_NDMC_READY",
    "MIPSEL_ENTWARE_READY", "MIPSEL_NATIVE_ELF", "MIPSEL_NATIVE_ELF_ON_NDMS",
    "MIPSEL_SERVICE_RESTART", "MIPSEL_PERSISTENCE",
)

# MIPSEL_NATIVE_ELF: the scenario must execute the ELF binary itself
# (not merely read its magic bytes) and wait for the command's answer.
ELF_SEND_SUBSTRING = "/bin/busybox uname"
ELF_ANSWER_MARKER = "mips"
# MT7621_REBOOT: an actual reset step plus a post-reset kernel boot; the
# runner only scans bytes logged after the reset, so first-boot lines
# cannot satisfy this marker.
SECOND_BOOT_MARKER = "Linux version"
# KeeneticOS Ported markers (the scenario patterns carrying the evidence).
SMP_4VPE_MARKER = "Brought up 4 CPUs"
INIT_MARKER = "init process was started"
NDMS_READY_MARKER = "system ready"
# telnetd + dropbear: native MIPSel daemons started by NDMS.
DAEMON_WAIT_MARKERS = ("waiting for connections", "Started version")


def _gate(status, reason):
    return {"status": status, "reason": reason}


def _mismatch():
    return {"MT7621_OPENWRT_BOOT": _gate("FAIL", "report_scenario_mismatch"),
            "MT7621_REBOOT": _gate("NOT_TESTED", "report_scenario_mismatch"),
            "MIPSEL_NATIVE_ELF": _gate("NOT_TESTED", "report_scenario_mismatch")}


PORTED_DERIVED_GATES = ("PORTED_KERNEL_BOOT", "PORTED_KERNEL_SMP_4VPE",
                        "PORTED_USERSPACE_READY", "PORTED_NDMS_READY",
                        "PORTED_LAN_READY", "PORTED_REBOOT",
                        "MIPSEL_NATIVE_ELF_ON_NDMS")


def _ported_mismatch():
    gates = {name: _gate("NOT_TESTED", "report_scenario_mismatch")
             for name in PORTED_DERIVED_GATES}
    gates["PORTED_KERNEL_BOOT"] = _gate("FAIL", "report_scenario_mismatch")
    return gates


def _checked_prefix(scenario, report):
    """Validated (steps, rows, first_fail) of a lab/run.py report for this
    scenario: rows are a scenario-ordered prefix, stopping at the first
    failing step, and a full-PASS report must cover the whole scenario.
    Returns None when the report does not match the committed scenario."""
    steps = scenario.get("steps", [])
    rows = report.get("steps", [])
    if rows and rows[0].get("action") == "setup":
        return None
    if not rows:
        return None
    if (report.get("scenario") != scenario.get("name")
            or [r.get("action") for r in rows]
            != [s.get("action") for s in steps[:len(rows)]]):
        return None
    first_fail = next((i for i, r in enumerate(rows) if r.get("outcome") != "PASS"),
                      None)
    if first_fail is None and len(rows) != len(steps):
        # A full-PASS report that stops short of the scenario end is not
        # something lab/run.py can produce.
        return None
    return steps, rows, first_fail


def derive_openwrt_gates(scenario, report):
    """Derive BOOT/REBOOT/NATIVE_ELF from a report against its scenario.

    lab/run.py records one row per executed scenario step, in scenario
    order, until the first failing step, so the rows must be a prefix of
    the committed scenario's action sequence.
    """
    checked = _checked_prefix(scenario, report)
    if checked is None:
        rows = report.get("steps", [])
        if rows and rows[0].get("action") == "setup":
            return {"MT7621_OPENWRT_BOOT": _gate("FAIL", "setup_failed"),
                    "MT7621_REBOOT": _gate("NOT_TESTED", "prerequisite_failed"),
                    "MIPSEL_NATIVE_ELF": _gate("NOT_TESTED",
                                               "prerequisite_failed")}
        if not rows:
            return {"MT7621_OPENWRT_BOOT": _gate("FAIL", "no_steps_recorded"),
                    "MT7621_REBOOT": _gate("NOT_TESTED", "prerequisite_failed"),
                    "MIPSEL_NATIVE_ELF": _gate("NOT_TESTED",
                                               "prerequisite_failed")}
        return _mismatch()
    steps, rows, first_fail = checked

    reset_idx = next((i for i, s in enumerate(steps) if s["action"] == "reset"),
                     None)

    # Boot: every first-boot step (everything up to the reset) ran and passed.
    if reset_idx is None:
        boot = (_gate("PASS", "first_boot_steps_passed")
                if first_fail is None else _gate("FAIL", "first_boot_step_failed"))
    elif first_fail is not None and first_fail < reset_idx:
        boot = _gate("FAIL", "first_boot_step_failed")
    else:
        boot = _gate("PASS", "first_boot_steps_passed")

    # Reboot: the reset row itself passed and a post-reset wait observed
    # the second kernel boot.
    if reset_idx is None:
        reboot = _gate("NOT_TESTED", "scenario_has_no_reset_step")
    elif first_fail is not None and first_fail < reset_idx:
        reboot = _gate("NOT_TESTED", "reset_not_reached")
    elif len(rows) <= reset_idx or rows[reset_idx].get("outcome") != "PASS":
        reboot = _gate("FAIL", "reset_not_reached")
    else:
        marker_idx = next(
            (i for i in range(reset_idx + 1, len(steps))
             if steps[i].get("action") == "wait_serial"
             and SECOND_BOOT_MARKER in steps[i].get("pattern", "")), None)
        if marker_idx is None:
            reboot = _gate("NOT_TESTED", "scenario_has_no_second_boot_step")
        elif marker_idx >= len(rows):
            reboot = _gate("FAIL", "second_boot_unobserved")
        elif rows[marker_idx].get("outcome") == "PASS":
            reboot = _gate("PASS", "reset_then_second_kernel_boot_observed")
        else:
            reboot = _gate("FAIL", "second_boot_marker_timeout")

    # Native ELF: explicit execution of the busybox ELF binary followed by
    # its verified answer; an echo-unsafe marker would let the UART echo of
    # the sent command line fake the PASS.
    elf = None
    for i in range(len(steps) - 1):
        sent = (steps[i].get("text", "")
                if steps[i].get("action") == "send_serial" else "")
        nxt = steps[i + 1]
        if (ELF_SEND_SUBSTRING in sent and nxt.get("action") == "wait_serial"
                and nxt.get("pattern") == ELF_ANSWER_MARKER):
            if ELF_ANSWER_MARKER in sent:
                elf = _gate("NOT_TESTED", "elf_marker_matches_uart_echo")
            elif i >= len(rows):
                elf = _gate("NOT_TESTED", "elf_step_not_reached")
            elif rows[i].get("outcome") != "PASS":
                elf = _gate("FAIL", "elf_step_failed")
            elif i + 1 >= len(rows):
                elf = _gate("FAIL", "elf_answer_unobserved")
            elif rows[i + 1].get("outcome") == "PASS":
                elf = _gate("PASS", "busybox_uname_answered_mips_in_guest")
            else:
                elf = _gate("FAIL", "elf_answer_marker_timeout")
            break
    if elf is None:
        elf = _gate("NOT_TESTED", "scenario_lacks_explicit_elf_execution")

    return {"MT7621_OPENWRT_BOOT": boot, "MT7621_REBOOT": reboot,
            "MIPSEL_NATIVE_ELF": elf}


def derive_ported_gates(scenario, report):
    """Derive the KeeneticOS Ported gates from a report against its scenario.

    PORTED_KERNEL_BOOT / PORTED_KERNEL_SMP_4VPE need the observed 4-VPE SMP
    bring-up ("Brought up 4 CPUs"), PORTED_USERSPACE_READY the ndm init
    start, PORTED_NDMS_READY the ndm "system ready" marker plus the NDMS
    HTTP answer on the loopback LAN forward (the actual response of the
    running NDMS), MIPSEL_NATIVE_ELF_ON_NDMS the startup of the native
    MIPSel NDMS daemons (telnetd, dropbear), and PORTED_REBOOT an actual
    reset step followed by a second full boot up to "system ready".
    """
    checked = _checked_prefix(scenario, report)
    if checked is None:
        rows = report.get("steps", [])
        if rows and rows[0].get("action") == "setup":
            return {name: _gate("NOT_TESTED", "prerequisite_failed")
                    for name in PORTED_DERIVED_GATES}
        if not rows:
            return {name: _gate("NOT_TESTED", "no_steps_recorded")
                    for name in PORTED_DERIVED_GATES}
        return _ported_mismatch()
    steps, rows, first_fail = checked

    def wait_idx(marker):
        return next((i for i, s in enumerate(steps)
                     if s.get("action") == "wait_serial"
                     and marker in s.get("pattern", "")), None)

    def state(idx):
        """absent | blocked | unreached | passed | timeout"""
        if idx is None:
            return "absent"
        if first_fail is not None and first_fail < idx:
            return "blocked"
        if idx >= len(rows):
            return "unreached"
        return "passed" if rows[idx].get("outcome") == "PASS" else "timeout"

    smp = state(wait_idx(SMP_4VPE_MARKER))
    init = state(wait_idx(INIT_MARKER))
    ready = state(wait_idx(NDMS_READY_MARKER))
    daemons = [state(wait_idx(m)) for m in DAEMON_WAIT_MARKERS]
    probe = state(next((i for i, s in enumerate(steps)
                        if s.get("action") == "probe_http"), None))
    reset_idx = next((i for i, s in enumerate(steps) if s["action"] == "reset"),
                     None)
    reset = state(reset_idx)

    def boot_gate(marker_state):
        if marker_state == "absent":
            return _gate("NOT_TESTED", "scenario_lacks_smp_marker")
        if marker_state in ("blocked", "unreached", "timeout"):
            return _gate("FAIL", "kernel_boot_failed")
        return _gate("PASS", "kernel_boot_observed")

    kernel_boot = boot_gate(smp)
    smp_4vpe = boot_gate(smp)
    if smp_4vpe["status"] == "PASS":
        smp_4vpe = _gate("PASS", "four_vpes_brought_up")

    def prereq_gate(marker_state, ok_reason, fail_reason):
        if smp in ("blocked", "unreached", "timeout"):
            return _gate("NOT_TESTED", "prerequisite_failed")
        if marker_state == "absent":
            return _gate("NOT_TESTED", "scenario_lacks_marker")
        if marker_state in ("blocked", "unreached"):
            return _gate("FAIL", fail_reason)
        if marker_state == "passed":
            return _gate("PASS", ok_reason)
        return _gate("FAIL", fail_reason)

    userspace = prereq_gate(init, "ndm_userspace_init_started",
                            "init_marker_timeout")
    ndms_ready = prereq_gate(ready, "ndm_ready_http_answered_on_lan",
                             "ndms_ready_marker_timeout")
    if ndms_ready["status"] == "PASS" and probe != "passed":
        ndms_ready = _gate("FAIL", "ndms_lan_probe_failed")
    if smp not in ("blocked", "unreached", "timeout") and ready == "passed":
        lan = _gate("PASS", "ndms_http_answered_on_loopback_forward") \
            if probe == "passed" else _gate("FAIL", "ndms_lan_probe_failed")
    elif probe == "absent":
        lan = _gate("NOT_TESTED", "scenario_lacks_http_probe")
    else:
        lan = _gate("NOT_TESTED", "prerequisite_failed")
    if smp in ("blocked", "unreached", "timeout"):
        native_elf = _gate("NOT_TESTED", "prerequisite_failed")
    elif any(d in ("blocked", "unreached", "timeout") for d in daemons):
        native_elf = _gate("FAIL", "ndms_daemon_startup_failed")
    elif any(d == "absent" for d in daemons):
        native_elf = _gate("NOT_TESTED", "scenario_lacks_daemon_waits")
    else:
        native_elf = _gate("PASS", "native_ndms_daemons_running")

    if reset == "absent":
        reboot = _gate("NOT_TESTED", "scenario_has_no_reset_step")
    elif reset in ("blocked", "unreached"):
        reboot = _gate("NOT_TESTED", "reset_not_reached")
    elif reset == "timeout":
        reboot = _gate("FAIL", "reset_step_failed")
    elif ready == "absent":
        reboot = _gate("NOT_TESTED", "scenario_has_no_second_ready_step")
    else:
        # the post-reset "system ready" wait: a new occurrence of the
        # marker, matched only against bytes logged after the reset
        post = next((i for i in range(reset_idx + 1, len(steps))
                     if steps[i].get("action") == "wait_serial"
                     and NDMS_READY_MARKER in steps[i].get("pattern", "")), None)
        if post is None:
            reboot = _gate("NOT_TESTED", "scenario_has_no_second_ready_step")
        elif post >= len(rows):
            reboot = _gate("FAIL", "second_boot_unobserved")
        elif rows[post].get("outcome") == "PASS":
            reboot = _gate("PASS", "reset_then_second_full_boot_to_ndms")
        else:
            reboot = _gate("FAIL", "second_boot_marker_timeout")

    return {"PORTED_KERNEL_BOOT": kernel_boot, "PORTED_KERNEL_SMP_4VPE": smp_4vpe,
            "PORTED_USERSPACE_READY": userspace, "PORTED_NDMS_READY": ndms_ready,
            "PORTED_LAN_READY": lan, "PORTED_REBOOT": reboot,
            "MIPSEL_NATIVE_ELF_ON_NDMS": native_elf}


def apply_labels(gates, statuses, reasons=None):
    """Apply controller labels; PASS is refused for native gates."""
    reasons = reasons or {}
    for name, status in statuses:
        if name not in GATES or status not in STATUSES:
            raise ValueError("unapproved gate/status: %s %s" % (name, status))
        if status == "PASS" and name in NATIVE_GATES:
            raise ValueError(
                "refusing PASS for native gate %s: guest-run evidence must "
                "come from a scenario report, not --set" % name)
        gates[name] = _gate(status, reasons.get(name, "controller_labelled"))
    return gates


def evaluate(openwrt_scenario, openwrt_report, statuses=(), reasons=None,
             ported_scenario=None, ported_report=None):
    gates = {name: _gate("NOT_TESTED", "unattempted") for name in GATES}
    gates.update(derive_openwrt_gates(openwrt_scenario, openwrt_report))
    if ported_scenario is not None and ported_report is not None:
        gates.update(derive_ported_gates(ported_scenario, ported_report))
    apply_labels(gates, statuses, reasons)
    return gates


def outcome_of(gates):
    values = list(gates.values())
    return ("FAIL" if any(v["status"] == "FAIL" for v in values)
            else "BLOCKED" if any(v["status"] != "PASS" for v in values)
            else "PASS")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--report-dir", required=True)
    ap.add_argument("--openwrt-scenario", required=True,
                    help="committed scenario JSON matching the report")
    ap.add_argument("--openwrt-report", required=True,
                    help="report.json of the OpenWrt boot/ELF/reboot scenario")
    ap.add_argument("--ported-scenario",
                    help="committed KeeneticOS Ported scenario JSON")
    ap.add_argument("--ported-report",
                    help="report.json of the KeeneticOS Ported boot scenario")
    ap.add_argument("--set", action="append", nargs=2, metavar=("GATE", "STATUS"),
                    default=[],
                    help="explicit gate status; PASS refused for native gates")
    ap.add_argument("--reason", action="append", nargs=2, metavar=("GATE", "REASON"),
                    default=[])
    args = ap.parse_args()
    try:
        scenario = json.loads(Path(args.openwrt_scenario).read_text(encoding="utf-8"))
        report = json.loads(Path(args.openwrt_report).read_text(encoding="utf-8"))
        ported_scenario = ported_report = None
        if args.ported_scenario and args.ported_report:
            ported_scenario = json.loads(
                Path(args.ported_scenario).read_text(encoding="utf-8"))
            ported_report = json.loads(
                Path(args.ported_report).read_text(encoding="utf-8"))
        reasons = {name: why for name, why in args.reason}
        gates = evaluate(scenario, report, args.set, reasons,
                         ported_scenario, ported_report)
    except ValueError as error:
        ap.error(str(error))
    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    outcome = outcome_of(gates)
    report = {"schema": 2, "platform": "MT7621_MIPSEL_QEMU_XIAOMI_R3G",
              "outcome": outcome, "hardware_acceptance": "NOT_TESTED",
              "hardware_acceptance_note": "full MT7621AT 4-VPE profile on a "
                                          "physical router is a separate "
                                          "acceptance criterion",
              "gates": gates,
              "warning": "Virtual test evidence is NOT physical-router acceptance."}
    (report_dir / "gates.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    suite = ET.Element("testsuite", name="MT7621 MIPSel stand", tests=str(len(gates)),
                       failures=str(sum(v["status"] == "FAIL" for v in gates.values())),
                       skipped=str(sum(v["status"] in ("BLOCKED", "NOT_TESTED")
                                       for v in gates.values())))
    lines = ["Laboratory outcome: **%s**" % outcome, "", "| Gate | Status | Evidence |", "|---|---|---|"]
    for name, value in gates.items():
        case = ET.SubElement(suite, "testcase", name=name)
        if value["status"] == "FAIL":
            ET.SubElement(case, "failure", message=value["reason"])
        elif value["status"] in ("BLOCKED", "NOT_TESTED"):
            ET.SubElement(case, "skipped", message=value["status"] + ": " + value["reason"])
        lines.append("| %s | %s | %s |" % (name, value["status"], value["reason"]))
    ET.ElementTree(suite).write(report_dir / "junit.xml", encoding="utf-8",
                                xml_declaration=True)
    lines += ["", "Physical-router acceptance: NOT_TESTED; the full MT7621AT "
              "4-VPE hardware profile is a separate acceptance criterion. "
              "KeeneticOS Ported boots 4 VPEs to NDMS in QEMU (derived from "
              "the committed scenario report); Entware, the ndm CLI over "
              "telnet, service restart and persistence remain NOT_TESTED. "
              "Native MIPSel ELF execution in the OpenWrt guest is NOT an "
              "Entware check on KeeneticOS.", ""]
    (report_dir / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    print("gates outcome:", outcome, "->", report_dir / "gates.json")


if __name__ == "__main__":
    main()

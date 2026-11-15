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
cannot fake the PASS), the Ported gates require the 4-VPE SMP
bring-up, the NDMS userspace startup markers and the NDMS HTTP answer on
the loopback LAN forward, and PORTED_NDMC_READY requires a real ndm CLI
session over the loopback telnet forward (prompt greeting plus a version
command with its verified release answer — an open port or banner alone
never satisfies it).  Statuses are never upgraded: BLOCKED and
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
# PORTED_NDMC_READY: the ndm CLI over the loopback telnet forward.  A real
# CLI session is proven by the prompt greeting plus a version command with
# its verified release answer; an open TCP port or the banner alone is not
# a CLI.  The answer markers are echo-safe (checked at scenario load).
CLI_PROMPT_MARKER = "\\(config\\)>"
CLI_VERSION_SEND = "show version"
CLI_VERSION_ANSWER = "release:"
CLI_REBOOT_SEND = "system reboot"
CLI_REBOOT_ANSWER = "Activated system reboot"
CLI_REBOOT_KERNEL_MARKER = "reboot: Restarting system"

# Entware clean-bootstrap chain (ported-mt7621-entware.json): real opkg
# answers, a native Entware ELF answer, the installer completion marker,
# a native reboot and post-reboot persistence.  The banner triplet is the
# Entware mipselsf bash 5.3.20(1) one; "mipsel-unknown-linux" can never
# appear in the answer (2026-11-15 diagnostic, see the MT7621 README).
ENTWARE_ARCH_SEND = "opkg print-architecture"
ENTWARE_ARCH_ANSWER = "mipsel-3.4_kn 200"
ENTWARE_ELF_SEND = "/opt/bin/bash --version"
ENTWARE_ELF_ANSWER = "mipsel-openwrt-linux-gnu"
ENTWARE_INITRC_MARKER = "configured init script"
ENTWARE_REBOOT_MARKER = "reboot: Restarting system"
ENTWARE_PERSIST_SEND = "/opt/.lab-marker"
ENTWARE_PERSIST_ANSWER = "MARK42"


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


def derive_cli_gates(scenario, report):
    """Derive PORTED_NDMC_READY from a report against its committed CLI
    scenario: the ndm CLI over the loopback telnet forward must show its
    prompt, answer a version command with the pinned release string and
    answer a state command — matching report rows, echo-safe by load-time
    validation.  An open port or banner alone never satisfies the gate."""
    untested = _gate("NOT_TESTED", "cli_scenario_not_provided")
    checked = _checked_prefix(scenario, report)
    if checked is None:
        return {"PORTED_NDMC_READY": _gate("FAIL", "report_scenario_mismatch")}
    steps, rows, first_fail = checked

    def telnet_idx(predicate):
        return next((i for i, s in enumerate(steps)
                     if s.get("action") == "telnet" and predicate(s)), None)

    def state(idx):
        if idx is None:
            return "absent"
        if first_fail is not None and first_fail < idx:
            return "blocked"
        if idx >= len(rows):
            return "unreached"
        return "passed" if rows[idx].get("outcome") == "PASS" else "timeout"

    prompt = state(telnet_idx(lambda s: CLI_PROMPT_MARKER in s.get("greeting", "")))
    version = state(telnet_idx(lambda s: CLI_VERSION_SEND in s.get("send", "")
                               and CLI_VERSION_ANSWER in s.get("expect", "")))
    # rows stop at the first failing step, so at most one of the two states
    # is "timeout" (a direct marker timeout) while the other is blocked —
    # the direct timeout is the gate's own FAIL verdict.
    if "timeout" in (prompt, version):
        return {"PORTED_NDMC_READY": _gate("FAIL", "ndm_cli_marker_timeout")}
    if "absent" in (prompt, version):
        return {"PORTED_NDMC_READY": _gate(
            "NOT_TESTED", "scenario_lacks_cli_prompt_or_version_answer")}
    if "blocked" in (prompt, version) or "unreached" in (prompt, version):
        return {"PORTED_NDMC_READY": _gate("NOT_TESTED", "prerequisite_failed")}
    return {"PORTED_NDMC_READY": _gate(
        "PASS", "ndm_cli_prompt_and_version_answer_on_telnet")}


def derive_entware_gates(scenario, report):
    """Derive MIPSEL_ENTWARE_READY from a report against its committed
    Entware scenario: the installer completion marker, real opkg answers,
    a native Entware ELF answer with the pinned banner triplet, a native
    reboot and post-reboot persistence — all from scenario-ordered report
    rows.  A failure before the installer completion marker is a
    prerequisite problem (NOT_TESTED); any failure at or after it is an
    Entware-chain failure (FAIL).  The gate never PASSes from a report
    that stops short of the scenario or does not carry the whole chain."""
    checked = _checked_prefix(scenario, report)
    if checked is None:
        return {"MIPSEL_ENTWARE_READY": _gate("FAIL", "report_scenario_mismatch")}
    steps, rows, first_fail = checked

    def step_idx(predicate):
        return [i for i, s in enumerate(steps) if predicate(s)]

    initrc = step_idx(lambda s: s.get("action") == "wait_serial"
                      and ENTWARE_INITRC_MARKER in s.get("pattern", ""))
    reboot = step_idx(lambda s: s.get("action") == "wait_serial"
                      and ENTWARE_REBOOT_MARKER in s.get("pattern", ""))
    arch = step_idx(lambda s: s.get("action") == "ssh"
                    and ENTWARE_ARCH_SEND in s.get("send", "")
                    and ENTWARE_ARCH_ANSWER
                    in s.get("expect", "").replace("\\", ""))
    elf = step_idx(lambda s: s.get("action") == "ssh"
                   and ENTWARE_ELF_SEND in s.get("send", "")
                   and ENTWARE_ELF_ANSWER in s.get("expect", ""))
    persist = step_idx(lambda s: s.get("action") == "ssh"
                       and ENTWARE_PERSIST_SEND in s.get("send", "")
                       and ENTWARE_PERSIST_ANSWER in s.get("expect", ""))
    ready = step_idx(lambda s: s.get("action") == "wait_serial"
                     and NDMS_READY_MARKER in s.get("pattern", ""))
    first_mount = step_idx(lambda s: s.get("action") == "wait_serial"
                           and "mounted to /opt" in s.get("pattern", ""))
    second_mount = step_idx(lambda s: s.get("action") == "ssh"
                            and "/proc/mounts" in s.get("send", "")
                            and "/opt ext4" in s.get("expect", ""))
    install = step_idx(lambda s: s.get("action") == "ssh"
                       and "opkg install bash" in s.get("send", "")
                       and "Configuring bash" in s.get("expect", ""))
    absent = step_idx(lambda s: s.get("action") == "ssh"
                      and "test ! -e /opt/etc/init.d/doinstall" in s.get("send", "")
                      and "DOINSTALL_ABSENT" in s.get("expect", ""))
    # A changed scenario cannot pass by moving the post-reboot proof to
    # before reboot, or omitting system-ready/mount/second opkg entirely.
    if not (initrc and reboot and len(arch) >= 2 and len(elf) >= 2
            and len(persist) >= 2 and len(ready) >= 2 and first_mount
            and second_mount and install and absent):
        return {"MIPSEL_ENTWARE_READY": _gate(
            "NOT_TESTED", "scenario_lacks_entware_chain")}
    evidence_order = (ready[0], first_mount[0], initrc[0], arch[0],
                      install[0], elf[0], absent[0], persist[0],
                      reboot[0], ready[1], second_mount[0],
                      arch[1], elf[1], persist[1])
    if any(a >= b for a, b in zip(evidence_order, evidence_order[1:])):
        return {"MIPSEL_ENTWARE_READY": _gate(
            "NOT_TESTED", "entware_chain_not_ordered")}
    if first_fail is None:
        if report.get("outcome") != "PASS":
            return {"MIPSEL_ENTWARE_READY": _gate(
                "FAIL", "report_outcome_mismatch")}
        return {"MIPSEL_ENTWARE_READY": _gate(
            "PASS", "entware_clean_bootstrap_opkg_elf_reboot_persistence")}
    if first_fail < initrc[0]:
        return {"MIPSEL_ENTWARE_READY": _gate(
            "NOT_TESTED", "prerequisite_failed")}
    return {"MIPSEL_ENTWARE_READY": _gate(
        "FAIL", "entware_chain_step_failed")}



def derive_service_restart_gate(scenario, report):
    """Independent guest-evidence Stage C service restart. Never infer it
    from the existing 32-step Entware bootstrap or another native gate."""
    checked = _checked_prefix(scenario, report)
    if checked is None:
        return {"MIPSEL_SERVICE_RESTART": _gate("FAIL", "report_scenario_mismatch")}
    steps, rows, first_fail = checked

    def positions(expect, needle):
        return [i for i, s in enumerate(steps)
                if s.get("action") == "ssh"
                and s.get("expect") == expect
                and needle in s.get("send", "")]

    # All proof commands are executed over fresh loopback SSH sessions.
    # Remote exit status 0 and the echo-safe actual output are validated
    # by run_ssh_step, not assumed from these scenario descriptions.
    applet = positions("APPLET_READY", "grep -x base64")
    fixture = positions("LAB_SERVICE_READY", "base64 -d > /opt/etc/init.d/S98labprobe")
    started = positions("RUNNING 1 [0-9]+", "S98labprobe start")
    saved = positions("FIRST_SAVED", "labprobe.first")
    restarted = positions("RUNNING 2 [0-9]+", "S98labprobe restart")
    proof = positions("RESTART_VERIFIED", 'old" != "$now')
    cleanup = positions("LAB_CLEANED", "S98labprobe stop")
    post_boot = [i for i, s in enumerate(steps)
                 if s.get("action") == "ssh"
                 and s.get("send") == "/opt/bin/busybox cat /opt/.lab-marker"
                 and s.get("expect") == "MARK42"]
    stages = (applet, fixture, started, saved, restarted, proof, cleanup)
    if not post_boot or len(post_boot) != 1 or any(len(p) != 1 for p in stages):
        return {"MIPSEL_SERVICE_RESTART": _gate(
            "NOT_TESTED", "scenario_lacks_service_restart_chain")}
    order = (post_boot[0],) + tuple(p[0] for p in stages)
    if any(a >= b for a, b in zip(order, order[1:])):
        return {"MIPSEL_SERVICE_RESTART": _gate(
            "NOT_TESTED", "service_restart_chain_not_ordered")}
    if first_fail is None:
        if report.get("outcome") != "PASS":
            return {"MIPSEL_SERVICE_RESTART": _gate(
                "FAIL", "report_outcome_mismatch")}
        return {"MIPSEL_SERVICE_RESTART": _gate(
            "PASS", "lab_service_restart_new_pid_and_liveness")}
    if first_fail < applet[0]:
        return {"MIPSEL_SERVICE_RESTART": _gate("NOT_TESTED", "prerequisite_failed")}
    return {"MIPSEL_SERVICE_RESTART": _gate("FAIL", "service_restart_chain_step_failed")}


def derive_persistence_gate(scenario, report):
    """Stage C: same /opt/etc value survives native reboot AND QEMU system_reset.

    This is not derivable from the old 32-step Entware bootstrap report.
    SSH output is checked by run.py for exit=0 plus echo-safe answer.
    """
    checked = _checked_prefix(scenario, report)
    if checked is None:
        return {"MIPSEL_PERSISTENCE": _gate("FAIL", "report_scenario_mismatch")}
    steps, rows, first_fail = checked

    def indices(action, predicate):
        return [i for i, s in enumerate(steps)
                if s.get("action") == action and predicate(s)]

    ready = indices("wait_serial", lambda s: s.get("pattern") == "system ready")
    reboot = indices("wait_serial", lambda s: "reboot: Restarting system" in s.get("pattern", ""))
    hard_reset = indices("reset", lambda s: True)
    write = indices("ssh", lambda s:
                    " > /opt/etc/.lab-persist" in s.get("send", "")
                    and "busybox sync" in s.get("send", "")
                    and s.get("expect") == "PERSIST_OK")
    reads = indices("ssh", lambda s:
                    s.get("send", "").endswith("cat /opt/etc/.lab-persist")
                    and " > /opt/etc/.lab-persist" not in s.get("send", "")
                    and s.get("expect") == "PERSIST_OK")
    sync = indices("ssh", lambda s:
                   "busybox sync" in s.get("send", "")
                   and s.get("expect") == "SYNC_COMPLETE")
    mounts = indices("ssh", lambda s:
                     "/proc/mounts" in s.get("send", "")
                     and "/opt ext4" in s.get("expect", ""))
    arch = indices("ssh", lambda s:
                   "opkg print-architecture" in s.get("send", "")
                   and "mipsel-3.4_kn 200" in s.get("expect", "").replace("\\", ""))
    elf = indices("ssh", lambda s:
                  s.get("send") == "/opt/bin/bash --version"
                  and "mipsel-openwrt-linux-gnu" in s.get("expect", ""))
    old_marker = indices("ssh", lambda s:
                         s.get("send") == "/opt/bin/busybox cat /opt/.lab-marker"
                         and s.get("expect") == "MARK42")
    if not (len(ready) >= 3 and len(reboot) == 1 and len(hard_reset) == 1
            and len(write) == 1 and len(reads) == 2 and len(sync) == 1
            and len(mounts) >= 2 and len(arch) >= 3 and len(elf) >= 3
            and len(old_marker) >= 2):
        return {"MIPSEL_PERSISTENCE": _gate("NOT_TESTED", "scenario_lacks_persistence_chain")}
    # Never accept a scenario where the persistence 'proof' was read
    # before the reboot, or if the hard reset occurred before native boot.
    ordered = (ready[0], write[0], reboot[0], ready[1], reads[0],
               sync[0], hard_reset[0], ready[2], mounts[-1], arch[-1],
               elf[-1], reads[1], old_marker[-1])
    if any(a >= b for a, b in zip(ordered, ordered[1:])):
        return {"MIPSEL_PERSISTENCE": _gate("NOT_TESTED", "persistence_chain_not_ordered")}
    if first_fail is None:
        return {"MIPSEL_PERSISTENCE": _gate(
            "PASS" if report.get("outcome") == "PASS" else "FAIL",
            "native_reboot_then_qemu_reset_opt_config_persisted"
            if report.get("outcome") == "PASS" else "report_outcome_mismatch")}
    if first_fail <= write[0]:
        return {"MIPSEL_PERSISTENCE": _gate("NOT_TESTED", "prerequisite_failed")}
    return {"MIPSEL_PERSISTENCE": _gate("FAIL", "persistence_chain_step_failed")}


def derive_autostart_gate(scenario, report):
    """Independent guest evidence: the pinned rc.unslung actually STARTS an
    Entware init script after boot (real worker + boot-generation marker),
    not merely the "configured init script" setting being applied.

    Deliberately outside GATES: the 19 core gates stay unchanged; this gate
    only appears when its own scenario/report pair is supplied.  Because the
    name is not in GATES, apply_labels() refuses any --set for it, so a PASS
    can only ever come from a validated scenario report.
    """
    checked = _checked_prefix(scenario, report)
    if checked is None:
        return {"MIPSEL_AUTOSTART": _gate("FAIL", "report_scenario_mismatch")}
    steps, rows, first_fail = checked

    def positions(expect, needle):
        return [i for i, s in enumerate(steps)
                if s.get("action") == "ssh"
                and s.get("expect") == expect
                and needle in s.get("send", "")]

    # The probe is seeded before the native reboot and never started by hand;
    # gen=1 after that reboot and gen=2 after the separate QEMU monitor reset
    # are the only ways these echo-safe markers can be produced.
    seed = positions("LABBOOT_READY", "base64 -d > /opt/etc/init.d/S99labboot")
    preboot = positions("LABBOOT_OFF", "labboot.gen")
    persisted = positions("PROBE_PERSISTED", "test -x /opt/etc/init.d/S99labboot")
    status1 = positions("LABBOOT gen=1 pid=[0-9]+", "S99labboot status")
    gen1 = positions("GEN_ONE", "labboot.gen")
    identity = positions("WORKER_LIVE", "/proc/$pid/cmdline")
    single = positions("WORKER_SINGLE", "for p in /proc/[0-9]*")
    sync = positions("SYNC_COMPLETE", "busybox sync")
    status2 = positions("LABBOOT gen=2 pid=[0-9]+", "S99labboot status")
    gen2 = positions("GEN_TWO", "labboot.gen")
    pids = positions("PIDS [0-9]+ [0-9]+", "labboot.pid1")
    resets = [i for i, s in enumerate(steps) if s.get("action") == "reset"]
    reboot = [i for i, s in enumerate(steps)
              if s.get("action") == "wait_serial"
              and s.get("pattern") == "reboot: Restarting system"]
    ready = [i for i, s in enumerate(steps)
             if s.get("action") == "wait_serial"
             and s.get("pattern") == "system ready"]
    initrc = [i for i, s in enumerate(steps)
              if s.get("action") == "wait_serial"
              and s.get("pattern") == "configured init script"]
    mounts = [i for i, s in enumerate(steps)
              if s.get("action") == "ssh" and s.get("expect") == "/opt ext4"]
    if not (len(seed) == 1 and len(preboot) == 1 and len(persisted) == 1
            and len(status1) == 1 and len(gen1) == 1 and len(identity) == 2
            and len(single) == 2 and len(sync) == 1 and len(status2) == 1
            and len(gen2) == 1 and len(pids) == 1 and len(resets) == 1
            and len(reboot) == 1 and len(ready) >= 3 and len(initrc) >= 3
            and len(mounts) == 2):
        return {"MIPSEL_AUTOSTART": _gate("NOT_TESTED", "scenario_lacks_autostart_chain")}
    # The pre-boot negative check proves no worker existed before the native
    # reboot; two /proc/<pid>/cmdline identities (a stale pidfile cannot fake
    # them) plus the boot-generation increments prove the script ran in each
    # of the two post-reboot boots, in boot order.
    ordered = (ready[0], initrc[0], seed[0], preboot[0], reboot[0],
               initrc[1], ready[1], persisted[0], status1[0], gen1[0],
               identity[0], single[0], sync[0], resets[0],
               initrc[2], ready[2], status2[0], gen2[0], identity[1],
               pids[0], single[1])
    if any(a >= b for a, b in zip(ordered, ordered[1:])):
        return {"MIPSEL_AUTOSTART": _gate("NOT_TESTED", "autostart_chain_not_ordered")}
    if first_fail is None:
        return {"MIPSEL_AUTOSTART": _gate(
            "PASS" if report.get("outcome") == "PASS" else "FAIL",
            "native_reboot_then_qemu_reset_service_autostarted"
            if report.get("outcome") == "PASS" else "report_outcome_mismatch")}
    if first_fail <= seed[0]:
        return {"MIPSEL_AUTOSTART": _gate("NOT_TESTED", "prerequisite_failed")}
    return {"MIPSEL_AUTOSTART": _gate("FAIL", "autostart_chain_step_failed")}


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
             ported_scenario=None, ported_report=None,
             cli_scenario=None, cli_report=None,
             entware_scenario=None, entware_report=None,
             service_scenario=None, service_report=None,
             persistence_scenario=None, persistence_report=None,
             autostart_scenario=None, autostart_report=None):
    gates = {name: _gate("NOT_TESTED", "unattempted") for name in GATES}
    gates.update(derive_openwrt_gates(openwrt_scenario, openwrt_report))
    if ported_scenario is not None and ported_report is not None:
        gates.update(derive_ported_gates(ported_scenario, ported_report))
    if cli_scenario is not None and cli_report is not None:
        gates.update(derive_cli_gates(cli_scenario, cli_report))
    if entware_scenario is not None and entware_report is not None:
        gates.update(derive_entware_gates(entware_scenario, entware_report))
    if service_scenario is not None and service_report is not None:
        gates.update(derive_service_restart_gate(service_scenario, service_report))
    if persistence_scenario is not None and persistence_report is not None:
        gates.update(derive_persistence_gate(persistence_scenario, persistence_report))
    if autostart_scenario is not None and autostart_report is not None:
        gates.update(derive_autostart_gate(autostart_scenario, autostart_report))
    apply_labels(gates, statuses, reasons)
    return gates


def outcome_of(gates):
    values = list(gates.values())
    return ("FAIL" if any(v["status"] == "FAIL" for v in values)
            else "BLOCKED" if any(v["status"] != "PASS" for v in values)
            else "PASS")


def ported_claim(gates):
    """Summary footer sentence derived from the evaluated Ported gates.

    The boot claim must follow the evaluated gate status, never assert it
    unconditionally: with the Ported report omitted (or a failed Ported
    boot) the footer must not state a successful NDMS boot."""
    boot = gates["PORTED_KERNEL_BOOT"]
    smp = gates["PORTED_KERNEL_SMP_4VPE"]
    ndms = gates["PORTED_NDMS_READY"]
    if all(gate["status"] == "PASS" for gate in (boot, smp, ndms)):
        claim = ("KeeneticOS Ported boots 4 VPEs to NDMS in QEMU (derived "
                 "from the committed scenario report)")
        open_gates = [name for name in ("PORTED_NDMC_READY",
                                        "MIPSEL_ENTWARE_READY",
                                        "MIPSEL_SERVICE_RESTART",
                                        "MIPSEL_PERSISTENCE")
                      if gates[name]["status"] != "PASS"]
        if open_gates:
            claim += ("; still open: " + ", ".join(open_gates)
                      + (" (%s)" % ", ".join(gates[n]["reason"] for n in open_gates)))
        return claim + "."
    return ("KeeneticOS Ported readiness: kernel=%s (%s); "
            "4-VPE SMP=%s (%s); NDMS=%s (%s)."
            % (boot["status"], boot["reason"], smp["status"],
               smp["reason"], ndms["status"], ndms["reason"]))


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
    ap.add_argument("--cli-scenario",
                    help="committed ndm CLI telnet scenario JSON")
    ap.add_argument("--cli-report",
                    help="report.json of the ndm CLI telnet scenario")
    ap.add_argument("--entware-scenario",
                    help="committed Entware clean-bootstrap scenario JSON")
    ap.add_argument("--entware-report",
                    help="report.json of the Entware clean-bootstrap scenario")
    ap.add_argument("--service-scenario",
                    help="committed Entware disposable-service Stage C scenario")
    ap.add_argument("--service-report",
                    help="report.json of the independent service-restart scenario")
    ap.add_argument("--persistence-scenario",
                    help="committed Entware native-reboot and QEMU-reset Stage C scenario")
    ap.add_argument("--persistence-report",
                    help="report.json of the independent Stage C persistence scenario")
    ap.add_argument("--autostart-scenario",
                    help="committed Entware rc.unslung autostart Stage C scenario")
    ap.add_argument("--autostart-report",
                    help="report.json of the independent rc.unslung autostart scenario")
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
        cli_scenario = cli_report = None
        if args.cli_scenario and args.cli_report:
            cli_scenario = json.loads(
                Path(args.cli_scenario).read_text(encoding="utf-8"))
            cli_report = json.loads(
                Path(args.cli_report).read_text(encoding="utf-8"))
        entware_scenario = entware_report = None
        if args.entware_scenario and args.entware_report:
            entware_scenario = json.loads(
                Path(args.entware_scenario).read_text(encoding="utf-8"))
            entware_report = json.loads(
                Path(args.entware_report).read_text(encoding="utf-8"))
        service_scenario = service_report = None
        if args.service_scenario and args.service_report:
            service_scenario = json.loads(
                Path(args.service_scenario).read_text(encoding="utf-8"))
            service_report = json.loads(
                Path(args.service_report).read_text(encoding="utf-8"))
        persistence_scenario = persistence_report = None
        if args.persistence_scenario and args.persistence_report:
            persistence_scenario = json.loads(
                Path(args.persistence_scenario).read_text(encoding="utf-8"))
            persistence_report = json.loads(
                Path(args.persistence_report).read_text(encoding="utf-8"))
        autostart_scenario = autostart_report = None
        if args.autostart_scenario and args.autostart_report:
            autostart_scenario = json.loads(
                Path(args.autostart_scenario).read_text(encoding="utf-8"))
            autostart_report = json.loads(
                Path(args.autostart_report).read_text(encoding="utf-8"))
        reasons = {name: why for name, why in args.reason}
        gates = evaluate(scenario, report, args.set, reasons,
                         ported_scenario, ported_report,
                         cli_scenario, cli_report,
                         entware_scenario, entware_report,
                         service_scenario, service_report,
                         persistence_scenario, persistence_report,
                         autostart_scenario, autostart_report)
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
              + ported_claim(gates) + " Native MIPSel ELF execution in the "
              "OpenWrt guest is NOT an Entware check on KeeneticOS.", ""]
    (report_dir / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    print("gates outcome:", outcome, "->", report_dir / "gates.json")


if __name__ == "__main__":
    main()

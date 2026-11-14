#!/usr/bin/env python3
"""Aggregate MT7621 gate evidence into the lab JSON/JUnit contract.

Reads the report.json files written by lab/run.py (or records explicitly
labelled statuses from the controller) and writes gates.json, junit.xml and
summary.md for the MT7621 stand.  Statuses are never upgraded: BLOCKED and
NOT_TESTED stay what they are.  Every entry carries a fixed reason label.

Usage:
  python3 lab/mt7621/mt7621_gates.py --report-dir DIR \
      --openwrt-report PATH [--ported-report PATH] \
      [--ported-validation PASS|BLOCKED --reason LABEL] ...
"""
import argparse
import json
from pathlib import Path
import xml.etree.ElementTree as ET

GATES = (
    "MT7621_QEMU_BUILD", "MT7621_OPENWRT_BOOT", "MT7621_REBOOT",
    "PORTED_IMAGE_VALIDATION", "PORTED_KERNEL_BOOT", "PORTED_NDMC_READY",
    "MIPSEL_ENTWARE_READY", "MIPSEL_NATIVE_ELF", "MIPSEL_SERVICE_RESTART",
    "MIPSEL_PERSISTENCE", "NEGATIVE_IMAGE_REJECT", "HOST_ISOLATION", "CLEANUP",
)
STATUSES = ("PASS", "FAIL", "BLOCKED", "NOT_TESTED")


def summarize_openwrt_report(path, boot_gate, reboot_gate):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    steps = data["steps"]
    total = len(steps)
    reboot_at = next((i for i, s in enumerate(steps) if s["action"] == "reset"), None)
    ok = data["outcome"] == "PASS"
    if ok:
        return {boot_gate: {"status": "PASS", "reason": "openwrt_boot_stages_observed"},
                reboot_gate: {"status": "PASS", "reason": "second_boot_observed"}}
    if reboot_at is not None and all(s["outcome"] == "PASS" for s in steps[:reboot_at]):
        return {boot_gate: {"status": "PASS", "reason": "openwrt_boot_stages_observed"},
                reboot_gate: {"status": "FAIL", "reason": "serial_timeout"}}
    return {boot_gate: {"status": "FAIL", "reason": "serial_timeout"},
            reboot_gate: {"status": "NOT_TESTED", "reason": "prerequisite_blocked"}}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--report-dir", required=True)
    ap.add_argument("--openwrt-report", required=True,
                    help="report.json of the OpenWrt boot/ELF/reboot scenario")
    ap.add_argument("--set", action="append", nargs=2, metavar=("GATE", "STATUS"),
                    default=[], help="explicit gate status for gates without a scenario report")
    ap.add_argument("--reason", action="append", nargs=2, metavar=("GATE", "REASON"),
                    default=[])
    args = ap.parse_args()
    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)

    gates = {name: {"status": "NOT_TESTED", "reason": "unattempted"} for name in GATES}
    gates.update(summarize_openwrt_report(args.openwrt_report,
                                         "MT7621_OPENWRT_BOOT", "MT7621_REBOOT"))
    reasons = {name: why for name, why in args.reason}
    for name, status in args.set:
        if name not in GATES or status not in STATUSES:
            ap.error("unapproved gate/status: %s %s" % (name, status))
        gates[name] = {"status": status,
                       "reason": reasons.get(name, "controller_labelled")}
    values = list(gates.values())
    outcome = ("FAIL" if any(v["status"] == "FAIL" for v in values)
               else "BLOCKED" if any(v["status"] != "PASS" for v in values)
               else "PASS")
    report = {"schema": 1, "platform": "MT7621_MIPSEL_QEMU_XIAOMI_R3G",
              "outcome": outcome, "hardware_acceptance": "NOT_TESTED",
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
    lines += ["", "Physical-router acceptance: NOT_TESTED. NDMS and Entware on "
              "Keenetic Ported remain BLOCKED; native MIPSel ELF execution in "
              "the OpenWrt guest is NOT an Entware check on KeeneticOS.", ""]
    (report_dir / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    print("gates outcome:", outcome, "->", report_dir / "gates.json")


if __name__ == "__main__":
    main()

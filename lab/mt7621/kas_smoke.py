#!/usr/bin/env python3
"""Real pinned KAS installer smoke inside isolated Ported MT7621/MIPSel.

The controller never executes KAS on the Ubuntu host. The public KAS
checkout is verified by immutable commit SHA, then its exact install.sh
is copied by authenticated loopback SSH into a disposable guest. Guest
stdout/stderr remain private; only fixed result categories are published.
An inability to satisfy the real KeeneticOS component contract is a
BLOCKED installation, not an emulator regression or an invented PASS.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
from types import SimpleNamespace

from lab.build_entware import build
from lab.mirror import Mirror
from lab.run import run
from lab.kas_mirror import load_sources

LOCK = Path(__file__).resolve().parents[1] / "entware-mipselsf.lock.json"
PIN = re.compile(r"^[0-9a-f]{40}$")



REQUIRED_KEENETIC_COMPONENTS = ("proxy", "dns-filter", "opkg-kmod-netfilter",
                               "ext", "ext-utils")
ONE_SECURE_DNS = ("dns-tls", "dns-https")


def missing_required_components(version_output):
    """Return only known component IDs, never raw device/version text.

    Mirrors KAS install.sh component_list_from_show_version field parsing:
    continuation lines can wrap even across a hyphenated identifier.
    An absent/unparseable inventory is UNKNOWN, not an invented missing set.
    """
    collect = False
    rows = []
    for line in version_output.splitlines():
        if not collect:
            match = re.match(r"^\s*components:\s*(.*)$", line)
            if match is None:
                continue
            collect = True
            rows.append(re.sub(r"\s+", "", match.group(1)))
            continue
        value = line.strip().replace(" ", "").replace("\t", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]+(?:,[A-Za-z0-9_-]+)*,?", value):
            break
        rows.append(value)
    if not collect:
        return None
    observed = set("".join(rows).split(","))
    absent = [item for item in REQUIRED_KEENETIC_COMPONENTS if item not in observed]
    if not any(x in observed for x in ONE_SECURE_DNS):
        absent.append("dns-tls-or-dns-https")
    return absent

def classify_failure(code, output):
    """Map private installer output to an allowlisted public failure category."""
    if code == 0 and "[OK] Done" in output:
        return "INSTALLER_COMPLETED"
    low = output.lower()
    if ("full required component contract" in low
            or "enable the missing component" in low
            or "required component" in low and "missing" in low):
        return "KEENETICOS_COMPONENTS_MISSING"
    if "opkg update failed" in low:
        return "OPKG_FEED_UNAVAILABLE"
    if ("failed to install" in low or "package unavailable" in low
            or "mihomo install failed" in low):
        return "IPK_UNAVAILABLE"
    if "architecture" in low and ("unsupported" in low or "cannot" in low):
        return "MIPSEL_ABI_UNSUPPORTED"
    if "download failed" in low or "curl" in low and "failed" in low:
        return "PINNED_ASSET_UNAVAILABLE"
    if code == 0:
        return "SUCCESS_MARKER_MISSING"
    return "INSTALLER_EXIT_NONZERO"


def write_public(path, kas_ref, phase, reason, exit_code=None, component_gap=None):
    """No guest output, passwords, paths, script bodies, or arbitrary diagnostics."""
    record = {"schema": "mt7621-kas-smoke-v1", "kas_ref": kas_ref,
              "phase": phase, "reason": reason, "kas_installed": phase == "PASS",
              "exit_code": exit_code if isinstance(exit_code, int) else None,
              "component_inventory_seen": component_gap is not None,
              "missing_component_ids": component_gap if component_gap is not None else []}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, sort_keys=True, indent=2) + "\n",
                    encoding="utf-8")


def guest_probe(credentials, workspace, deadline, source, kas_ref, public):
    """Trusted run.py post-scenario hook; VM remains alive for the whole probe."""
    from lab.guest import Guest  # lazy: unit tests do not require Paramiko
    guest = Guest(credentials, workspace, deadline=deadline)
    phase, reason, exit_code = "NOT_TESTED", "GUEST_NOT_REACHED", None
    component_gap = None
    try:
        guest.connect(120)
        phase, reason = "BLOCKED", "ENTWARE_PREFLIGHT_UNAVAILABLE"
        required = ('test -x /opt/bin/opkg && test -x /opt/bin/bash && '
                    'test -r /opt/etc/init.d/rc.unslung && '
                    'grep -q " /opt ext4 " /proc/mounts && '
                    '/opt/bin/opkg print-architecture | '
                    'grep -q "mipsel-3.4_kn 200"')
        guest.command(required, timeout=45)
        version_output = guest.command('/bin/ndmc -c "show version"', timeout=45)
        component_gap = missing_required_components(version_output)
        if component_gap is None:
            print("KAS COMPONENT INVENTORY: UNKNOWN", flush=True)
        else:
            print("KAS COMPONENT INVENTORY: verified required IDs, missing=%d" %
                  len(component_gap), flush=True)
        print("KAS MIPSEL PREFLIGHT: native Entware and ndmc reachable", flush=True)
        phase, reason = "BLOCKED", "PINNED_SOURCE_DELIVERY_FAILED"
        guest.command("mkdir -p /opt/lab; chmod 700 /opt/lab", timeout=30)
        # Entware Dropbear does not necessarily ship an SFTP subsystem.
        # Reuse the proven Paramiko SSH-stdin upload contract from the ARM64 lab:
        # authenticated loopback exec + private stdin + native checksum.
        guest.command("umask 077; cat > /opt/lab/install.sh; "
                      "chmod 600 /opt/lab/install.sh",
                      timeout=90, input_data=source)
        expected = hashlib.sha256(source).hexdigest()
        observed = guest.command("/opt/bin/busybox sha256sum /opt/lab/install.sh",
                                 timeout=30).split()[0]
        if expected != observed:
            raise RuntimeError("pinned guest source digest mismatch")
        print("KAS MIPSEL SOURCE: pinned checkout transferred and verified", flush=True)

        # A real KAS installation attempt. No host-side mock or fake PASS.
        # The guest has an offline WAN and a pinned 25-IPK local feed. Current
        # Ported images may not have all production KeeneticOS components.
        phase, reason = "BLOCKED", "INSTALLER_NOT_COMPLETED"
        command = ("KEENETIC_AUTO_SETUP_REF=" + kas_ref +
                   " PATH=/opt/bin:/opt/sbin:/bin:/sbin:/usr/bin:/usr/sbin" +
                   " sh /opt/lab/install.sh disk")
        exit_code, stdout, stderr = guest.execute(command, timeout=300)
        category = classify_failure(exit_code, stdout + "\n" + stderr)
        if category != "INSTALLER_COMPLETED":
            reason = category
            print("KAS MIPSEL INSTALL: " + category, flush=True)
            raise RuntimeError("guest KAS install not accepted: " + category)
        installed = guest.command("/opt/bin/opkg list-installed", timeout=45)
        if not all(name + " - " in installed for name in ("mihomo", "magitrickle")):
            reason = "INSTALLED_PACKAGES_MISSING"
            raise RuntimeError("KAS package postcondition failed")
        phase, reason = "PASS", "PINNED_INSTALLER_AND_PACKAGE_CONTRACT"
        print("KAS MIPSEL INSTALL: PASS (actual installed packages)", flush=True)
    except TimeoutError:
        reason = "GUEST_PROBE_TIMEOUT"
        raise RuntimeError("KAS guest timed out") from None
    finally:
        guest.close()
        write_public(public, kas_ref, phase, reason, exit_code,
                     component_gap=component_gap)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--emulator-root", required=True)
    ap.add_argument("--nand-dir", required=True)
    ap.add_argument("--workspace", required=True, help="MUST be a fresh, private directory")
    ap.add_argument("--credentials-file", required=True)
    ap.add_argument("--kas-dir", required=True, help="immutable pinned public KAS checkout")
    ap.add_argument("--kas-ref", required=True)
    ap.add_argument("--max-seconds", type=int, default=2400)
    args = ap.parse_args()
    if not PIN.fullmatch(args.kas_ref):
        ap.error("--kas-ref must be full 40-character SHA")
    source = load_sources(args.kas_dir, args.kas_ref)["install.sh"]
    if not source.startswith(b"#!/bin/sh"):
        ap.error("KAS installer header mismatch")
    workspace = Path(args.workspace)
    workspace.mkdir(mode=0o700, exist_ok=False)
    creds = json.loads(Path(args.credentials_file).read_text(encoding="utf-8"))
    if creds.get("username") != "root":
        ap.error("expected isolated root credential")
    image, feed = workspace / "opt.img", workspace / "mirror"
    build(image, feed, workspace / "cache", args.credentials_file,
          arch="mipsel", lock_path=LOCK)
    scenario = Path(__file__).resolve().parents[1] / "scenarios" / "ported-mt7621-entware-tz-skip.json"
    public = workspace / "report" / "kas-smoke.json"
    with Mirror(feed) as port:
        return run(SimpleNamespace(
            scenario=scenario, emulator_root=args.emulator_root,
            nand_dir=args.nand_dir, output_dir=workspace / "report",
            max_seconds=args.max_seconds, preset="xiaomi-mi-router-3g-breed",
            lan_forwards="8080:80;8022:222;8023:23", opt_image=str(image),
            archive_image=None, mirror_port=port,
            credentials_file=args.credentials_file,
            after_scenario=lambda **ctx: guest_probe(args.credentials_file,
                workspace, ctx["deadline"], source, args.kas_ref, public)))


if __name__ == "__main__":
    raise SystemExit(main())

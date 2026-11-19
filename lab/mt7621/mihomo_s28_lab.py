"""Real pinned Mihomo S28 MIPSel IPK E2E inside the isolated Ported MT7621 guest.

Two frozen artifacts, never rebuilt and never downloaded from a live feed at
guest time:
- mihomo_1.19.34-2_mipsel-3.4.ipk — the currently published entware-go
  `latest` asset (sha256 290c0802…, also mirrored by the KAS release
  fixture).  It ships the legacy S99mihomo init name.
- mihomo_1.19.34-3_mipsel-3.4.ipk — the PR #67 revision-aware no-publish CI
  artifact (entware-go Actions run 44565066188).  It ships S28mihomo with
  byte-identical init code (same sha256 as the -2 S99 payload) and is the
  basis for all future S28 builds.

The host verifies full SHA-256 pins and a static IPK/ELF contract BEFORE any
guest work (fail closed).  The scenario replays the KN-1913 physical
sequence in the emulator (PR #66 handoff): install -2 (S99), mark the user
config, exercise the mixed-port proxy egress, upgrade to -3 — opkg removes
the stale package-owned S99 and leaves exactly one S28mihomo with the user
config preserved and the package sample stored as config.yaml-opkg — then
rollback to -2, re-upgrade, and one native KeeneticOS reboot with rc.unslung
autostart of the same configuration.

No feed/release mutation, no physical router, no real proxy credentials.
Guest stdout/stderr stay private; only fixed echo-safe markers and counts
leave a run.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

from lab.build_entware import add_packages, build
from lab.mt7621.kas_base_deps import (OVERLAY_LOCK, validated_kas_base_overlay)
from lab.mirror import Mirror
from lab.mt7621.telemt_lab import (MAX_IPK, AuditError, elf_mips_info,
                                   ipk_parts)
from lab.run import run

LOCK = Path(__file__).resolve().parents[2] / "lab" / "entware-mipselsf.lock.json"
SCENARIO = (Path(__file__).resolve().parents[2] / "lab" / "scenarios"
            / "ported-mt7621-mihomo-s28.json")
SCENARIOS = {"mihomo-s28": SCENARIO}
MIRROR_HOST = "192.168.1.100"

# Frozen artifact pins.  -2 = published `latest` asset; -3 = PR #67 CI run
# 44565066188 (no-publish matrix, S28 init, PKG_RELEASE 3).
V2_IPK_NAME = "mihomo_1.19.34-2_mipsel-3.4.ipk"
V3_IPK_NAME = "mihomo_1.19.34-3_mipsel-3.4.ipk"
V2_IPK_SHA = "290c0802e60b12e38eb43f58656de3d8f0de317532a0bb67d2dda30b80fdadf6"
V3_IPK_SHA = "3176e3f402f7f7f10aca3659fb57f0cd53c4d9d2b34d3e270a620c0dfdfc948f"
# ELF payloads as shipped inside the pinned IPKs (verified in-guest too).
V2_ELF_SHA = "ad8c23ed6640da6cf2720eaac6281e4d91f2bf489e56ec424639ce89c600984a"
V3_ELF_SHA = "30857fb981b8778e7f25a07ca128b0891f6e3008f9495e55f719e7db09ce769c"
# The S99 (package -2) and S28 (package -3) init payloads are byte-identical.
INIT_SHA = "06ba8f6abe78a3b109d2037c902d826085d5a4814cf4b4aca0d135b22a7b86d9"
CONFIG_SHA = "7bed4b15f9758a0ae632af3a74fa61d5fba67926bb3ce598ed1ccf4a78e7298a"
V2_VERSION = "1.19.34-2"
V3_VERSION = "1.19.34-3"
PINS = ((V2_IPK_NAME, V2_IPK_SHA), (V3_IPK_NAME, V3_IPK_SHA))
CONTROL = {
    "v2": {"Package": "mihomo", "Version": V2_VERSION,
           "Architecture": "mipsel-3.4"},
    "v3": {"Package": "mihomo", "Version": V3_VERSION,
           "Architecture": "mipsel-3.4"},
}
PAYLOAD = {
    "v2": {"binary": "opt/bin/mihomo", "init": "opt/etc/init.d/S99mihomo",
           "config": "opt/etc/mihomo/config.yaml", "init_name": "S99mihomo"},
    "v3": {"binary": "opt/bin/mihomo", "init": "opt/etc/init.d/S28mihomo",
           "config": "opt/etc/mihomo/config.yaml", "init_name": "S28mihomo"},
}
ELF_SHA = {"v2": V2_ELF_SHA, "v3": V3_ELF_SHA}

SOFTFLOAT = 3
ENTWARE_INTERP = "/opt/lib/ld.so.1"


def audit_ipk(blob, kind, expected_elf_sha=None, expected_init_sha=None,
              expected_config_sha=None):
    """Full static contract for one pinned Mihomo IPK.

    Mihomo is a static Go binary: no PT_INTERP and no PT_DYNAMIC are
    expected, and the MIPS softfloat ABI must match the Entware target.
    The expected_* arguments let host tests exercise the same contract on
    synthetic fixtures; production audits pass them as None and pin to the
    frozen digests.
    """
    fields, payload = ipk_parts(blob)
    if fields != CONTROL[kind]:
        raise AuditError("control mismatch for %s: %r" % (kind, fields))
    paths = {PAYLOAD[kind]["binary"], PAYLOAD[kind]["init"],
             PAYLOAD[kind]["config"]}
    if set(payload) != paths:
        raise AuditError("unexpected payload paths for %s: %r"
                         % (kind, sorted(payload)))
    info = elf_mips_info(payload[PAYLOAD[kind]["binary"]])
    if info["fp_abi"] != SOFTFLOAT:
        raise AuditError("%s FP ABI %d != Entware softfloat %d"
                         % (kind, info["fp_abi"], SOFTFLOAT))
    if info["dynamic"] or info["interp"] is not None or info["e_type"] != 2:
        raise AuditError("mihomo must be a static ET_EXEC Go binary")
    if expected_elf_sha is None:
        expected_elf_sha = ELF_SHA[kind]
    digest = hashlib.sha256(payload[PAYLOAD[kind]["binary"]]).hexdigest()
    if digest != expected_elf_sha:
        raise AuditError("%s ELF SHA-256 mismatch" % kind)
    if expected_init_sha is None:
        expected_init_sha = INIT_SHA
    init_digest = hashlib.sha256(payload[PAYLOAD[kind]["init"]]).hexdigest()
    if init_digest != expected_init_sha:
        raise AuditError("%s init script SHA-256 mismatch" % kind)
    if expected_config_sha is None:
        expected_config_sha = CONFIG_SHA
    config_digest = hashlib.sha256(payload[PAYLOAD[kind]["config"]]).hexdigest()
    if config_digest != expected_config_sha:
        raise AuditError("%s config.yaml SHA-256 mismatch" % kind)
    if not payload[PAYLOAD[kind]["init"]].startswith(b"#!/bin/sh"):
        raise AuditError("%s init script is not a /bin/sh script" % kind)
    return {"control": fields, "elf": info,
            "ipk_sha256": hashlib.sha256(blob).hexdigest()}


def feed_provides():
    """Package names and Provides aliases of the pinned local feed: the
    25-package Entware bootstrap plus the validated KAS base overlay
    (ca-bundle and friends live in the overlay, not the bootstrap)."""
    provided = set()
    for packages in (json.loads(LOCK.read_text())["packages"],
                     validated_kas_base_overlay()["packages"]):
        for package in packages:
            provided.add(package["Package"])
            for alias in package.get("Provides", "").split(","):
                alias = alias.strip().split(" ", 1)[0]
                if alias:
                    provided.add(alias)
    return provided


def pin_dependencies_satisfied(ipk_dir, provided):
    """Fail closed when a pinned IPK needs a package the local feed cannot
    provide (run 44593450737: opkg refused mihomo because ca-bundle was not
    in the pinned 25-package feed)."""
    for name, _sha in PINS:
        blob = (Path(ipk_dir) / name).read_bytes()
        fields, _payload = ipk_parts(blob, extra_fields=("Depends",))
        missing = []
        for group in fields.get("Depends", "").split(","):
            group = group.strip()
            if not group:
                continue
            if not any(alt.strip() in provided
                       for alt in group.split("|")):
                missing.append(group)
        if missing:
            raise AuditError(
                "pinned feed cannot satisfy %s dependencies: %s"
                % (name, ", ".join(missing)))


def verify_ipk_dir(ipk_dir):
    """Fail-closed host gate over both frozen artifacts; returns a
    sanitized summary (pins and ELF facts only, no payload bytes)."""
    summary = {}
    for name, sha in PINS:
        path = Path(ipk_dir) / name
        if not path.is_file() or path.stat().st_size > MAX_IPK:
            raise AuditError("missing or oversized artifact: " + name)
        blob = path.read_bytes()
        if hashlib.sha256(blob).hexdigest() != sha:
            raise AuditError("IPK SHA-256 mismatch: " + name)
        kind = "v2" if name == V2_IPK_NAME else "v3"
        report = audit_ipk(blob, kind)
        summary[kind] = {"ipk_sha256": sha,
                         "elf_sha256": ELF_SHA[kind],
                         "init_sha256": INIT_SHA,
                         "config_sha256": CONFIG_SHA,
                         "init_name": PAYLOAD[kind]["init_name"],
                         "control": report["control"],
                         "fp_abi": report["elf"]["fp_abi"]}
    pin_dependencies_satisfied(ipk_dir, feed_provides())
    return summary


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--audit-ipks", action="store_true",
                    help="host-only: verify pinned artifacts and exit")
    ap.add_argument("--ipk-dir", required=True,
                    help="directory holding the two pinned Mihomo IPKs")
    ap.add_argument("--emulator-root")
    ap.add_argument("--nand-dir")
    ap.add_argument("--workspace", help="MUST be a fresh, private directory")
    ap.add_argument("--credentials-file")
    ap.add_argument("--max-seconds", type=int, default=3000)
    ap.add_argument("--scenario", choices=sorted(SCENARIOS),
                    default="mihomo-s28")
    args = ap.parse_args()

    summary = verify_ipk_dir(args.ipk_dir)
    if args.audit_ipks:
        print(json.dumps({"status": "STATIC_IPK_CONTRACT_PASS",
                          "v2": summary["v2"], "v3": summary["v3"]},
                         sort_keys=True, indent=2))
        return 0
    if not (args.emulator_root and args.nand_dir and args.workspace
            and args.credentials_file):
        ap.error("guest run requires --emulator-root/--nand-dir/--workspace/"
                 "--credentials-file")

    workspace = Path(args.workspace)
    workspace.mkdir(mode=0o700, exist_ok=False)
    credentials = json.loads(Path(args.credentials_file).read_text(encoding="utf8"))
    if credentials.get("username") != "root":
        raise ValueError("dropbear SSH probes authenticate as root")
    image, feed = workspace / "opt.img", workspace / "mirror"
    build(str(image), str(feed), workspace / "cache", args.credentials_file,
          arch="mipsel", lock_path=str(LOCK))
    # mihomo Depends: ca-bundle, which lives in the validated KAS base
    # overlay, not the 25-package bootstrap.  Extend only the disposable
    # mirror (add_packages verifies every digest); the proven bootstrap
    # lock stays untouched (run 44593450737 lesson).
    validated_kas_base_overlay()
    add_packages(feed, workspace / "cache", OVERLAY_LOCK)
    # Loopback-only IPK delivery: the same pinned bytes, verified above,
    # served beside the pinned 25-package feed at /entware/<name>.
    for name, _sha in PINS:
        source = Path(args.ipk_dir) / name
        target = feed / "entware" / name
        target.write_bytes(source.read_bytes())
    with Mirror(feed) as port:
        return run(SimpleNamespace(
            scenario=str(SCENARIOS[args.scenario]),
            emulator_root=args.emulator_root,
            nand_dir=args.nand_dir, output_dir=workspace / "report",
            max_seconds=args.max_seconds, preset="xiaomi-mi-router-3g-breed",
            lan_forwards="8080:80;8022:222;8023:23", opt_image=str(image),
            archive_image=None, mirror_port=port,
            credentials_file=args.credentials_file))


if __name__ == "__main__":
    sys.exit(main())

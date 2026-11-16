#!/usr/bin/env python3
"""Real pinned TeleMT MIPSel IPK E2E inside the isolated Ported MT7621 guest.

The two experimental IPKs are frozen CI artifacts of entware-go Draft PR #44
(Actions run 43742529964, verified 2026-11-16): the host verifies full
SHA-256 pins and a static IPK/ELF contract BEFORE any guest work, the
scenario delivers the same pinned bytes through the loopback-only mirror,
installs them with the guest's own native opkg, and starts Core as a local
daemon bound to 127.0.0.1 only with the synthetic lab configuration (no real
Telegram credentials, no upstream middle-proxy, isolated WAN).  The scenario
then proves stop/restart, one native KeeneticOS reboot and the real
rc.unslung autostart of the same configuration.

No rebuild of the experimental artifacts, no feed/release mutation, no
physical device.  The cloud workflow feeds this module pinned artifact
bytes only; without the acquisition token the guest stage is intentionally
skipped rather than approximated.  Guest stdout/stderr stay private; only
fixed echo-safe markers and counts leave a run.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import struct
import sys
import tarfile

from lab.build_entware import build
from lab.mirror import Mirror
from lab.run import run

LOCK = Path(__file__).resolve().parents[2] / "lab" / "entware-mipselsf.lock.json"
SCENARIO = (Path(__file__).resolve().parents[2] / "lab" / "scenarios"
            / "ported-mt7621-telemt.json")
MIRROR_HOST = "192.168.1.100"

# Frozen experimental artifacts: entware-go Draft PR #44, Actions run
# 43742529964 (artifacts present, never rebuilt, never published to latest).
CORE_IPK_SHA = "b8dfbb83380d8f4353151b0de43d8c34ceb2369aea45cb6ddaf3b3089a798e5d"
PANEL_IPK_SHA = "0236dfb076b14b129a6392de49f287838a11aebe7681e092084d4ce87e771e68"
# ELF payloads as shipped inside the pinned IPKs (verified in-guest too).
CORE_ELF_SHA = "3efa4a53c1d50bf1b7a0ed6ca7de98201faf0fa2e27eaaf5f1cd589a6bf26063"
PANEL_ELF_SHA = "380cb5112b58538eafec9ae9fab27fb22c994fd36ce3130c541c5e11b29a40c8"
CORE_IPK_NAME = "telemt_3.5.17-1_mipsel-3.4.ipk"
PANEL_IPK_NAME = "telemt-panel-lite_1.0.0.rc.4-1_mipsel-3.4.ipk"
PINS = ((CORE_IPK_NAME, CORE_IPK_SHA), (PANEL_IPK_NAME, PANEL_IPK_SHA))
CONTROL = {
    "core": {"Package": "telemt", "Version": "3.5.17-1",
             "Architecture": "mipsel-3.4"},
    "panel": {"Package": "telemt-panel-lite", "Version": "1.0.0~rc.4-1",
              "Architecture": "mipsel-3.4"},
}
PAYLOAD_BINARY = {"core": "opt/bin/telemt", "panel": "opt/bin/telemt-panel"}

MAX_IPK = 16 * 1024 * 1024
MAX_MEMBER = 32 * 1024 * 1024
ENTWARE_INTERP = "/opt/lib/ld.so.1"
SOFTFLOAT = 3


class AuditError(ValueError):
    pass


def tar_members(blob, label):
    try:
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
            out = {}
            total = 0
            for member in tar.getmembers():
                if member.name.startswith("/") or ".." in member.name.split("/"):
                    raise AuditError("unsafe tar path: %s" % label)
                if member.issym() or member.islnk() or member.isdev():
                    raise AuditError("unsafe tar member type: %s" % label)
                if not member.isfile():
                    continue
                if member.size > MAX_MEMBER or member.name.removeprefix("./") in out:
                    raise AuditError("oversized or duplicated member: %s" % label)
                total += member.size
                if total > MAX_MEMBER:
                    raise AuditError("payload exceeds bound: %s" % label)
                stream = tar.extractfile(member)
                if stream is None:
                    raise AuditError("unreadable member: %s" % label)
                out[member.name.removeprefix("./")] = stream.read(MAX_MEMBER + 1)
                if len(out[member.name.removeprefix("./")]) > MAX_MEMBER:
                    raise AuditError("member too large: %s" % label)
            return out
    except (tarfile.TarError, OSError) as error:
        raise AuditError("invalid gzip tar: %s" % label) from error


def ipk_parts(blob):
    """Split an .ipk (tar.gz outer, as produced by the pinned packer) into
    control fields and the data payload members."""
    outer = tar_members(blob, "outer IPK")
    control_members = [k for k in outer if k.startswith("control.tar.")]
    data_members = [k for k in outer if k.startswith("data.tar.")]
    if len(control_members) != 1 or len(data_members) != 1 \
            or not control_members[0].endswith(".gz") \
            or not data_members[0].endswith(".gz"):
        raise AuditError("expected unique control.tar.gz and data.tar.gz")
    control = tar_members(outer[control_members[0]], "control.tar.gz")
    payload = tar_members(outer[data_members[0]], "data.tar.gz")
    if "control" not in control:
        raise AuditError("missing control file")
    fields = {}
    for line in control["control"].decode("utf-8", "strict").splitlines():
        if ": " not in line:
            continue
        key, value = line.split(": ", 1)
        if key in {"Package", "Version", "Architecture"}:
            if key in fields:
                raise AuditError("duplicated %s in control" % key)
            fields[key] = value
    return fields, payload


def elf_mips_info(binary):
    """ELF32 little-endian MIPS facts: fp_abi from PT_MIPS_ABIFLAGS, the
    PT_INTERP string and whether a PT_DYNAMIC segment exists."""
    if len(binary) < 52 or binary[:4] != b"\x7fELF":
        raise AuditError("not an ELF binary")
    if binary[4:6] != b"\x01\x01" or struct.unpack_from("<H", binary, 18)[0] != 8:
        raise AuditError("not ELF32 little-endian MIPS")
    e_type = struct.unpack_from("<H", binary, 16)[0]
    phoff = struct.unpack_from("<I", binary, 28)[0]
    phentsize, phnum = struct.unpack_from("<HH", binary, 42)
    if phnum > 256 or (phnum and phentsize < 32) \
            or phoff + phentsize * phnum > len(binary):
        raise AuditError("invalid ELF program header table")
    interp = None
    dynamic = False
    fp_abis = []
    for index in range(phnum):
        at = phoff + index * phentsize
        p_type, p_offset, _, _, p_filesz = struct.unpack_from(
            "<IIIII", binary, at)
        if p_offset + p_filesz > len(binary):
            raise AuditError("ELF segment outside file")
        if p_type == 0x70000003:  # PT_MIPS_ABIFLAGS
            if p_filesz != 24:
                raise AuditError("malformed PT_MIPS_ABIFLAGS")
            fp_abis.append(binary[p_offset + 7])
        elif p_type == 2:
            dynamic = True
        elif p_type == 3:
            interp = binary[p_offset:p_offset + p_filesz].rstrip(b"\0") \
                .decode("ascii", "replace")
    if len(fp_abis) != 1:
        raise AuditError("expected exactly one PT_MIPS_ABIFLAGS segment")
    return {"e_type": e_type, "interp": interp, "dynamic": dynamic,
            "fp_abi": fp_abis[0]}


def audit_ipk(blob, kind, expected_elf_sha=None):
    """Full static contract for one pinned experimental IPK."""
    fields, payload = ipk_parts(blob)
    if fields != CONTROL[kind]:
        raise AuditError("control mismatch for %s: %r" % (kind, fields))
    if set(payload) != {PAYLOAD_BINARY[kind]}:
        raise AuditError("unexpected payload paths for %s: %r"
                         % (kind, sorted(payload)))
    info = elf_mips_info(payload[PAYLOAD_BINARY[kind]])
    if info["fp_abi"] != SOFTFLOAT:
        raise AuditError("%s FP ABI %d != Entware softfloat %d"
                         % (kind, info["fp_abi"], SOFTFLOAT))
    if kind == "core":
        if not info["dynamic"] or info["interp"] != ENTWARE_INTERP:
            raise AuditError("core must be dynamic against " + ENTWARE_INTERP)
    else:
        if info["dynamic"] or info["interp"] is not None \
                or info["e_type"] != 2:
            raise AuditError("panel must be a static ET_EXEC Go binary")
    digest = hashlib.sha256(payload[PAYLOAD_BINARY[kind]]).hexdigest()
    if expected_elf_sha is None:
        expected_elf_sha = CORE_ELF_SHA if kind == "core" else PANEL_ELF_SHA
    if digest != expected_elf_sha:
        raise AuditError("%s ELF SHA-256 mismatch" % kind)
    return {"control": fields, "elf": info,
            "ipk_sha256": hashlib.sha256(blob).hexdigest()}


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
        kind = "core" if name == CORE_IPK_NAME else "panel"
        report = audit_ipk(blob, kind)
        summary[kind] = {"ipk_sha256": sha,
                         "elf_sha256": CORE_ELF_SHA if kind == "core"
                         else PANEL_ELF_SHA,
                         "control": report["control"],
                         "fp_abi": report["elf"]["fp_abi"],
                         "interpreter": report["elf"]["interp"]}
    return summary


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--audit-ipks", action="store_true",
                    help="host-only: verify pinned artifacts and exit")
    ap.add_argument("--ipk-dir", required=True,
                    help="directory holding the two pinned experimental IPKs")
    ap.add_argument("--emulator-root")
    ap.add_argument("--nand-dir")
    ap.add_argument("--workspace", help="MUST be a fresh, private directory")
    ap.add_argument("--credentials-file")
    ap.add_argument("--max-seconds", type=int, default=3000)
    args = ap.parse_args()

    summary = verify_ipk_dir(args.ipk_dir)
    if args.audit_ipks:
        print(json.dumps({"status": "STATIC_IPK_CONTRACT_PASS",
                          "core": summary["core"], "panel": summary["panel"]},
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
    # Loopback-only IPK delivery: the same pinned bytes, verified above,
    # served beside the pinned 25-package feed at /entware/<name>.
    for name, _sha in PINS:
        source = Path(args.ipk_dir) / name
        target = feed / "entware" / name
        target.write_bytes(source.read_bytes())
    with Mirror(feed) as port:
        return run(SimpleNamespace(
            scenario=str(SCENARIO), emulator_root=args.emulator_root,
            nand_dir=args.nand_dir, output_dir=workspace / "report",
            max_seconds=args.max_seconds, preset="xiaomi-mi-router-3g-breed",
            lan_forwards="8080:80;8022:222;8023:23", opt_image=str(image),
            archive_image=None, mirror_port=port,
            credentials_file=args.credentials_file))


if __name__ == "__main__":
    sys.exit(main())

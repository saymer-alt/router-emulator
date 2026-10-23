#!/usr/bin/env python3
"""Experimental disposable MT7981 flash assembly (never flash physical routers).

Input: already prepared official OpenWrt U-Boot-mod partition folder, public
WR3000P KeenBOOT FIP candidate, WR3000P Ported FIT+SquashFS image.
Uses the KB-41/KB-51 runtime NMBM partition map, not OpenWrt's map.
The OpenWrt BL2 still needs a FIP mirror at 0x3c0000 (synthetic factory).
Copy-only: source files are never changed, and output stays local/private.
"""
import argparse
import hashlib
from pathlib import Path
import shutil


EXPECTED = {
    "ported": ("710e4a37b1aa9f3a65a5122edd0650f5c5ad3e6eca5df53b11ac28aab2664eb7", 31588412),
    "keenboot": ("8b8615b345d912187853fea6bef6b1868760fd52ed92ba7c3a380ad5533cbf08", 306784),
}
PARTS = {0: 0x100000, 1: 0x80000, 2: 0x200000, 3: 0x40000, 4: 0x200000}
TOTAL = 128 * 1024 * 1024
FIP_OFFSET = 0x80000
BL2_FIP_OFFSET = 0x3c0000
FIRMWARE_OFFSET = 0x500000
FIRMWARE_END = 0x3c00000
NMBM_START = 0x7800000


def write_at(files, offset, data):
    """Write by physical address across QEMU backing files, not guest MTD IDs."""
    if offset < 0 or offset + len(data) > TOTAL:
        raise ValueError("write exceeds NAND")
    start = 0
    consumed = 0
    for path in files:
        end = start + path.stat().st_size
        if offset < end and consumed < len(data):
            local = max(offset - start, 0)
            count = min(end - start - local, len(data) - consumed)
            with path.open("r+b") as fh:
                fh.seek(local)
                fh.write(data[consumed:consumed + count])
            consumed += count
            offset += count
        start = end


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prepare(stock, ported, keenboot, out):
    stock, ported, keenboot, out = (Path(p).resolve() for p in (stock, ported, keenboot, out))
    if not stock.is_dir() or not ported.is_file() or not keenboot.is_file():
        raise ValueError("missing stock NAND folder or Ported/KeenBOOT file")
    if out.exists():
        raise ValueError("output dir must NOT exist (no overwrite)")
    if stock in out.parents or out in stock.parents or out in (stock, ported, keenboot):
        raise ValueError("output must be separate from all sources")
    expected, length = EXPECTED["ported"]
    if ported.stat().st_size != length or file_sha256(ported) != expected:
        raise ValueError("Ported 5.1.8 SHA256/size mismatch: refuse")
    with ported.open("rb") as fh:
        if fh.read(4) != bytes.fromhex("d00dfeed"):
            raise ValueError("not a FIT-image header")
    boot = keenboot.read_bytes()
    expected, length = EXPECTED["keenboot"]
    if len(boot) != length or file_sha256(keenboot) != expected:
        raise ValueError("requires pinned KeenBOOT KB-41; KB-51 rejects Ported 5.1.8 before FIT boot")
    if boot[:4] != bytes.fromhex("010064aa"):
        # If this is an alternate image type, do not put arbitrary bytes in
        # the FIP slot and pretend BL2 can load them. Needs manual analysis.
        raise ValueError("KeenBOOT candidate is not a standalone FIP (header mismatch)")
    files = {}
    for no in range(6):
        found = sorted(stock.glob("mt7981.mtd%d.*.bin" % no))
        if len(found) != 1:
            raise ValueError("stock partition mtd%d not unique/missing" % no)
        files[no] = found[0]
        if no in PARTS and found[0].stat().st_size != PARTS[no]:
            raise ValueError("stock partition mtd%d size mismatch" % no)
    if sum(f.stat().st_size for f in files.values()) != TOTAL:
        raise ValueError("stock partition sizes do not total 128 MiB")
    bl2 = files[0].read_bytes()
    if any(value != 0xff for value in bl2[FIP_OFFSET:]):
        raise ValueError("BL2 extends into KeenBOOT FIP slot: refuse overlap")
    if len(boot) > FIRMWARE_OFFSET - BL2_FIP_OFFSET:
        raise ValueError("BL2 FIP mirror would overlap firmware")
    if ported.stat().st_size > FIRMWARE_END - FIRMWARE_OFFSET:
        raise ValueError("Ported exceeds primary firmware slot")
    out.mkdir(parents=True, mode=0o700)
    try:
        # Start erased: never copy OpenWrt UBI, random identity, or a Factory
        # dump into the guest's differently located factory/NMBM regions.
        for source in files.values():
            path = out / source.name
            size = source.stat().st_size
            with path.open("wb") as fh:
                block = bytes([0xff]) * (1024 * 1024)
                leftover = size
                while leftover:
                    nbytes = min(len(block), leftover)
                    fh.write(block[:nbytes])
                    leftover -= nbytes
        backing = [out / files[n].name for n in range(6)]
        write_at(backing, 0, bl2[:FIP_OFFSET])
        write_at(backing, FIP_OFFSET, boot)
        write_at(backing, BL2_FIP_OFFSET, boot)
        write_at(backing, FIRMWARE_OFFSET, ported.read_bytes())
    except Exception:
        shutil.rmtree(out, ignore_errors=True)
        raise
    print("CANDIDATE: pinned KB-41; FIP 0x80000, BL2 mirror 0x3c0000, raw FIT 0x500000")
    print("Synthetic factory; NMBM tail erased. Virtual boot is not hardware acceptance.")
    print("Private candidate folder:", out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stock-nand", required=True)
    ap.add_argument("--ported", required=True)
    ap.add_argument("--keenboot", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    try:
        prepare(a.stock_nand, a.ported, a.keenboot, a.out)
    except (ValueError, OSError) as e:
        ap.error(str(e))


if __name__ == "__main__":
    main()

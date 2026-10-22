#!/usr/bin/env python3
"""Experimental disposable MT7981 flash assembly (never flash physical routers).

Input: already prepared official OpenWrt U-Boot-mod partition folder, public
WR3000P KeenBOOT FIP candidate, WR3000P Ported FIT+SquashFS image.
This is a RESEARCH CANDIDATE, not a verified KeenBOOT/OEM NAND layout.
Copy-only: source files are never changed, and output stays local/private.
"""
import argparse
import hashlib
from pathlib import Path
import shutil


EXPECTED = {
    "ported": ("710e4a37b1aa9f3a65a5122edd0650f5c5ad3e6eca5df53b11ac28aab2664eb7", 31588412),
}
PARTS = {0: 0x100000, 1: 0x80000, 2: 0x200000, 3: 0x40000, 4: 0x200000}
TOTAL = 128 * 1024 * 1024


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
    if ported.open("rb").read(4) != bytes.fromhex("d00dfeed"):
        raise ValueError("not a FIT-image header")
    boot = keenboot.read_bytes()
    if not 0x20000 <= len(boot) <= PARTS[4]:
        raise ValueError("KeenBOOT length unsuitable for candidate FIP partition")
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
    out.mkdir(parents=True, mode=0o700)
    try:
        for source in files.values():
            shutil.copy2(source, out / source.name)
        def replace_partition(n, data):
            path = out / files[n].name
            size = path.stat().st_size
            if len(data) > size:
                raise ValueError("replacement exceeds mtd%d" % n)
            with path.open("wb") as fh:
                fh.write(data)
                block = bytes([0xff]) * (1024 * 1024)
                leftover = size - len(data)
                while leftover:
                    nbytes = min(len(block), leftover)
                    fh.write(block[:nbytes])
                    leftover -= nbytes
        replace_partition(4, boot)
        replace_partition(5, ported.read_bytes())
    except Exception:
        shutil.rmtree(out, ignore_errors=True)
        raise
    print("CANDIDATE: BL2 OpenWrt ubootmod + FIP KeenBOOT + raw FIT Ported at start of mtd5")
    print("WARNING: layout/BL2-FIP ABI and KeenBOOT flash addressing UNVERIFIED; no OS PASS implied.")
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

#!/usr/bin/env python3
"""Find the three SHA256-pinned public MT7621 parts in a 7z extraction.

Never trust the vendor archive's names or directory layout: only pinned
lengths and digests from prepare_ported_mir3g.py identify the components.
"""
import argparse
from pathlib import Path
import shutil
from lab.mt7621.prepare_ported_mir3g import EXPECTED, check_component, file_sha256

PARTS = ("uconfig", "ustate", "eeprom")


def locate(unpacked):
    root = Path(unpacked)
    if not root.is_dir():
        raise ValueError("missing extracted installer directory")
    found = {}
    for path in root.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        possible = [k for k in PARTS if k not in found
                    and path.stat().st_size == EXPECTED[k][1]]
        if not possible:
            continue
        digest = file_sha256(path)
        for key in possible:
            if digest == EXPECTED[key][0]:
                found[key] = path
                break
    missing = [key for key in PARTS if key not in found]
    if missing:
        raise ValueError("installer lacks pinned part(s): " + ", ".join(missing))
    return found


def stage(unpacked, output):
    output = Path(output)
    if output.exists():
        raise ValueError("staging output already exists")
    matches = locate(unpacked)
    output.mkdir(parents=True, mode=0o700)
    try:
        for key in PARTS:
            target = output / (key + ".bin")
            shutil.copyfile(matches[key], target)
            check_component(key, target)
    except Exception:
        shutil.rmtree(output)
        raise
    return output


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--unpacked", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    try:
        stage(args.unpacked, args.out)
    except (OSError, ValueError) as error:
        ap.error(str(error))
    print("Three public NAND components verified by pinned SHA256")


if __name__ == "__main__":
    main()

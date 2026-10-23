"""Render only fixed, allowlisted UART evidence; never echo guest lines."""
import argparse
from pathlib import Path
import re


MARKERS = (
    ("BL2", r"NOTICE:  BL2: v2\.13\.0"),
    ("BL31", r"NOTICE:  BL31: v2\.14\.0"),
    ("KeenBOOT KB-41", r"Version: \(KB-41 \[54e0ab3\]\)"),
    ("KeenBOOT KB-51 (incompatible default boot)", r"Version: KB-51 \[ff51234\]"),
    ("NMBM attached/created", r"NMBM has been successfully (?:created|attached)"),
    ("Reading 'firmware' from 0x500000", r"Reading 'firmware' from 0x500000"),
    ("FIT kernel hash OK", r"Verifying Hash Integrity \.\.\. crc32\+ sha1\+ OK"),
    ("Starting kernel", r"Starting kernel \.\.\."),
    ("Linux version 4.9-ndm-5", r"Linux version 4\.9-ndm-5"),
    ("SquashFS root mounted", r"VFS: Mounted root \(squashfs filesystem\)"),
    ("Network Device Manager 5.01.C.8.0-2", r"Core::Main: \*\*\* Network Device Manager 5\.01\.C\.8\.0-2\."),
    ("ERROR: *** Image verification failed ***", r"\*\*\* Image verification failed \*\*\*"),
    ("ERROR: *** Trying firmware image 2 ***", r"\*\*\* Trying firmware image 2 \*\*\*"),
    ("ERROR: kernel panic", r"Kernel panic - not syncing:"),
)


def evidence(data):
    return [label for label, pattern in MARKERS if re.search(pattern, data)]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("console_dir", type=Path)
    args = ap.parse_args()
    logs = sorted(args.console_dir.glob("console_*.log"))
    if not logs:
        print("No UART evidence available")
        return
    data = logs[-1].read_bytes()[-2500000:].decode("utf8", "replace")
    print("ALLOWLISTED UART EVIDENCE (fixed labels only):")
    for label in evidence(data):
        print(label)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Analyze public evaluation firmware structure without redistributing its bytes.

Safe on GitHub-hosted ephemeral CI: report metadata, not extracted content.
All files stay in the runner's temporary directory. No modifying routers.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct


MAGIC = {
    "fit/uImage FDT": b"\xd0\x0d\xfe\xed",
    "squashfs": b"hsqs",
    "UBI eraseblock": b"UBI#",
    "gzip": b"\x1f\x8b\x08",
    "7z": b"7z\xbc\xaf\x27\x1c",
    "LZMA-alone": b"\x5d\x00\x00\x80\x00",
    "ELF": b"\x7fELF",
    "ZIP": b"PK\x03\x04",
    "uImage": b"\x27\x05\x19\x56",
    "XZ": b"\xfd7zXZ\x00",
}
LIMIT_MB = 200


def inspect_file(path):
    p = Path(path)
    if not p.is_file() or not 0 < p.stat().st_size <= LIMIT_MB * 1024 * 1024:
        raise ValueError("missing file or outside 1..200 MiB")
    data = p.read_bytes()
    hdr = data[:64]
    results = []
    for name, signature in MAGIC.items():
        pos = 0
        offsets = []
        while len(offsets) < 12:
            pos = data.find(signature, pos)
            if pos == -1:
                break
            offsets.append(pos)
            pos += len(signature)
        if offsets:
            results.append({"signature": name, "offsets": offsets, "more_than_12": data.find(signature, offsets[-1] + len(signature)) >= 0 if len(offsets) == 12 else False})
    payload = {
        "name": p.name,
        "bytes": len(data),
        "md5": hashlib.md5(data).hexdigest(),
        "sha256": hashlib.sha256(data).hexdigest(),
        "header_first_32_hex": hdr[:32].hex(),
        "recognized_signatures": results,
    }
    # FIT first at position 0: meaningful total size is the next 4 bytes.
    if len(data) >= 8 and data[:4] == MAGIC["fit/uImage FDT"]:
        payload["fit_total_size"] = struct.unpack(">I", data[4:8])[0]
    return payload


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="+")
    parser.add_argument("--metadata-only", action="store_true")
    opts = parser.parse_args()
    for path in opts.files:
        print(json.dumps(inspect_file(path), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()

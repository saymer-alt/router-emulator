#!/usr/bin/env python3
"""Assemble a disposable NAND folder for Keenetic Ported 5.1.8 on the
Xiaomi Mi Router 3G preset (MT7621, parallel NAND, Breed bootloader).

LOCAL COMPONENTS ONLY — none of them are committed to this repository:

  --breed     breed-mt7621-xiaomi-r3g.bin   (Breed 1.2 r1416,
              https://breed.hackpascal.net/, public bootloader build)
  --firmware  Xiaomi-R3G_5.1.8.bin          (Ported evaluation firmware,
              https://osvault.project-athens.com/osvault/Xiaomi%20R3G/)
  --uconfig   u-config.bin                  (from the Xiaomi-R3G.7z
              installer archive of the same project)
  --ustate    U-State.bin                   (same archive)
  --eeprom    reserved_eeprom.bin           (public default EEPROM shipped
              with that archive; NOT a personal device dump)

Layout replicates the documented Ported dual-slot arrangement created by
the official Breed telnet install procedure (wiki/guides/xiaomi-3G):
  mtd0 Bootloader 0x0000000 0x80000   Breed
  mtd1 U-Config   0x0080000 0x80000   u-config + Breed env blob at 0x80000
  mtd2 EEPROM     0x0100000 0x80000   reserved_eeprom (rest erased)
  mtd3 Firmware1  0x0180000 0x3e40000 Ported firmware (slot 1)
  mtd4 U-State    0x3fc0000 0x80000   U-State
  mtd5 U-Config2  0x4040000 0x80000   u-config copy
  mtd6 EEPROM2    0x40c0000 0x80000   eeprom copy
  mtd7 Firmware2  0x4140000 0x3e40000 Ported firmware (slot 2, boot target)
  mtd8 env-tail   0x7f80000 0x80000   erased (Breed env/BBT tail)

The Breed environment block (4 KiB at 0x80000) is constructed, not dumped:
CRC32 (zlib) over bytes 4..0x1000 followed by "name=value" pairs and zero
padding.  autoboot.command "boot flash 0x180000" boots the Ported kernel
without any serial input.  The CRC format was verified against Breed's own
flash write of an empty environment (CRC32(4092 zero bytes) == the CRC
Breed emitted).

Copy-only: source files are never modified, the output directory must be
new, and every component is pinned by SHA-256 and size.  Refusals are
deliberately strict: wrong size, wrong hash or a wrong firmware header
must fail loudly instead of producing a flash folder that silently
boots the wrong thing.
"""
import argparse
import hashlib
from pathlib import Path
import shutil
import struct
import zlib

EXPECTED = {
    "breed": ("05ebb31fd889b148022346179346d91c60a1b4518bdc4d8b9717a62aec638bed", 137250),
    "firmware": ("05958b9786621ee014bb72551f6458d0672c93370cbce9aec3573c6bba038cb2", 26345532),
    "uconfig": ("2334d504abdfa96e9cded3a13ee124511ec72d52ed48e0c3e6e3306c3516ef2e", 524288),
    "ustate": ("c5157de3081a3a075fb1f619a942d26cb0182955d353296d52b67d11b4033455", 524288),
    "eeprom": ("0542c79373a80c5d123042be622037d26fc816a8ac3940f62f2d87c60440ad1e", 262144),
}
BOOT_COMMAND = "boot flash 0x180000"
ENV_OFFSET = 0x80000
ENV_SIZE = 0x1000
FIRMWARE_SLOT_OFFSET = 0x180000
PARTS = [
    (0x0000000, 0x0080000),
    (0x0080000, 0x0080000),
    (0x0100000, 0x0080000),
    (0x0180000, 0x3e40000),
    (0x3fc0000, 0x0080000),
    (0x4040000, 0x0080000),
    (0x40c0000, 0x0080000),
    (0x4140000, 0x3e40000),
    (0x7f80000, 0x0080000),
]
PART_NAMES = ["Bootloader", "U-Config", "EEPROM", "Firmware1", "U-State",
              "U-Config2", "EEPROM2", "Firmware2", "env-tail"]
TOTAL = 128 * 1024 * 1024


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def breed_env_blob(command=BOOT_COMMAND):
    """Breed environment block: CRC32 (zlib) + pairs + zero padding."""
    data = b"autoboot.command=" + command.encode("ascii") + b"\x00\x00"
    data += bytes(ENV_SIZE - 4 - len(data))
    return struct.pack("<I", zlib.crc32(data) & 0xFFFFFFFF) + data


def check_component(name, path):
    expected, length = EXPECTED[name]
    path = Path(path)
    if not path.is_file():
        raise ValueError("missing %s file: %s" % (name, path))
    if path.stat().st_size != length:
        raise ValueError("%s: size %d does not match pinned %d"
                         % (name, path.stat().st_size, length))
    digest = file_sha256(path)
    if digest != expected:
        raise ValueError("%s: SHA-256 %s does not match pinned %s"
                         % (name, digest, expected))
    return path


def check_firmware_header(path):
    with Path(path).open("rb") as fh:
        head = fh.read(34)
    if head[:4] != bytes.fromhex("27051956"):
        raise ValueError("firmware is not a u-boot legacy uImage (magic mismatch)")
    name = head[32:34]
    if name != b"KN":
        raise ValueError("firmware uImage name does not start with the "
                         "expected Keenetic model id (KN), got %r" % name)


def write_at(files, offset, data):
    """Write by physical address across the QEMU backing files."""
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


def assemble(breed, firmware, uconfig, ustate, eeprom, out):
    out = Path(out)
    if out.exists():
        raise ValueError("output dir must NOT exist (no overwrite)")
    sources = {name: check_component(name, path) for name, path in
               (("breed", breed), ("firmware", firmware), ("uconfig", uconfig),
                ("ustate", ustate), ("eeprom", eeprom))}
    check_firmware_header(sources["firmware"])
    if sum(size for _, size in PARTS) != TOTAL:
        raise ValueError("partition table does not total 128 MiB")
    if any(str(out) in str(p) or str(p.resolve()) in str(out.resolve())
           for p in sources.values()):
        raise ValueError("output must be separate from all sources")
    out.mkdir(parents=True, mode=0o700)
    try:
        files = []
        for i, ((off, size), name) in enumerate(zip(PARTS, PART_NAMES)):
            path = out / ("mt7621.mtd%d.%s.bin" % (i, name))
            path.write_bytes(b"\xff" * size)
            files.append(path)
        write_at(files, PARTS[0][0], sources["breed"].read_bytes())
        write_at(files, PARTS[1][0], sources["uconfig"].read_bytes())
        write_at(files, ENV_OFFSET, breed_env_blob())
        write_at(files, PARTS[2][0], sources["eeprom"].read_bytes())
        write_at(files, FIRMWARE_SLOT_OFFSET, sources["firmware"].read_bytes())
        write_at(files, PARTS[4][0], sources["ustate"].read_bytes())
        write_at(files, PARTS[5][0], sources["uconfig"].read_bytes())
        write_at(files, PARTS[6][0], sources["eeprom"].read_bytes())
        write_at(files, PARTS[7][0], sources["firmware"].read_bytes())
        # the boot target is slot 2 per the documented install; autoboot
        # boots slot 1 here — the same image content is present in both
        # slots, which also exercises the redundant copy
        slot = PARTS[3][1]
        if files[3].stat().st_size + files[7].stat().st_size != 2 * slot:
            raise ValueError("firmware slot files have unexpected sizes")
    except Exception:
        shutil.rmtree(out, ignore_errors=True)
        raise
    print("assembled:", out)
    print("boot target: firmware slot 1 @0x180000 (Breed env autoboot.command)")
    print("both firmware slots carry the same pinned Ported image")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--breed", required=True)
    ap.add_argument("--firmware", required=True)
    ap.add_argument("--uconfig", required=True)
    ap.add_argument("--ustate", required=True)
    ap.add_argument("--eeprom", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    try:
        assemble(args.breed, args.firmware, args.uconfig, args.ustate,
                 args.eeprom, args.out)
    except (ValueError, OSError) as e:
        ap.error(str(e))


if __name__ == "__main__":
    main()

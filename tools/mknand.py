#!/usr/bin/env python3
"""
Build / edit a raw SPI-NAND image (with OOB) for the Router Emulator
(MT7981 and MT7986 boards).

NAND geometry: W25N01GV-like, 2048 byte pages + 64 byte OOB, 64 pages/block,
1024 blocks (128 MiB).  The output file holds every page as 2112 raw bytes,
exactly like a dump made with a NAND programmer.

Layout (the common OpenWrt "ubootmod" SPI-NAND layout of MT7981 boards):
  0x000000  BL2          (preloader.bin, contains SPINAND! + GFH header)
  0x100000  u-boot-env   (unused by OpenWrt U-Boot, env lives in UBI)
  0x180000  Factory      (Wi-Fi EEPROM)
  0x380000  bdinfo       (MAC address at 0xde00)
  0x3c0000  FIP          (BL31 + U-Boot)
  0x5c0000  ubi          (UBI: fit, recovery, ubootenv, rootfs_data ...)
With --no-bdinfo (boards without a bdinfo partition, MACs in Factory):
  0x380000  FIP, 0x580000 ubi

The emulator normally uses a directory of per-partition dumps (data only,
no OOB); every file whose name contains "mtdN" is used, ordered by N:
  nand/mt7981.mtd0.BL2.bin ... nand/mt7981.mtd5.ubi.bin
They are concatenated in mtd order to form the full flash; the emulator
writes changes back to these files.

Examples:
  mknand.py create -o nand/ --bl2 preloader.bin --fip bl31-uboot.fip \
      --factory mt7981.mtd2.Factory.bin --bdinfo bdinfo.bin \
      --fit sysupgrade.itb --recovery initramfs-recovery.itb
  mknand.py write  -i nand/ --part fip --file new.fip
  mknand.py split  -i full-dump.bin -o nand/          (full dump -> dir)
  mknand.py join   -i nand/ -o nand-raw.bin           (dir -> raw w/ OOB)
  mknand.py create -o nand.bin --bl2 preloader.bin --fip bl31-uboot.fip \
      --fit sysupgrade.itb --recovery initramfs-recovery.itb
  mknand.py write  -i nand.bin --offset 0x3c0000 --file new.fip
  mknand.py read   -i nand.bin --offset 0 --size 0x100000 -o bl2-dump.bin
  mknand.py --prefix mt7986 --no-bdinfo create -o nand/ --wifi-eeprom 7986 ...
                    (MT7986 board without a Factory dump: minimal Wi-Fi
                     EEPROM, OpenWrt has no default one for the MT7986)
  mknand.py mt7621-nand -o nand-mi-router-3g/ --bootloader uboot.bin \
      --factory factory.bin --kernel kernel1.bin --ubi rootfs0.bin
                    (MT7621 parallel NAND, Xiaomi layout; see below)
  mknand.py strip  -i nand.bin -o nand-nooob.bin      (remove OOB)
  mknand.py addoob -i dump-nooob.bin -o nand.bin      (add empty OOB)
"""
import argparse
import os
import random
import shutil
import subprocess
import sys
import tempfile
import zlib

PAGE = 2048
OOB = 64
PPB = 64
BLOCKS = 1024
BLOCK = PAGE * PPB
RAW_PAGE = PAGE + OOB
TOTAL = PAGE * PPB * BLOCKS
RAW_TOTAL = RAW_PAGE * PPB * BLOCKS

PARTS = {
    "bl2": (0x000000, 0x100000),
    "u-boot-env": (0x100000, 0x080000),
    "factory": (0x180000, 0x200000),
    "bdinfo": (0x380000, 0x040000),
    "fip": (0x3c0000, 0x200000),
    "ubi": (0x5c0000, TOTAL - 0x5c0000),
}
# file names like OpenWrt backups: <prefix>.mtdN.<label>.bin
PART_FILES = ["BL2", "u-boot-env", "Factory", "bdinfo", "FIP", "ubi"]
PREFIX = "mt7981"


def set_no_bdinfo():
    """Layout without the bdinfo partition: FIP at 0x380000, ubi at 0x580000."""
    global PART_FILES
    PARTS.pop("bdinfo")
    PARTS["fip"] = (0x380000, 0x200000)
    PARTS["ubi"] = (0x580000, TOTAL - 0x580000)
    PART_FILES = ["BL2", "u-boot-env", "Factory", "FIP", "ubi"]


def set_parts(spec):
    """Own partition layout "label:size,...", size "-" = rest of the flash;
    the labels BL2, Factory, bdinfo, FIP and ubi are what create fills."""
    global PART_FILES
    PARTS.clear()
    PART_FILES = []
    off = 0
    for item in spec.split(","):
        label, size = item.split(":")
        size = TOTAL - off if size == "-" else int(size, 0)
        PARTS[label.lower()] = (off, size)
        PART_FILES.append(label)
        off += size
    if off > TOTAL:
        sys.exit("partitions exceed the flash size")


def set_prefix(prefix):
    global PREFIX
    PREFIX = prefix


def set_flash_mb(mb):
    """Switch geometry to a 128 MiB (1024 blocks), 256 MiB (2048) or
    512 MiB (4096) flash."""
    global BLOCKS, TOTAL, RAW_TOTAL, OOB, RAW_PAGE
    BLOCKS = mb * 1024 * 1024 // BLOCK
    OOB = 128 if mb >= 256 else 64          # W25N02KV / W25N04KV: 128 byte OOB
    RAW_PAGE = PAGE + OOB
    TOTAL = PAGE * PPB * BLOCKS
    RAW_TOTAL = RAW_PAGE * PPB * BLOCKS
    PARTS["ubi"] = (0x5c0000, TOTAL - 0x5c0000)


def parse_int(s):
    return int(s, 0)


def logical_to_raw(data):
    """Convert a data-only image (len multiple of PAGE) to raw with OOB."""
    out = bytearray()
    for off in range(0, len(data), PAGE):
        out += data[off:off + PAGE].ljust(PAGE, b"\xff")
        out += b"\xff" * OOB
    return out


def raw_to_logical(raw):
    out = bytearray()
    for off in range(0, len(raw), RAW_PAGE):
        out += raw[off:off + PAGE]
    return out


def dir_files(path):
    """Files whose name contains "mtdN", ordered by partition number N."""
    import re
    files = {}
    for name in os.listdir(path):
        m = re.search(r"mtd([0-9]+)", name)
        if m:
            n = int(m.group(1))
            if n in files:
                sys.exit(f"{path}: two files for mtd{n}: {files[n]} and {name}")
            files[n] = name
    return [os.path.join(path, files[n]) for n in sorted(files)]


class Nand:
    def __init__(self, path=None):
        if path and os.path.isdir(path):
            data = b"".join(open(f, "rb").read() for f in dir_files(path))
            if len(data) > TOTAL:
                sys.exit(f"{path}: partition files are larger than the flash")
            self.raw = bytearray(logical_to_raw(data.ljust(TOTAL, b"\xff")))
            return
        if path:
            raw = open(path, "rb").read()
            if len(raw) == TOTAL:
                raw = logical_to_raw(raw)
            if len(raw) != RAW_TOTAL:
                sys.exit(f"{path}: size {len(raw)} is neither {RAW_TOTAL} "
                         f"(with OOB) nor {TOTAL} (data only)")
            self.raw = bytearray(raw)
        else:
            self.raw = bytearray(b"\xff" * RAW_TOTAL)

    def write(self, offset, data, erase=True):
        if offset % PAGE:
            sys.exit("offset must be page aligned")
        if erase:
            # erase whole blocks covered by the write
            first = offset // BLOCK
            last = (offset + max(len(data), 1) - 1) // BLOCK
            for b in range(first, last + 1):
                s = b * PPB * RAW_PAGE
                self.raw[s:s + PPB * RAW_PAGE] = b"\xff" * (PPB * RAW_PAGE)
        for i in range(0, len(data), PAGE):
            page = (offset + i) // PAGE
            chunk = data[i:i + PAGE].ljust(PAGE, b"\xff")
            s = page * RAW_PAGE
            self.raw[s:s + PAGE] = chunk

    def read(self, offset, size):
        out = bytearray()
        while size > 0:
            page, col = divmod(offset, PAGE)
            n = min(size, PAGE - col)
            s = page * RAW_PAGE + col
            out += self.raw[s:s + n]
            offset += n
            size -= n
        return bytes(out)

    def save(self, path):
        if path.endswith("/") or os.path.isdir(path):
            self.save_dir(path)
            return
        with open(path, "wb") as f:
            f.write(self.raw)

    def save_dir(self, path):
        """Write one data-only file per partition (mtd0..mtd5)."""
        os.makedirs(path, exist_ok=True)
        for f in dir_files(path):
            os.unlink(f)
        for i, (key, label) in enumerate(zip(PARTS, PART_FILES)):
            off, size = PARTS[key]
            fn = os.path.join(path, f"{PREFIX}.mtd{i}.{label}.bin")
            with open(fn, "wb") as f:
                f.write(self.read(off, size))
            print(f"  {fn}  (0x{off:07x}, 0x{size:x} bytes)")


def build_ubi(args, tmp):
    vols = []
    if getattr(args, "fip_in_ubi", False) and args.fip:
        # "spim-nand-ubi" BL2s load the FIP from the UBI volume "fip"
        vols.append(("fip", args.fip, "static", None))
    if getattr(args, "sysupgrade", None):
        # stock OpenWrt NAND layout: UBI volumes kernel + rootfs (+ data)
        import tarfile
        with tarfile.open(args.sysupgrade) as t:
            for m in t.getmembers():
                base = os.path.basename(m.name)
                if base in ("kernel", "root") and m.isfile():
                    out = os.path.join(tmp, base)
                    with open(out, "wb") as f:
                        f.write(t.extractfile(m).read())
        for name, fn in (("kernel", "kernel"), ("rootfs", "root")):
            path = os.path.join(tmp, fn)
            if not os.path.exists(path):
                sys.exit(f"{args.sysupgrade}: no '{fn}' in sysupgrade tar")
            vols.append((name, path, "dynamic", None))
    if getattr(args, "factory_volume", None):
        # Factory in a UBI volume "factory" (e.g. Asus BT8): the dump, or
        # erased with random MACs at 0x4 / 0xa (the Wi-Fi EEPROM layout)
        size = parse_int(args.factory_volume)
        data = bytearray(b"\xff" * size)
        if args.factory:
            fac = open(args.factory, "rb").read()[:size]
            data[:len(fac)] = fac
        else:
            mac = [0x02] + [random.randrange(256) for _ in range(5)]
            data[4:10] = bytes(mac)
            mac[5] = (mac[5] + 1) & 0xff
            data[10:16] = bytes(mac)
            print("factory volume: random MACs %s (+1)"
                  % ":".join("%02x" % b for b in data[4:10]))
        path = os.path.join(tmp, "factory")
        with open(path, "wb") as f:
            f.write(data)
        vols.append(("factory", path, "dynamic", None))
    if args.fit:
        vols.append(("fit", args.fit, "dynamic", None))
    if args.recovery:
        vols.append(("recovery", args.recovery, "dynamic", None))
    if not vols:
        return None
    ini = os.path.join(tmp, "ubinize.cfg")
    with open(ini, "w") as f:
        for i, (name, image, vtype, size) in enumerate(vols):
            f.write(f"[{name}]\nmode=ubi\nvol_id={i}\nvol_type={vtype}\n"
                    f"vol_name={name}\nimage={os.path.abspath(image)}\n\n")
        n = len(vols)
        if getattr(args, "sysupgrade", None):
            f.write(f"[rootfs_data]\nmode=ubi\nvol_id={n}\nvol_type=dynamic\n"
                    f"vol_name=rootfs_data\nvol_size=1MiB\nvol_flags=autoresize\n\n")
        else:
            for name in ("ubootenv", "ubootenv2"):
                f.write(f"[{name}]\nmode=ubi\nvol_id={n}\nvol_type=dynamic\n"
                        f"vol_name={name}\nvol_size=0x100000\n\n")
                n += 1
    out = os.path.join(tmp, "ubi.img")
    ubinize = shutil.which("ubinize") or shutil.which(
        "ubinize", path="/usr/sbin:/sbin:/usr/local/sbin")
    if not ubinize:
        sys.exit("ubinize not found (apt install mtd-utils)")
    subprocess.run([ubinize, "-o", out, "-p", str(BLOCK), "-m", str(PAGE),
                    "-s", str(PAGE), ini], check=True)
    return open(out, "rb").read()


# SPI-NOR boards (e.g. 16 MB, OpenWrt "firmware" FIT partition): default
# partition layout of the MT7981 NOR boards OpenWrt supports
NOR_PARTS = "BL2:0x40000,u-boot-env:0x10000,Factory:0x10000,bdinfo:0x10000,FIP:0x80000,firmware:-"


def strip_fwtool(data):
    """Remove fwtool metadata/signature trailers ("FWx0") from a
    sysupgrade image, as OpenWrt does before writing it to flash."""
    while len(data) >= 16 and data[-16:-12] == b"FWx0":
        size = int.from_bytes(data[-4:], "big")
        if size < 16 or size > len(data):
            break
        data = data[:-size]
    return data


def uboot_env(lines, size):
    """U-Boot environment of a redundant-env build (CRC32, flags, data)."""
    data = b"\0".join(l.encode() for l in lines) + b"\0\0"
    if len(data) > size - 5:
        sys.exit("U-Boot environment larger than %#x bytes" % size)
    data = data.ljust(size - 5, b"\0")
    return zlib.crc32(data).to_bytes(4, "little") + b"\1" + data


def nor_nand(args, env):
    """'nand' subfolder: the SPI-NAND next to the NOR (machine option
    nor-nand=on), one file per partition of --nand-parts.  --nand-file puts
    files at partition (or flash) offsets; --nand-sysupgrade builds a UBI image of the
    sysupgrade tar's kernel and root (volumes kernel, rootfs, rootfs_data;
    with --env-ubi also ubootenv / ubootenv2 holding the environment)."""
    total = args.nand_mb << 20
    parts, off = [], 0
    for item in args.nand_parts.split(","):
        label, size = item.split(":")
        size = total - off if size == "-" else int(size, 0)
        parts.append((label, size))
        off += size
    if off > total:
        sys.exit("NAND partitions exceed the flash size")
    starts, off = {}, 0
    for label, size in parts:
        starts[label] = off
        off += size
    place = []                  # (flash offset, data)

    def put(spec, data):
        # LABEL[@OFF] or @OFF (from the start of the flash)
        label, _, at = spec.partition("@")
        if label and label not in starts:
            sys.exit(f"no NAND partition {label}")
        place.append((starts.get(label, 0) + (int(at, 0) if at else 0), data))

    for spec in args.nand_file or []:
        where, _, fn = spec.partition("=")
        put(where, open(fn, "rb").read())
    if args.nand_sysupgrade:
        put(args.nand_ubi_at or parts[0][0],
            sysupgrade_ubi(args.nand_sysupgrade,
                           env if args.env_ubi else None, args.env_size))
    for at, blob in place:
        if at + len(blob) > total:
            sys.exit(f"{len(blob)} bytes at {at:#x} exceed the NAND size")
    out = os.path.join(args.output, "nand")
    os.makedirs(out, exist_ok=True)
    for f in dir_files(out):
        os.unlink(f)
    for i, (label, size) in enumerate(parts):
        start = starts[label]
        data = bytearray(b"\xff" * size)
        for at, blob in place:
            lo, hi = max(at, start), min(at + len(blob), start + size)
            if lo < hi:
                data[lo - start:hi - start] = blob[lo - at:hi - at]
        fn = os.path.join(out, f"{PREFIX}.mtd{i}.{label}.bin")
        open(fn, "wb").write(bytes(data))
        print(f"  {fn}  (NAND 0x{start:08x}, 0x{size:x} bytes)")


def sysupgrade_ubi(path, env, env_size):
    """UBI image (128 KiB PEBs) of an OpenWrt sysupgrade tar (kernel + root):
    kernel, rootfs, rootfs_data (autoresize) and, with env, the redundant
    U-Boot environment volumes ubootenv / ubootenv2."""
    import tarfile
    with tempfile.TemporaryDirectory() as tmp:
        with tarfile.open(path) as t:
            for m in t.getmembers():
                base = os.path.basename(m.name)
                if base in ("kernel", "root") and m.isfile():
                    with open(os.path.join(tmp, base), "wb") as f:
                        f.write(t.extractfile(m).read())
        vols = []
        for name, fn in (("kernel", "kernel"), ("rootfs", "root")):
            if not os.path.exists(os.path.join(tmp, fn)):
                sys.exit(f"{path}: no '{fn}' in sysupgrade tar")
            vols.append(f"[{name}]\nmode=ubi\nvol_type=dynamic\n"
                        f"vol_name={name}\nimage={os.path.join(tmp, fn)}\n")
        if env:
            for i, name in enumerate(("ubootenv", "ubootenv2")):
                fn = os.path.join(tmp, name)
                e = bytearray(uboot_env(env, env_size))
                e[4] = 1 - i            # flags: the first copy is newer
                e[:4] = zlib.crc32(bytes(e[5:])).to_bytes(4, "little")
                open(fn, "wb").write(bytes(e))
                vols.append(f"[{name}]\nmode=ubi\nvol_type=dynamic\n"
                            f"vol_name={name}\nimage={fn}\nvol_size=0x20000\n")
        vols.append("[rootfs_data]\nmode=ubi\nvol_type=dynamic\n"
                    "vol_name=rootfs_data\nvol_size=1MiB\nvol_flags=autoresize\n")
        ini = os.path.join(tmp, "ubinize.cfg")
        with open(ini, "w") as f:
            for i, v in enumerate(vols):
                f.write(v + f"vol_id={i}\n\n")
        out = os.path.join(tmp, "ubi.img")
        ubinize = shutil.which("ubinize") or shutil.which(
            "ubinize", path="/usr/sbin:/sbin:/usr/local/sbin")
        if not ubinize:
            sys.exit("ubinize not found (apt install mtd-utils)")
        subprocess.run([ubinize, "-o", out, "-p", str(BLOCK), "-m", str(PAGE),
                        "-s", str(PAGE), ini], check=True,
                       stdout=subprocess.DEVNULL)
        return open(out, "rb").read()


def factory_macs(data, base):
    """MT7621 factory (Xiaomi boards): MAC at 0xe000, +1 at 0xe006; the
    Wi-Fi EEPROMs at 0 and 0x8000 (if present) get +2 and +3."""
    mac = int(base.replace(":", ""), 16)
    for at, d in ((0xe000, 0), (0xe006, 1), (0x4, 2), (0x8004, 3)):
        if at < 0xe000 and data[at - 4:at - 2] == b"\xff\xff":
            continue            # no Wi-Fi EEPROM there
        data[at:at + 6] = ((mac + d) & 0xffffffffffff).to_bytes(6, "big")
    print(f"base MAC: {base}")


def cmd_nor(args):
    """Folder of NOR partition files: BL2, u-boot-env, Factory, bdinfo, FIP,
    firmware (OpenWrt sysupgrade.bin = FIT kernel + squashfs + jffs2 data)."""
    total = args.size_mb << 20
    parts, off = [], 0
    for item in args.parts.split(","):
        label, size = item.split(":")
        size = total - off if size == "-" else int(size, 0)
        parts.append((label, off, size))
        off += size
    if off > total:
        sys.exit("NOR partitions exceed the flash size")
    # --bl2: the first-stage bootloader (BL2, or "u-boot" on MT7621
    # boards, where one image holds TPL, SPL and U-Boot)
    files = {"BL2": args.bl2, "u-boot": args.bl2, "FIP": args.fip,
             "Factory": args.factory, "factory": args.factory,
             "bdinfo": args.bdinfo, "u-boot-env": args.env_bin}
    labels = [p[0] for p in parts]
    if not args.raw and not (args.bl2 and (args.fip or "FIP" not in labels)):
        sys.exit("nor: --bl2 and --fip, or --raw")
    raw = open(args.raw, "rb").read() if args.raw else None
    env = [l.rstrip("\n") for l in open(args.env) if l.strip()
           and not l.startswith("#")] if args.env else None
    os.makedirs(args.output, exist_ok=True)
    for f in dir_files(args.output):
        os.unlink(f)
    for i, (label, poff, size) in enumerate(parts):
        data = bytearray(b"\xff" * size)
        src = files.get(label)
        if args.raw:
            data[:] = raw[poff:poff + size].ljust(size, b"\xff")
        elif label == "firmware" and args.sysupgrade:
            img = strip_fwtool(open(args.sysupgrade, "rb").read())
            if len(img) > size:
                sys.exit(f"sysupgrade image ({len(img)} bytes) larger than "
                         f"the firmware partition ({size})")
            data[:len(img)] = img
        elif src:
            blob = open(src, "rb").read()[:size]   # e.g. a NAND-size Factory
            data[:len(blob)] = blob
            if label == "factory" and args.mac and "bdinfo" not in labels:
                factory_macs(data, args.mac)
        elif label == "bdinfo" or label == args.mac_part:
            mac = args.mac or "80:af:ca:%02x:%02x:%02x" % tuple(
                random.randrange(256) for _ in range(3))
            at = 0xde00 if label == "bdinfo" else args.mac_off
            data[at:at + 6] = bytes(int(x, 16) for x in mac.split(":"))
            print(f"base MAC: {mac}")
        elif label == "u-boot-env" and env and not args.env_ubi:
            data[:args.env_size] = uboot_env(env, args.env_size)
        fn = os.path.join(args.output, f"{PREFIX}.mtd{i}.{label}.bin")
        open(fn, "wb").write(bytes(data))
        print(f"  {fn}  (0x{poff:07x}, 0x{size:x} bytes)")
    if args.nand_file or args.nand_sysupgrade:
        nor_nand(args, env)
    print(f"wrote {args.output}")


# Xiaomi MT7621 boards with a 128 MiB parallel NAND (Mi Router 3G, ...):
# stock layout as in OpenWrt's mt7621_xiaomi_nand_128m.dtsi
XIAOMI_PARTS = ("Bootloader:0x80000,Config:0x40000,Bdata:0x40000,"
                "factory:0x40000,crash:0x40000,crash_syslog:0x40000,"
                "reserved0:0x40000,kernel_stock:0x400000,kernel:0x400000,"
                "ubi:0x7580000")
BBT_BLOCKS = 4


def cmd_mt7621_nand(args):
    """MT7621 parallel NAND folder (machine option flash=nand): one data
    file per partition, the bootloader in the first, the OpenWrt kernel
    (kernel1.bin) and UBI (rootfs0.bin) in "kernel" / "ubi", everything
    else erased.  (The MediaTek U-Boot's factory bad block table in the
    last blocks, outside all partitions, is put there by the emulator.)
    factory: MAC at 0xe000 (WAN), +1 at 0xe006 (GMAC);
    the Wi-Fi EEPROMs at 0 and 0x8000 (if present) get +2 and +3."""
    total = args.size_mb << 20
    parts, off = [], 0
    for item in args.parts.split(","):
        label, size = item.split(":")
        size = total - BBT_BLOCKS * BLOCK - off if size == "-" else int(size, 0)
        parts.append((label, off, size))
        off += size
    if off > total - BBT_BLOCKS * BLOCK:
        sys.exit("partitions overlap the factory BBT in the last blocks")
    files = {parts[0][0]: args.bootloader, "factory": args.factory,
             "kernel": args.kernel, "ubi": args.ubi}
    os.makedirs(args.output, exist_ok=True)
    for f in dir_files(args.output):
        os.unlink(f)
    for f in os.listdir(args.output):
        if f.endswith(".oob"):
            os.unlink(os.path.join(args.output, f))

    def put(i, label, poff, data):
        fn = os.path.join(args.output, f"{PREFIX}.mtd{i}.{label}.bin")
        open(fn, "wb").write(bytes(data))
        print(f"  {fn}  (0x{poff:07x}, 0x{len(data):x} bytes)")
        return fn

    for i, (label, poff, size) in enumerate(parts):
        data = bytearray(b"\xff" * size)
        if files.get(label):
            blob = open(files[label], "rb").read()
            if len(blob) > size:
                sys.exit(f"{files[label]} ({len(blob)} bytes) larger than "
                         f"{label} ({size})")
            data[:len(blob)] = blob
        if label == "factory":
            factory_macs(data, args.mac)
        put(i, label, poff, data)
    print(f"wrote {args.output}")


def wifi_eeprom(chip, mac):
    """Minimal MediaTek Wi-Fi EEPROM (mt7915 driver layout): chip ID, MAC,
    band 0 = 2.4 GHz, band 1 = 5 GHz, 4 paths and streams each, a target
    TX power of 20 dBm per path (MT7976 a-die layout); no calibration (the
    emulated radio never transmits)."""
    e = bytearray(0x1000)
    e[0:2] = chip.to_bytes(2, "little")
    e[4:10] = mac
    e[0x190] = (0 << 6) | (4 << 3) | 4
    e[0x191] = (1 << 6) | (4 << 3) | 4
    e[0x192] = e[0x193] = 4 << 5
    # target power, 0.5 dBm units: 2.4 GHz per path, 5 GHz per path and
    # channel group; without it the driver's power is 0 (shown as 6 dBm)
    e[0x441:0x445] = bytes([40] * 4)
    e[0x445:0x445 + 4 * 5] = bytes([40] * 20)
    return bytes(e)


def cmd_create(args):
    nand = Nand(args.input) if args.input else Nand()
    if args.wifi_eeprom and not args.factory:
        mac = bytes([0x02] + [random.randrange(256) for _ in range(5)])
        nand.write(PARTS["factory"][0], wifi_eeprom(int(args.wifi_eeprom, 16),
                                                    mac))
        print("Factory: minimal Wi-Fi EEPROM (chip %s), Wi-Fi MAC %s"
              % (args.wifi_eeprom, ":".join("%02x" % b for b in mac)))
    if args.bl2:
        nand.write(PARTS["bl2"][0], open(args.bl2, "rb").read())
    if args.fip and not args.fip_in_ubi:
        data = open(args.fip, "rb").read()
        if len(data) > PARTS["fip"][1]:
            sys.exit("FIP too large")
        nand.write(PARTS["fip"][0], data)
    if args.factory and not args.factory_volume:
        nand.write(PARTS["factory"][0], open(args.factory, "rb").read())
    if "bdinfo" not in PARTS:
        if args.bdinfo or args.mac:
            print("no bdinfo partition in this layout: --bdinfo/--mac ignored "
                  "(the MACs come from Factory)")
        args.bdinfo = args.mac = None
    if args.bdinfo:
        nand.write(PARTS["bdinfo"][0], open(args.bdinfo, "rb").read())
    mac = args.mac
    if mac is None and not args.input and not args.bdinfo and "bdinfo" in PARTS:
        mac = "80:af:ca:%02x:%02x:%02x" % tuple(random.randrange(256)
                                               for _ in range(3))
    if mac:
        b = bytes(int(x, 16) for x in mac.split(":"))
        if len(b) != 6:
            sys.exit("bad MAC")
        bd = bytearray(b"\xff" * PARTS["bdinfo"][1])
        bd[0xde00:0xde06] = b
        nand.write(PARTS["bdinfo"][0], bytes(bd))
        print(f"base MAC: {mac}")
    with tempfile.TemporaryDirectory() as tmp:
        ubi = build_ubi(args, tmp)
    if ubi is not None:
        if len(ubi) > PARTS["ubi"][1]:
            sys.exit("UBI image too large")
        # erase the full ubi partition first: fresh UBI attach
        off, size = PARTS["ubi"]
        nand.write(off, b"\xff" * size)
        nand.write(off, ubi, erase=False)
        if args.fip_in_ubi:
            # like ubiformat: every free PEB gets the (erase counter 0) EC
            # header ubinize wrote, so a BL2 scanning UBI for the FIP does
            # not report each erased block
            ech = ubi[:64]
            for b in range(len(ubi) // BLOCK, size // BLOCK):
                nand.write(off + b * BLOCK, ech.ljust(PAGE, b"\xff"),
                           erase=False)
    nand.save(args.output)
    print(f"wrote {args.output}")


def resolve_offset(args):
    if args.part:
        return PARTS[args.part][0]
    return args.offset


def cmd_write(args):
    nand = Nand(args.input)
    nand.write(resolve_offset(args), open(args.file, "rb").read())
    out = args.output or args.input
    nand.save(out)


def cmd_read(args):
    nand = Nand(args.input)
    off = resolve_offset(args)
    size = args.size or (PARTS[args.part][1] if args.part else TOTAL - off)
    open(args.output, "wb").write(nand.read(off, size))


def cmd_strip(args):
    open(args.output, "wb").write(raw_to_logical(open(args.input, "rb").read()))


def cmd_addoob(args):
    data = open(args.input, "rb").read()
    if len(data) != TOTAL:
        sys.exit(f"input must be {TOTAL} bytes")
    open(args.output, "wb").write(logical_to_raw(data))


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--no-bdinfo", action="store_true",
                   help="layout without bdinfo: FIP at 0x380000, ubi at 0x580000")
    p.add_argument("--flash-mb", type=int, choices=(128, 256, 512), default=128,
                   help="flash size in MB (256 for W25N02KV, 512 for W25N04KV)")
    p.add_argument("--parts", help="own layout, e.g. BL2:0x100000,"
                   "backup:0x80000,Factory:0x400000,bdinfo:0x40000,"
                   "FIP:0x200000,ubi:- (applied after --flash-mb)")
    p.add_argument("--prefix", default="mt7981",
                   help="partition file name prefix (default %(default)s)")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("create", help="create a NAND image")
    c.add_argument("-o", "--output", required=True,
                   help="raw image file (with OOB) or directory/ for "
                        "per-partition files")
    c.add_argument("-i", "--input", help="start from an existing image/dir")
    c.add_argument("--bl2")
    c.add_argument("--fip")
    c.add_argument("--factory")
    c.add_argument("--bdinfo", help="bdinfo partition dump (MAC at 0xde00)")
    c.add_argument("--mac", help="base MAC (bdinfo 0xde00)")
    c.add_argument("--fit", help="OpenWrt sysupgrade .itb -> UBI volume 'fit'")
    c.add_argument("--recovery", help="initramfs recovery .itb -> UBI 'recovery'")
    c.add_argument("--sysupgrade", help="stock-layout OpenWrt sysupgrade.bin (tar) "
                   "-> UBI volumes kernel, rootfs, rootfs_data")
    c.add_argument("--fip-in-ubi", action="store_true",
                   help="FIP as UBI volume 'fip' (BL2 built for spim-nand-ubi) "
                        "instead of a raw FIP partition")
    c.add_argument("--factory-volume", metavar="SIZE",
                   help="Factory as UBI volume 'factory' of SIZE bytes (the "
                        "--factory dump, else erased with random MACs at "
                        "0x4 / 0xa) instead of a raw partition")
    c.add_argument("--wifi-eeprom", metavar="CHIP",
                   help="without --factory: minimal Wi-Fi EEPROM for this chip "
                        "(hex, e.g. 7986) at the start of Factory")
    c.set_defaults(func=cmd_create)

    for name, fn, h in (("write", cmd_write, "write a file at an offset"),
                        ("read", cmd_read, "read data from the image")):
        w = sub.add_parser(name, help=h)
        w.add_argument("-i", "--input", required=True)
        w.add_argument("-o", "--output", required=(name == "read"))
        g = w.add_mutually_exclusive_group(required=True)
        g.add_argument("--offset", type=parse_int)
        g.add_argument("--part", choices=PARTS.keys())
        if name == "write":
            w.add_argument("--file", required=True)
        else:
            w.add_argument("--size", type=parse_int)
        w.set_defaults(func=fn)

    s = sub.add_parser("strip", help="raw image -> data-only image")
    s.add_argument("-i", "--input", required=True)
    s.add_argument("-o", "--output", required=True)
    s.set_defaults(func=cmd_strip)
    sp = sub.add_parser("split", help="image or dir -> per-partition dir")
    sp.add_argument("-i", "--input", required=True)
    sp.add_argument("-o", "--output", required=True)
    sp.set_defaults(func=lambda a: Nand(a.input).save_dir(a.output))
    j = sub.add_parser("join", help="per-partition dir -> raw image with OOB")
    j.add_argument("-i", "--input", required=True)
    j.add_argument("-o", "--output", required=True)
    j.set_defaults(func=lambda a: open(a.output, "wb").write(Nand(a.input).raw))
    n = sub.add_parser("nor", help="SPI-NOR flash folder (one file per partition)")
    n.add_argument("-o", "--output", required=True, help="output directory")
    n.add_argument("--size-mb", type=int, default=16)
    n.add_argument("--parts", default=NOR_PARTS,
                   help="label:size,... ('-' = rest of the flash), default: %(default)s")
    n.add_argument("--bl2")
    n.add_argument("--fip")
    n.add_argument("--raw", metavar="IMAGE",
                   help="image of the whole NOR (e.g. OpenWrt One "
                        "nor-factory.bin), split into the partitions")
    n.add_argument("--factory")
    n.add_argument("--bdinfo")
    n.add_argument("--mac", help="base MAC for a generated bdinfo (0xde00)")
    n.add_argument("--sysupgrade", help="OpenWrt sysupgrade.bin for 'firmware'")
    n.add_argument("--mac-part", metavar="LABEL",
                   help="partition holding the base MAC at offset 0 (binary, "
                        "e.g. Teltonika 'config'; generated like bdinfo's)")
    n.add_argument("--mac-off", type=parse_int, default=0,
                   help="offset of the MAC in --mac-part (default 0; "
                        "MT7621 boards: 0xe000 of 'factory')")
    n.add_argument("--env", metavar="FILE",
                   help="U-Boot environment (one name=value per line) for "
                        "'u-boot-env'")
    n.add_argument("--env-bin", metavar="FILE",
                   help="image of 'u-boot-env' as it is (e.g. Breed's "
                        "environment: \"ENV\\0\" + name=value\\0 ...)")
    n.add_argument("--env-size", type=parse_int, default=0x4000,
                   help="CONFIG_ENV_SIZE of the U-Boot (default 0x4000)")
    n.add_argument("--env-ubi", action="store_true",
                   help="the environment is in UBI volumes ubootenv / "
                        "ubootenv2 of --nand-sysupgrade (U-Boot "
                        "CONFIG_ENV_IS_IN_UBI), not in 'u-boot-env'")
    n.add_argument("--nand-mb", type=int, default=512,
                   help="SPI-NAND next to the NOR (machine option "
                        "nor-nand=on), subfolder 'nand': size")
    n.add_argument("--nand-parts", default="ubi:-",
                   help="NAND layout, label:size,... ('-' = rest)")
    n.add_argument("--nand-file", action="append", metavar="[LABEL]@OFF=FILE",
                   help="FILE at offset OFF of the NAND partition LABEL "
                        "(no LABEL: of the flash; may span partitions)")
    n.add_argument("--nand-sysupgrade", metavar="FILE",
                   help="OpenWrt sysupgrade.bin (tar): UBI image of it")
    n.add_argument("--nand-ubi-at", metavar="LABEL[@OFF]",
                   help="where that UBI image goes (default: start of the "
                        "first NAND partition)")
    n.set_defaults(func=cmd_nor)
    x = sub.add_parser("mt7621-nand",
                       help="MT7621 parallel NAND folder (Xiaomi layout)")
    x.add_argument("-o", "--output", required=True, help="output directory")
    x.add_argument("--size-mb", type=int, default=128)
    x.add_argument("--parts", default=XIAOMI_PARTS,
                   help="label:size,... ('-' = up to the factory BBT), "
                        "default: %(default)s")
    x.add_argument("--bootloader", required=True,
                   help="first partition: stock U-Boot or Breed image")
    x.add_argument("--factory", help="factory dump (Wi-Fi EEPROMs), MACs "
                                     "are replaced")
    x.add_argument("--mac", default="80:af:ca:11:22:33", help="base MAC")
    x.add_argument("--kernel", help="OpenWrt kernel1.bin for 'kernel'")
    x.add_argument("--ubi", help="OpenWrt rootfs0.bin (UBI image) for 'ubi'")
    x.set_defaults(func=cmd_mt7621_nand)
    a = sub.add_parser("addoob", help="data-only dump -> raw image")
    a.add_argument("-i", "--input", required=True)
    a.add_argument("-o", "--output", required=True)
    a.set_defaults(func=cmd_addoob)

    args = p.parse_args()
    set_prefix(args.prefix)
    set_flash_mb(args.flash_mb)
    if args.no_bdinfo:
        set_no_bdinfo()
    if args.parts:
        set_parts(args.parts)
    args.func(args)


if __name__ == "__main__":
    main()

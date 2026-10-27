#!/usr/bin/env python3
"""
Build an eMMC image for the emulator (machine option flash-type=emmc), e.g. for
the GL.iNet GL-MT6000 (MT7986A) with the OpenWrt U-Boot.

The image file is what the emulated eMMC holds, in this order:
  boot0   (--boot-mb, default 4 MB)  BL2 preloader ("EMMC_BOOT" header)
  boot1   (--boot-mb)                empty (or --boot1)
  user    GPT disk, the rest of the image (total size a power of 2)

User area layouts (GPT; partitions are found by name), --layout:
  gl-mt6000 (default)
    0x0400000  u-boot-env    512 KB  (also U-Boot's raw env offset)
    0x0480000  factory         2 MB  (Wi-Fi EEPROM, MAC at 0xa)
    0x0680000  fip             4 MB  (BL31 + U-Boot)
    0x1000000  kernel         32 MB  \\ OpenWrt squashfs-factory.bin =
    0x3000000  rootfs        512 MB  / kernel padded to 32 MB + rootfs
  bpi-r4 (Bananapi BPi-R4, OpenWrt's emmc-gpt.bin; --fit, --recovery)
    0x0400000  ubootenv      512 KB
    0x0480000  factory         2 MB
    0x0680000  fip             4 MB
    0x0c00000  recovery       32 MB  initramfs-recovery.itb
    0x4000000  production    448 MB  squashfs-sysupgrade.itb (FIT)
  wh3000-pro (Huasifei WH3000 Pro eMMC, vendor layout; rootfs is 2 GB on
              the real 8 GB chip, 512 MB here)
    0x0400000  u-boot-env    512 KB
    0x0480000  factory         2 MB  (Wi-Fi EEPROM, MAC at 0x4)
    0x0680000  fip             4 MB
    0x0a80000  config          4 MB
    0x0e80000  kernel         16 MB  \\ OpenWrt squashfs-sysupgrade.bin
    0x1e80000  rootfs        512 MB  / (tar: kernel FIT, root squashfs)

--boot-env writes an OpenWrt U-Boot environment (redundant, 2 x 256 KB at
the start of u-boot-env, as in OpenWrt's MT7981 eMMC builds) that reads
the FIT from the "kernel" partition and boots it: for boards whose
firmware OpenWrt builds but whose bootloader it does not, run with the
OpenWrt U-Boot of another board with the same SoC, boot medium and DRAM.

The file is sparse: unused space takes no disk space.

Examples:
  mkemmc.py -o emmc-gl-mt6000/mt7986.emmc.img --bl2 preloader.bin \\
      --fip bl31-uboot.fip --firmware squashfs-factory.bin
  mkemmc.py -o emmc-wh3000-pro/mt7981.emmc.img --layout wh3000-pro \\
      --bl2 preloader.bin --fip bl31-uboot.fip --boot-env \\
      --sysupgrade squashfs-sysupgrade.bin --wifi-chip 7981
"""
import argparse
import os
import random
import shutil
import subprocess
import sys
import tarfile
import zlib

MB = 1024 * 1024
LAYOUTS = {  # name, offset, size
    "gl-mt6000": [
        ("u-boot-env", 0x0400000, 0x80000),
        ("factory", 0x0480000, 0x200000),
        ("fip", 0x0680000, 0x400000),
        ("kernel", 0x1000000, 32 * MB),
        ("rootfs", 0x3000000, 512 * MB),
    ],
    "bpi-r4": [     # Bananapi BPi-R4 (emmc-gpt.bin): FIT in "production"
        ("ubootenv", 0x0400000, 0x80000),
        ("factory", 0x0480000, 0x200000),
        ("fip", 0x0680000, 0x400000),
        ("recovery", 0x0c00000, 32 * MB),
        ("production", 0x4000000, 448 * MB),
    ],
    "wh3000-pro": [
        ("u-boot-env", 0x0400000, 0x80000),
        ("factory", 0x0480000, 0x200000),
        ("fip", 0x0680000, 0x400000),
        ("config", 0x0a80000, 0x400000),
        ("kernel", 0x0e80000, 16 * MB),
        ("rootfs", 0x1e80000, 512 * MB),
    ],
}

# OpenWrt U-Boot environment: boot the FIT in the "kernel" partition
BOOT_ENV = [
    "loadaddr=0x46000000",
    "bootdelay=0",
    "bootcmd=run boot_kernel",
    # OpenWrt's U-Boot shows its boot menu instead of running bootcmd
    "bootmenu_0=Boot the system in the kernel partition.=run boot_kernel",
    "bootmenu_default=0",
    "bootmenu_delay=0",
    "boot_kernel=part start mmc 0 kernel part_addr && "
    "part size mmc 0 kernel part_size && "
    "mmc read $loadaddr $part_addr $part_size && bootm $loadaddr",
]
ENV_SIZE = 0x40000


def uboot_env(lines, flags):
    """One copy of a redundant U-Boot environment: CRC32, flags, data."""
    data = b"\0".join(l.encode() for l in lines) + b"\0\0"
    data = data.ljust(ENV_SIZE - 5, b"\0")
    return zlib.crc32(data).to_bytes(4, "little") + bytes([flags]) + data


def sysupgrade_parts(path):
    """kernel and root of an OpenWrt sysupgrade tar."""
    out = {}
    with tarfile.open(path) as t:
        for m in t.getmembers():
            name = m.name.rsplit("/", 1)[-1]
            if m.isfile() and name in ("kernel", "root"):
                out[name] = t.extractfile(m).read()
    if "kernel" not in out or "root" not in out:
        sys.exit(f"{path}: no sysupgrade-*/kernel and root in the tar")
    return out["kernel"], out["root"]


def wifi_eeprom(chip, mac):
    """Minimal MediaTek Wi-Fi EEPROM (as tools/mknand.py --wifi-eeprom)."""
    e = bytearray(0x1000)
    e[0:2] = chip.to_bytes(2, "little")
    e[4:10] = mac
    e[0x190] = (0 << 6) | (4 << 3) | 4
    e[0x191] = (1 << 6) | (4 << 3) | 4
    e[0x192] = e[0x193] = 4 << 5
    e[0x441:0x445] = bytes([40] * 4)                # 20 dBm, 2.4 GHz
    e[0x445:0x445 + 4 * 5] = bytes([40] * 20)       # 20 dBm, 5 GHz groups
    return e


def put(f, off, data):
    f.seek(off)
    f.write(data)


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-o", "--output", required=True, help="image file")
    p.add_argument("--size-mb", type=int, default=1024,
                   help="whole image incl. boot partitions, a power of 2 "
                        "(default 1024)")
    p.add_argument("--boot-mb", type=int, default=4,
                   help="size of each boot partition (machine option "
                        "emmc-boot, default 4)")
    p.add_argument("--layout", default="gl-mt6000", choices=sorted(LAYOUTS),
                   help="GPT layout of the user area (default gl-mt6000)")
    p.add_argument("--bl2", required=True, help="eMMC preloader (EMMC_BOOT)")
    p.add_argument("--boot1", help="contents of boot partition 1 (dump)")
    p.add_argument("--fip", required=True, help="bl31-uboot.fip")
    p.add_argument("--firmware", help="OpenWrt squashfs-factory.bin "
                   "(kernel padded to 32 MB + rootfs)")
    p.add_argument("--sysupgrade", help="OpenWrt squashfs-sysupgrade.bin "
                   "(tar): kernel into \"kernel\", root into \"rootfs\"")
    p.add_argument("--fit", help="OpenWrt squashfs-sysupgrade.itb (FIT) -> "
                   "\"production\" (bpi-r4 layout)")
    p.add_argument("--recovery", help="initramfs-recovery.itb -> \"recovery\"")
    p.add_argument("--boot-env", action="store_true",
                   help="OpenWrt U-Boot environment that boots the FIT "
                        "in the \"kernel\" partition")
    p.add_argument("--factory", help="factory partition dump (else a minimal "
                   "Wi-Fi EEPROM with a random MAC)")
    p.add_argument("--wifi-chip", default="7986",
                   help="chip ID of the generated EEPROM (hex)")
    a = p.parse_args()
    PARTS = LAYOUTS[a.layout]

    size = a.size_mb * MB
    boot = a.boot_mb * MB
    if size & (size - 1):
        sys.exit("--size-mb must be a power of 2 (QEMU's eMMC needs that)")
    user = size - 2 * boot
    last = PARTS[-1][1] + PARTS[-1][2]
    if last + 1 * MB > user:
        sys.exit(f"image too small: the partitions need {last // MB + 1} MB")
    sgdisk = shutil.which("sgdisk") or shutil.which(
        "sgdisk", path="/usr/sbin:/sbin:/usr/local/sbin")
    if not sgdisk:
        sys.exit("sgdisk not found (apt install gdisk)")

    os.makedirs(os.path.dirname(os.path.abspath(a.output)), exist_ok=True)
    tmp = a.output + ".user"
    with open(tmp, "wb") as f:
        f.truncate(user)
    cmd = [sgdisk, "-o", "-a", "1"]
    for i, (name, off, sz) in enumerate(PARTS, 1):
        cmd += ["-n", f"{i}:{off // 512}:{(off + sz) // 512 - 1}",
                "-c", f"{i}:{name}"]
    subprocess.run(cmd + [tmp], check=True, stdout=subprocess.DEVNULL)

    part = {name: off for name, off, _ in PARTS}
    psize = {name: sz for name, _, sz in PARTS}
    with open(tmp, "r+b") as f:
        put(f, part["fip"], open(a.fip, "rb").read())
        if a.factory:
            put(f, part["factory"], open(a.factory, "rb").read()[:0x200000])
        else:
            mac = bytes([0x02] + [random.randrange(256) for _ in range(5)])
            eep = wifi_eeprom(int(a.wifi_chip, 16), mac)
            # Ethernet MAC base (mac-base cell at 0xa on the GL-MT6000)
            eep[0xa:0x10] = bytes([0x06]) + mac[1:]
            put(f, part["factory"], bytes(eep))
            print("factory: minimal Wi-Fi EEPROM, MAC %s"
                  % ":".join("%02x" % b for b in mac))
        if a.firmware:
            fw = open(a.firmware, "rb").read()
            room = PARTS[-1][1] + PARTS[-1][2] - part["kernel"]
            if len(fw) > room:
                sys.exit("firmware larger than kernel + rootfs")
            put(f, part["kernel"], fw)
        if a.sysupgrade:
            kernel, root = sysupgrade_parts(a.sysupgrade)
            for name, data in (("kernel", kernel), ("rootfs", root)):
                if len(data) > psize[name]:
                    sys.exit(f"sysupgrade {name} larger than the partition")
                put(f, part[name], data)
        for name, img in (("production", a.fit), ("recovery", a.recovery)):
            if img:
                if name not in part:
                    sys.exit(f"--{'fit' if name == 'production' else name}: "
                             f"the {a.layout} layout has no \"{name}\"")
                data = open(img, "rb").read()
                if len(data) > psize[name]:
                    sys.exit(f"{img} larger than the {name} partition")
                put(f, part[name], data)
        if a.boot_env:
            put(f, part["u-boot-env"], uboot_env(BOOT_ENV, 1))
            put(f, part["u-boot-env"] + ENV_SIZE, uboot_env(BOOT_ENV, 0))

    with open(a.output, "wb") as out:
        out.truncate(size)
        bl2 = open(a.bl2, "rb").read()
        if bl2[:9] != b"EMMC_BOOT":
            print("warning: the BL2 has no EMMC_BOOT header", file=sys.stderr)
        put(out, 0, bl2)
        if a.boot1:
            put(out, boot, open(a.boot1, "rb").read()[:boot])
        # copy the user area, keeping holes
        with open(tmp, "rb") as f:
            off = 0
            while off < user:
                chunk = f.read(4 * MB)
                if chunk.strip(b"\0"):
                    put(out, 2 * boot + off, chunk)
                off += len(chunk)
    os.unlink(tmp)
    print(f"wrote {a.output} ({a.size_mb} MB: boot0/boot1 {a.boot_mb} MB, "
          f"user area {user // MB} MB)")


if __name__ == "__main__":
    main()

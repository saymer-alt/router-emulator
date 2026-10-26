#!/usr/bin/env python3
"""OpenWrt sysupgrade.bin for a ramips board whose SPI-NOR was replaced by
a bigger one, built with the official ImageBuilder: the kernel's device
tree gets "firmware" up to the end of the flash (partitions after it are
dropped), the rest is the release as it is.

  tools/ib-flash-size.py (--ib DIR | --version V) --profile PROFILE
                         --mb 32|64 [-o OUTDIR]

DIR: unpacked openwrt-imagebuilder-<ver>-ramips-mt7621.Linux-x86_64
(downloads.openwrt.org); --version V: that ImageBuilder of release V,
downloaded (sha256 checked) and unpacked into --cache (default work/ib).
E.g. --version 25.12.5 --profile xiaomi_mi-router-4a-gigabit-v2.

The ImageBuilder takes the device kernels prebuilt (uImage: lzma-loader +
LZMA of the kernel with the DTB appended).  The DTB is replaced in the
decompressed kernel, which is compressed again with the ImageBuilder's own
lzma (the options of Build/lzma; checked: it reproduces the original
stream) and put back in place of the old stream - it must not be longer,
the loader is linked around it.  The model gets " (<N>M)".
"""
import argparse, glob, hashlib, lzma, os, re, shutil, struct, subprocess, sys, tempfile
import urllib.request, zlib

ap = argparse.ArgumentParser()
src = ap.add_mutually_exclusive_group(required=True)
src.add_argument("--ib")
src.add_argument("--version")
ap.add_argument("--cache", default=os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "work", "ib"))
ap.add_argument("--profile", required=True)
ap.add_argument("--mb", type=int, required=True)
ap.add_argument("-o", "--output", default=".")
a = ap.parse_args()

if a.version:
    name = f"openwrt-imagebuilder-{a.version}-ramips-mt7621.Linux-x86_64"
    url = f"https://downloads.openwrt.org/releases/{a.version}/targets/ramips/mt7621/"
    a.ib = os.path.join(a.cache, name)
    if not os.path.isdir(a.ib):
        os.makedirs(a.cache, exist_ok=True)
        sums = urllib.request.urlopen(url + "sha256sums").read().decode()
        want = re.search(r"^([0-9a-f]{64}) \*%s\.tar\.zst$" % re.escape(name), sums, re.M)
        if not want:
            sys.exit(f"no {name}.tar.zst in {url}sha256sums")
        tar = a.ib + ".tar.zst"
        if not os.path.exists(tar) or \
                hashlib.sha256(open(tar, "rb").read()).hexdigest() != want.group(1):
            print(f"downloading {name}.tar.zst")
            urllib.request.urlretrieve(url + name + ".tar.zst", tar)
        if hashlib.sha256(open(tar, "rb").read()).hexdigest() != want.group(1):
            sys.exit(f"{tar}: sha256 mismatch")
        subprocess.run(["tar", "--zstd", "-xf", tar, "-C", a.cache], check=True)

kdir = glob.glob(os.path.join(a.ib, "build_dir", "target-*", "linux-*",
                              a.profile + "-kernel.bin"))
if not kdir:
    sys.exit(f"no {a.profile}-kernel.bin in {a.ib}/build_dir")
kdir = os.path.dirname(kdir[0])
kernel = os.path.join(kdir, a.profile + "-kernel.bin")
dtbs = [f for f in os.listdir(kdir) if f.startswith("image-") and f.endswith(".dtb")
        and f.endswith("_" + a.profile.split("_", 1)[1] + ".dtb")]
if len(dtbs) != 1:
    sys.exit(f"DTB of {a.profile}: {dtbs or 'none'}")
LZ = os.path.join(a.ib, "staging_dir", "host", "bin", "lzma")
tmp = tempfile.mkdtemp(prefix="ibfs-")


def run(*cmd, **kw):
    return subprocess.run(cmd, check=True, **kw)


# device tree: firmware to the end of the flash
old_dtb = open(os.path.join(kdir, dtbs[0]), "rb").read()
dts = run("dtc", "-q", "-I", "dtb", "-O", "dts", os.path.join(kdir, dtbs[0]),
          capture_output=True, text=True).stdout
m = re.search(r"partition@([0-9a-f]+) \{[^}]*label = \"firmware\";[^}]*?"
              r"reg = <(0x[0-9a-f]+) (0x[0-9a-f]+)>;", dts)
if not m:
    sys.exit("no firmware partition in the device tree")
fw_start, fw_end = int(m.group(2), 16), int(m.group(2), 16) + int(m.group(3), 16)
size = a.mb << 20
dts = dts.replace(m.group(0), m.group(0).replace(
    m.group(3) + ">;", "0x%x>;" % (size - fw_start)), 1)
for p in re.finditer(r"\n\t*partition@([0-9a-f]+) \{[^}]*\};", dts):
    if int(p.group(1), 16) >= fw_end:       # e.g. Xiaomi's "Config"
        print(f"dropped partition@{p.group(1)}")
        dts = dts.replace(p.group(0), "", 1)
dts, n = re.subn(r'(model = "[^"]*)";', r'\1 (%dM)";' % a.mb, dts, count=1)
open(os.path.join(tmp, "new.dts"), "w").write(dts)
run("dtc", "-q", "-I", "dts", "-O", "dtb", "-o", os.path.join(tmp, "new.dtb"),
    os.path.join(tmp, "new.dts"))
new_dtb = open(os.path.join(tmp, "new.dtb"), "rb").read()
print(f"firmware 0x{fw_start:x}-0x{size:x}")

# kernel: replace the DTB in the LZMA payload of the loader
img = bytearray(open(kernel, "rb").read())
hdr, data = img[:64], img[64:64 + struct.unpack(">I", img[12:16])[0]]
if struct.unpack(">I", hdr[:4])[0] != 0x27051956:
    sys.exit("kernel is not a uImage")
k = None
for o in range(len(data) - 5):
    if data[o] < 225 and struct.unpack("<I", data[o + 1:o + 5])[0] in \
            (1 << 22, 1 << 23, 1 << 24, 1 << 25):
        try:
            d = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE)
            x = d.decompress(bytes(data[o:]))
        except lzma.LZMAError:
            continue
        if len(x) > 1 << 20:
            k, used = x, len(data) - o - len(d.unused_data)
            break
if k is None:
    sys.exit("no LZMA kernel in the loader")
at = k.rfind(old_dtb)
if at < 0:
    sys.exit("the DTB is not in the kernel")
open(os.path.join(tmp, "k.bin"), "wb").write(k[:at] + new_dtb + k[at + len(old_dtb):])
opts = ["-lc1", "-lp2", "-pb2"]
open(os.path.join(tmp, "o.bin"), "wb").write(k)
run(LZ, "e", os.path.join(tmp, "o.bin"), *opts, os.path.join(tmp, "o.lzma"),
    stdout=subprocess.DEVNULL)
if open(os.path.join(tmp, "o.lzma"), "rb").read() != bytes(data[o:o + used]):
    sys.exit("lzma does not reproduce the original stream (other options?)")
run(LZ, "e", os.path.join(tmp, "k.bin"), *opts, os.path.join(tmp, "k.lzma"),
    stdout=subprocess.DEVNULL)
z = open(os.path.join(tmp, "k.lzma"), "rb").read()
if len(z) > used:
    sys.exit(f"new LZMA stream {len(z) - used} bytes longer than the old one")
data[o:o + used] = z + b"\0" * (used - len(z))
hdr[24:28] = struct.pack(">I", zlib.crc32(data))
hdr[4:8] = bytes(4)
hdr[4:8] = struct.pack(">I", zlib.crc32(bytes(hdr)))

# image: the ImageBuilder with this kernel, the original put back after
saved = os.path.join(tmp, "kernel.orig")
shutil.copy(kernel, saved)
try:
    open(kernel, "wb").write(bytes(hdr) + bytes(data) + bytes(img[64 + len(data):]))
    bindir = os.path.join(tmp, "bin")
    run("make", "-C", a.ib, "image", "PROFILE=" + a.profile, "BIN_DIR=" + bindir,
        stdout=open(os.path.join(tmp, "ib.log"), "w"), stderr=subprocess.STDOUT)
finally:
    shutil.copy(saved, kernel)
sys_ = [f for f in os.listdir(bindir) if f.endswith("-squashfs-sysupgrade.bin")]
if len(sys_) != 1:
    sys.exit(f"no sysupgrade.bin (log: {tmp}/ib.log)")
out = os.path.join(a.output, sys_[0].replace(a.profile, f"{a.profile}-{a.mb}m"))
shutil.copy(os.path.join(bindir, sys_[0]), out)
shutil.rmtree(tmp)
print(f"wrote {out}")

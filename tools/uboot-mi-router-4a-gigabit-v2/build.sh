#!/bin/bash
# Bootloader of the Xiaomi Mi Router 4A Gigabit Edition v2 with a bigger
# SPI-NOR (32 / 64 MB): neither the stock U-Boot 2018.09 nor Breed 1.1
# knows the W25Q512JV (ef4020).  This builds OpenWrt's U-Boot (src/u-boot +
# OpenWrt's uboot-mediatek patches) with
# mt7621_xiaomi_mi-router-4a-gigabit-v2_defconfig: the config of OpenWrt's
# zbtlink_zbt-wg3526-16m (MT7621 SPL + MediaTek's DDR init blob) with DDR3
# 128 MB, Xiaomi's flash layout ("firmware" from 0x180000 to the end of the
# flash, cut to the chip's size), the reset button on GPIO 18 and SPI-NOR
# >16 MB through the bank register: the chip stays in 3-byte mode, which
# the boot ROM reads after a reboot.
#
#   tools/uboot-mi-router-4a-gigabit-v2/build.sh [OUT.bin]
#
#   default OUT: work/u-boot-mt7621-mi-router-4a-gigabit-v2.bin (not in the
#   repository and not built by CI: owners of such a router have theirs)
#   CROSS_COMPILE (default mipsel-linux-gnu-)
set -e
cd "$(dirname "$(readlink -f "$0")")/../.."
ROOT=$PWD
HERE=$ROOT/tools/uboot-mi-router-4a-gigabit-v2
OUT=$(readlink -f "${1:-$ROOT/work/u-boot-mt7621-mi-router-4a-gigabit-v2.bin}")
export CROSS_COMPILE=${CROSS_COMPILE:-mipsel-linux-gnu-}
MK=src/openwrt/package/boot/uboot-mediatek/Makefile
VER=$(sed -n 's/^PKG_VERSION:=//p' $MK)
SRC=$ROOT/work/uboot-mi-router-4a-gigabit-v2
BLOB=$ROOT/work/mt7621_stage_sram_noprint.bin

if [ ! -d "$SRC" ]; then
    git -C src/u-boot worktree add --detach "$SRC" "v$VER"
    for p in src/openwrt/package/boot/uboot-mediatek/patches/*.patch; do
        patch -d "$SRC" -p1 -s --no-backup-if-mismatch < "$p"
    done
fi
# MediaTek's DDR init (the one OpenWrt's release builds use, without output)
URL=$(sed -n 's/^MT7621_LOWLEVEL_PRELOADER_URL:=//p' $MK)
SUM=$(sed -n '/Download\/mt7621-stage-sram-noprint/,/endef/s/.*HASH:=//p' $MK)
[ -s "$BLOB" ] || wget -q -O "$BLOB" "${URL}mt7621_stage_sram_noprint.bin"
echo "$SUM  $BLOB" | sha256sum -c --quiet
cp "$BLOB" "$SRC/mt7621_stage_sram.bin"
cp "$HERE/xiaomi,mi-router-4a-gigabit-v2.dts" "$SRC/arch/mips/dts/"
cp "$HERE/mt7621_xiaomi_mi-router-4a-gigabit-v2_defconfig" "$SRC/configs/"
mkdir -p "$SRC/defenvs"
cp "$HERE/xiaomi_mi-router-4a-gigabit-v2_env" "$SRC/defenvs/"
make -C "$SRC" -s mt7621_xiaomi_mi-router-4a-gigabit-v2_defconfig
make -C "$SRC" -s -j"$(nproc)"
[ "$(stat -c %s "$SRC/u-boot-mt7621.bin")" -le $((0x30000)) ] ||
    { echo "u-boot-mt7621.bin is bigger than the u-boot partition" >&2; exit 1; }
cp "$SRC/u-boot-mt7621.bin" "$OUT"
echo "wrote $OUT ($(stat -c %s "$OUT") bytes)"

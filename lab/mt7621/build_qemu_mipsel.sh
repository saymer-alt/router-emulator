#!/usr/bin/env bash
# Build only the mipsel system target (MT7621 boards) of the patched QEMU.
# Same pinned source and patch series as lab/build_qemu.sh; the aarch64
# build is not needed for the MT7621 stand and is skipped to save time.
set -euo pipefail
repo=$(cd "$(dirname "$0")/../.." && pwd)
target=${1:?new native build directory required}
mkdir -p "$target"
target=$(cd "$target" && pwd)
git clone --depth 1 --branch v10.1.0 https://gitlab.com/qemu-project/qemu.git "$target/source"
cd "$target/source"
test "$(git rev-parse HEAD)" = f8b2f64e2336a28bf0d50b6ef8a7d8c013e9bcf3
git -c user.name=lab-build -c user.email=lab-build@localhost am "$repo"/qemu-patches/*.patch
mkdir build
cd build
../configure --target-list=mipsel-softmmu --enable-slirp --enable-fdt=enabled \
  --disable-docs --disable-werror --disable-guest-agent --disable-tools
ninja -j "${JOBS:-2}" qemu-system-mipsel
cp qemu-system-mipsel "$target/qemu-system-mipsel"
sha256sum "$target/qemu-system-mipsel" > "$target/qemu-mipsel.sha256"
echo "built: $target/qemu-system-mipsel"
echo "run it with: QEMU_BIN=$target/qemu-system-mipsel ./emulator.sh -P <mt7621-preset> ..."

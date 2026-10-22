#!/usr/bin/env bash
# Build only the changed AArch64 system target; no Windows, MIPS or PGO build.
set -euo pipefail
repo=$(cd "$(dirname "$0")/.." && pwd)
target=${1:?new native build directory required}
mkdir -p "$target"
target=$(cd "$target" && pwd)
git clone --depth 1 --branch v10.1.0 https://gitlab.com/qemu-project/qemu.git "$target/source"
cd "$target/source"
test "$(git rev-parse HEAD)" = f8b2f64e2336a28bf0d50b6ef8a7d8c013e9bcf3
git -c user.name=lab-build -c user.email=lab-build@localhost am "$repo"/qemu-patches/*.patch
mkdir build
cd build
../configure --target-list=aarch64-softmmu --enable-slirp --enable-fdt=enabled \
  --disable-docs --disable-werror --disable-guest-agent --disable-tools
ninja -j "${JOBS:-2}" qemu-system-aarch64
cp qemu-system-aarch64 "$target/qemu-system-aarch64"
sha256sum "$target/qemu-system-aarch64" > "$target/qemu.sha256"

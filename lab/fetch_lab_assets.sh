#!/usr/bin/env bash
# Fixed public evaluation inputs. Keep all outputs in the private runner folder.
set -euo pipefail
base_dir=${1:?private output directory required}
repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
mkdir -m 700 -p "$base_dir"
base_dir=$(cd "$base_dir" && pwd)
umask 077
mkdir "$base_dir/downloads"
cd "$base_dir/downloads"
release=https://github.com/stavultras/router-emulator/releases/download/v4.1
curl -fL --retry 3 -o emulator.tar.gz "$release/Router-Emulator-4.1-linux-x86_64.tar.gz"
curl -fL --retry 3 -o presets.zip "$release/Router-Emulator-4.1-presets.zip"
curl -fL --retry 3 --max-time 180 -o ported.bin 'https://osvault.project-athens.com/osvault/Cudy%20WR3000P/Cudy_WR3000P_5.1.8.bin'
curl -fL --retry 3 --max-time 90 -o keenboot.bin 'https://osvault.project-athens.com/files/keenboot/aarch64/KB-41/KeenBOOT_WR3000P_KB-41.bin'
sha256sum -c <<'SUMS'
8ae277cc0fecfb99da3a452fb4226f272ee6d571f362898ca2176513f3adb0b7  emulator.tar.gz
8146b505a9d75407c4504bf90638d7e3d3e3ccfc529d09c45a40b2b6868604a2  presets.zip
710e4a37b1aa9f3a65a5122edd0650f5c5ad3e6eca5df53b11ac28aab2664eb7  ported.bin
8b8615b345d912187853fea6bef6b1868760fd52ed92ba7c3a380ad5533cbf08  keenboot.bin
SUMS
tar xzf emulator.tar.gz
unzip -q presets.zip
cd Router-Emulator
tools/prepare-nand.sh cudy_wr3000p-v1 25.12.5

# This pinned v4.1 release is only an asset/flash seed. The QEMU binary from
# the active patch series is installed by the caller, so use the wrapper and
# preset from the same checkout to keep them compatible with that QEMU.
install -m 755 "$repo/emulator.sh" "$base_dir/downloads/Router-Emulator/emulator.sh"
install -m 644 "$repo/presets/cudy-wr3000p-v1.ini" \
  "$base_dir/downloads/Router-Emulator/presets/cudy-wr3000p-v1.ini"
install -m 644 "$repo/VERSION" "$base_dir/downloads/Router-Emulator/VERSION"

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
release=https://github.com/stavultras/router-emulator/releases/download/v3.8
curl -fL --retry 3 -o emulator.tar.gz "$release/Router-Emulator-3.8-linux-x86_64.tar.gz"
curl -fL --retry 3 -o presets.zip "$release/Router-Emulator-3.8-presets.zip"
curl -fL --retry 3 --max-time 180 -o ported.bin 'https://osvault.project-athens.com/osvault/Cudy%20WR3000P/Cudy_WR3000P_5.1.8.bin'
curl -fL --retry 3 --max-time 90 -o keenboot.bin 'https://osvault.project-athens.com/files/keenboot/aarch64/KB-41/KeenBOOT_WR3000P_KB-41.bin'
sha256sum -c <<'SUMS'
77fa6f8a6a00af1164c9e144dc2f8bda7154877271498666bb9f17355d2e74d6  emulator.tar.gz
ef98b33c7636b5e792dcb16bcd1ad54f247639fccc19e9d9662fd422954d0732  presets.zip
710e4a37b1aa9f3a65a5122edd0650f5c5ad3e6eca5df53b11ac28aab2664eb7  ported.bin
8b8615b345d912187853fea6bef6b1868760fd52ed92ba7c3a380ad5533cbf08  keenboot.bin
SUMS
tar xzf emulator.tar.gz
unzip -q presets.zip
cd Router-Emulator
tools/prepare-nand.sh cudy_wr3000p-v1 25.12.5

# This pinned v3.8 release is only an asset/flash seed. The QEMU binary from
# the active patch series is installed by the caller, so the wrapper and the
# preset must also come from this same checkout (v4.1 uses dump-dir and
# flash-type/flash-id; v3.8's nand-dir silently breaks the QEMU -M launch).
install -m 755 "$repo/emulator.sh" "$base_dir/downloads/Router-Emulator/emulator.sh"
install -m 644 "$repo/presets/cudy-wr3000p-v1.ini" \
  "$base_dir/downloads/Router-Emulator/presets/cudy-wr3000p-v1.ini"
install -m 644 "$repo/VERSION" "$base_dir/downloads/Router-Emulator/VERSION"

#!/bin/bash
# Build the Router Emulator: QEMU v10.1.0 + qemu-patches/*.patch
#
#   ./build.sh            clone QEMU into src/qemu, apply the patches, build
#                         src/qemu/build/qemu-system-aarch64 (Filogic boards)
#                         and qemu-system-mipsel (MT7621 boards)
#
# Environment:
#   JOBS=N                parallel build jobs (default: ninja's choice)
#   CONFIGURE_ARGS="..."  extra arguments for QEMU's configure
#   PGO_NAND=DIR          profile-guided optimisation (GCC): an instrumented
#                         qemu-system-aarch64 (src/qemu/build-pgo-gen) boots
#                         the WR3000P flash folder DIR (e.g. nand-wr3000p from
#                         tools/prepare-nand.sh cudy_wr3000p-v1 ...) through
#                         tools/pgo-train.py, then src/qemu/build is rebuilt
#                         with that profile; later runs without PGO_NAND keep
#                         it (rm -rf src/qemu/build drops it).  Not PGO=1:
#                         build-windows.sh uses that for its own PGO step
#
# libslirp and libfdt are taken from the system when their development
# files are installed; otherwise QEMU's own meson subprojects (libslirp,
# dtc) are downloaded and built, so the build works without root as long
# as a compiler, meson, ninja, glib, pixman and zlib are present.
set -e
cd "$(dirname "$(readlink -f "$0")")"
ROOT=$PWD

# --- required tools and libraries -------------------------------------
missing=()
for t in git cc make ninja meson python3 pkg-config; do
    command -v "$t" >/dev/null 2>&1 || missing+=("$t")
done
if command -v pkg-config >/dev/null 2>&1; then
    for p in glib-2.0 pixman-1 zlib; do
        pkg-config --exists "$p" || missing+=("$p (development files)")
    done
fi
if [ ${#missing[@]} -gt 0 ]; then
    echo "missing: ${missing[*]}" >&2
    echo "see README.build.linux.md for the packages of your distribution" >&2
    exit 1
fi
for p in slirp; do
    pkg-config --exists "$p" ||
        echo "note: no system lib$p, QEMU's subproject will be downloaded" >&2
done

if [ ! -d src/qemu/.git ]; then
    mkdir -p src
    git clone --depth 1 --branch v10.1.0 https://gitlab.com/qemu-project/qemu.git src/qemu
fi
# the flash folder relative to the directory build.sh was started in
[ -n "$PGO_NAND" ] && PGO_NAND=$(cd "$OLDPWD" && readlink -f "$PGO_NAND")
cd src/qemu
if ! git rev-parse -q --verify mt7981 >/dev/null; then
    git checkout -b mt7981
fi
# apply the patches not yet on the branch: a checkout made before the
# repository got new patches receives them (the branch is expected to hold
# the series in order; after own commits in src/qemu, apply by hand)
patches=("$ROOT"/qemu-patches/*.patch)
have=$(git rev-list --count v10.1.0..mt7981)
# a reused checkout may hold an older series: verify the applied prefix by
# subject (a commit count alone does not identify the series) before resuming
series_n=$have
[ "$series_n" -gt ${#patches[@]} ] && series_n=${#patches[@]}
"$ROOT"/tools/check-patch-series.sh "$ROOT"/qemu-patches "$series_n"
if [ "$have" -lt ${#patches[@]} ]; then
    git checkout -q mt7981
    git -c user.name=build -c user.email=build@localhost am "${patches[@]:$have}"
fi
# --enable-fdt=enabled: system libfdt if found, else the bundled dtc
CONF=(--enable-slirp --enable-fdt=enabled --disable-docs --disable-werror
      $CONFIGURE_ARGS)
if [ -n "$PGO_NAND" ]; then
    cc -v 2>&1 | grep -q '^gcc version' || { echo "PGO_NAND: PGO needs GCC as cc" >&2; exit 1; }
    NAND=$PGO_NAND
    [ -f "$NAND/mt7981.mtd0.BL2.bin" ] ||
        { echo "PGO_NAND: no flash folder $NAND (tools/prepare-nand.sh cudy_wr3000p-v1 25.12.5)" >&2; exit 1; }
    # gcda files are named after the object paths relative to the build
    # directory (-fprofile-prefix-path), so the profile of build-pgo-gen
    # fits build; atomic counters: QEMU runs the guest CPUs in threads
    rm -rf pgo-prof pgo-nand build-pgo-gen && mkdir build-pgo-gen && cd build-pgo-gen
    ../configure --target-list=aarch64-softmmu "${CONF[@]}" \
        --extra-cflags="-fprofile-generate=$PWD/../pgo-prof -fprofile-prefix-path=$PWD -fprofile-update=prefer-atomic" \
        --extra-ldflags="-fprofile-generate=$PWD/../pgo-prof" >configure.out
    ninja ${JOBS:+-j "$JOBS"} qemu-system-aarch64
    cd .. && cp -r "$NAND" pgo-nand
    echo "PGO: training run"
    timeout 1800 python3 "$ROOT/tools/pgo-train.py" build-pgo-gen/qemu-system-aarch64 pgo-nand
    rm -rf pgo-nand build-pgo-gen
    # a fresh build directory (configure keeps old options in an existing
    # one); qemu-system-mipsel code without a profile is optimised as usual
    rm -rf build && mkdir build && cd build
    ../configure --target-list=aarch64-softmmu,mipsel-softmmu "${CONF[@]}" \
        --extra-cflags="-fprofile-use=$PWD/../pgo-prof -fprofile-prefix-path=$PWD -fprofile-partial-training -Wno-missing-profile -Wno-coverage-mismatch"
else
    mkdir -p build && cd build
    # a build directory from before the MT7621 machine is configured again
    grep -qs mipsel-softmmu build.ninja ||
        ../configure --target-list=aarch64-softmmu,mipsel-softmmu "${CONF[@]}"
fi
ninja ${JOBS:+-j "$JOBS"} qemu-system-aarch64 qemu-system-mipsel
echo "built: $PWD/qemu-system-aarch64 $PWD/qemu-system-mipsel"

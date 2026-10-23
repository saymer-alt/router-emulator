#!/usr/bin/env bash
# Verify that the commits applied on the current branch since v10.1.0
# correspond to the first N patch files of DIR — by subject, in order.
# A commit count alone does not prove the same series: a src/qemu checkout
# reused across a renumbered or extended series would otherwise silently
# skip patches (have >= file count) or mis-pair the resumed slice.
# Subject lines must be single-line, as in every qemu-patches/*.patch.
#
# Usage: check-patch-series.sh DIR N   (run inside the QEMU checkout)
set -euo pipefail
dir=$1 n=$2
[ "$n" -gt 0 ] || exit 0
want=$(for p in "$dir"/*.patch; do
    [ "$n" -gt 0 ] || break
    n=$((n - 1))
    sed -n 's/^Subject: \[PATCH[^]]*\] //p' "$p"
done)
got=$(git log --reverse --format=%s v10.1.0..HEAD | head -n "${2:-0}")
if [ "$want" != "$got" ]; then
    echo "ERROR: this checkout holds a different patch series than $dir" >&2
    echo "       (stale src/qemu from an older qemu-patches/ layout?)" >&2
    echo "       Remove src/qemu (or realign the mt7981 branch) and re-run the build." >&2
    exit 1
fi

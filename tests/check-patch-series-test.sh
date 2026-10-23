#!/usr/bin/env bash
# Regression test for tools/check-patch-series.sh, mirroring build.sh's call
# semantics: N = min(applied commits, patch files). A matching prefix must
# pass (including upstream's documented own-commits-after-the-series case);
# a same-count stale series and a mid-series foreign commit must fail loudly.
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
tool="$root/tools/check-patch-series.sh"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

make_patch() {  # name file
    printf 'From 0000000000000000000000000000000000000000 Mon Sep 17 00:00:00 2001\nSubject: [PATCH] %s\n\nbody\n---\n a | 1 +\n 1 file changed, 1 insertion(+)\n' "$1" > "$tmp/patches/$2"
}
mkcommit() { git -c user.name=t -c user.email=t@t commit -q --allow-empty -m "$1"; }

mkdir -p "$tmp/patches" && cd "$tmp" && git init -q repo && cd repo
git -c user.name=t -c user.email=t@t commit -q --allow-empty -m base && git tag v10.1.0
make_patch "alpha one" 0010-alpha.patch
make_patch "beta two" 0011-beta.patch
make_patch "gamma three" 0012-gamma.patch

# 1) applied series matches the files: pass
mkcommit "alpha one"
mkcommit "beta two"
"$tool" "$tmp/patches" 2
echo "case 1 (matching series) passed"

# 2) own commit appended after the full series: pass (upstream's documented case;
#    build.sh resumes nothing and passes N = min(have, count))
mkcommit "local experiment"
"$tool" "$tmp/patches" 2
echo "case 2 (own commit after full series) passed"

# 3) foreign commit inside a partially applied series (stale checkout): must fail
git checkout -q -b stale-mid v10.1.0
mkcommit "alpha one"
mkcommit "intruder"
if "$tool" "$tmp/patches" 2 2>/dev/null; then
    echo "FAIL: mid-series foreign commit not detected"; exit 1
fi
echo "case 3 (mid-series foreign commit) failed as required"

# 4) same count, different subjects (renumbered files vs stale checkout): must fail
git checkout -q -b stale-rename v10.1.0
mkcommit "old alpha"; mkcommit "old beta"
if "$tool" "$tmp/patches" 2 2>/dev/null; then
    echo "FAIL: same-count stale series not detected"; exit 1
fi
echo "case 4 (same-count stale series) failed as required"

echo "PASS: check-patch-series behaves correctly"

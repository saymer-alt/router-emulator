#!/bin/bash
# Guest CPU speed: boot a preset, run a shell loop (300000 iterations) and
# md5sum of 64 MB five times each, print the times (min = least host noise).
#   tests/cpubench.sh [QEMU_BINARY] [PRESET]      (*.exe: Windows build under Wine)
cd "$(dirname "$(readlink -f "$0")")/.."
export QEMU_BIN=${1:-$PWD/src/qemu/build/qemu-system-aarch64}
P=${2:-cudy-wr3000p-v1}
D=work/bench-nand; rm -rf $D
N=$(awk -F= '$1=="dump-dir"{print $2}' presets/$P.ini)
cp -r "work/winpkg/Router-Emulator/$N" $D 2>/dev/null || cp -r "$N" $D
WIN=
case "$QEMU_BIN" in *.exe) WIN=--win; export QEXE=$QEMU_BIN WINEPREFIX=${WINEPREFIX:-$PWD/work/wineprefix} ;; esac
timeout 330 python3 tests/quick.py $WIN --limit 320 --log work/bench.log -P $P -n $D \
  'Please press Enter@150' '!40' '>' 'root@@20' \
  '>for k in 1 2 3 4 5; do time sh -c "i=0; while [ \$i -lt 300000 ]; do i=\$((i+1)); done"; done 2>&1 | grep real; for k in 1 2 3 4 5; do time sh -c "dd if=/dev/zero bs=1M count=64 2>/dev/null | md5sum >/dev/null"; done 2>&1 | grep real; echo ==E$((2+3))' \
  '==E5@240' > /dev/null
tr -d '\r' < work/bench.log | grep -a '^real' | python3 -c '
import re, sys
t = []
for l in sys.stdin:
    m = re.search(r"(?:(\d+)m\s*)?([\d.]+)s", l)
    t.append(int(m.group(1) or 0) * 60 + float(m.group(2)))
for name, v in (("loop", t[:5]), ("md5", t[5:10])):
    print("%-5s %s  min %.2f s" % (name + ":", " ".join("%.2f" % x for x in v), min(v) if v else 0))
'

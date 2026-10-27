#!/usr/bin/env python3
"""PGO training run: boot a board from a NAND folder, run some shell /
ubus / uci / md5sum work, quit through QMP so the instrumented QEMU writes
its profile.

  pgo-train.py QEMU_EXE NAND_DIR [PROFRAW_PATTERN]

QEMU_EXE ending in .exe (the Windows build, clang) runs under Wine and
writes PROFRAW_PATTERN; otherwise QEMU runs natively (the Linux build, gcc
-fprofile-generate writes its .gcda files where it was built to)."""
import os, socket, subprocess, sys, time

exe, nand = sys.argv[1:3]
if exe.endswith(".exe"):
    host = lambda p: "Z:" + os.path.abspath(p).replace("/", "\\")
    env = dict(os.environ, WINEDEBUG="-all", LLVM_PROFILE_FILE=host(sys.argv[3]))
    cmd = ["wine", exe]
else:
    host = os.path.abspath
    env = dict(os.environ)
    if len(sys.argv) > 3:
        env["LLVM_PROFILE_FILE"] = sys.argv[3]
    cmd = [exe]
q = subprocess.Popen(cmd + ["-M",
    "mt7981-router,dump-dir=" + host(nand) +
    ",gmac0=mt7531,ports=lan4:lan3:lan2:lan1:-,gmac1=rtl8221b,ddr=ddr4,usb-port=2",
    "-m", "512M", "-display", "none",
    "-serial", "tcp:127.0.0.1:45881,server=on,wait=on",
    "-qmp", "tcp:127.0.0.1:45882,server=on,wait=off", "-netdev", "user,id=wan"],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
for _ in range(100):
    try:
        c = socket.create_connection(("127.0.0.1", 45881)); break
    except OSError:
        time.sleep(0.3)
c.settimeout(1)
buf = b""


def until(pat, limit):
    global buf
    t = time.time()
    while time.time() - t < limit:
        try:
            buf += c.recv(65536)
        except socket.timeout:
            pass
        if pat in buf:
            buf = b""
            return True
    return False


ok = until(b"Please press Enter", 1200)
print("boot:", ok, flush=True)
time.sleep(60)                      # let boot scripts run (also profiled)
c.send(b"\r"); until(b"root@", 60)
c.send(b'i=0; while [ $i -lt 30000 ]; do i=$((i+1)); done; '
       b'dd if=/dev/zero bs=1M count=8 2>/dev/null | md5sum; '
       b'i=0; while [ $i -lt 100 ]; do ubus call system board >/dev/null; '
       b'uci show network >/dev/null; i=$((i+1)); done; '
       b'head -c 8388608 /dev/mtd5 | md5sum; echo DO""NE\r')
print("work:", until(b"DONE", 1200), flush=True)
s = socket.create_connection(("127.0.0.1", 45882)); f = s.makefile("rw"); f.readline()
f.write('{"execute":"qmp_capabilities"}\n'); f.flush(); f.readline()
f.write('{"execute":"quit"}\n'); f.flush()
rc = q.wait(300)
print("exit:", rc)
sys.exit(0 if ok and rc == 0 else 1)

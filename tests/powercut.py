#!/usr/bin/env python3
"""Power-cut stress test: kill (QMP quit) or reset QEMU at random moments,
then check that BL2 still loads FIP (BL31 starts).
  powercut.py NANDDIR [MACHINE-OPTS] [ROUNDS]
MACHINE-OPTS: -M options after dump-dir, default WR3000H-like hardware."""
import json, os, random, socket, subprocess, sys, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
nand = os.path.abspath(sys.argv[1]); opts = sys.argv[2] if len(sys.argv) > 2 else "gmac0=mt7531,ports=lan4:lan3:lan2:lan1:-,gmac1=rtl8221b,ddr=ddr3"
rounds = int(sys.argv[3]) if len(sys.argv) > 3 else 6

def start():
    port = random.randint(20000, 40000)
    q = subprocess.Popen([ROOT + "/src/qemu/build/qemu-system-aarch64", "-M", f"mt7981-router,dump-dir={nand},{opts}",
        "-display", "none", "-serial", f"tcp:127.0.0.1:{port},server=on,wait=off",
        "-qmp", f"tcp:127.0.0.1:{port+1},server=on,wait=off", "-nic", "none"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        try:
            c = socket.create_connection(("127.0.0.1", port)); c.settimeout(0.2); return q, c, port + 1
        except OSError: time.sleep(0.1)
    raise SystemExit("no console")

def qmp(port, cmd):
    s = socket.create_connection(("127.0.0.1", port)); f = s.makefile("rw")
    f.readline(); f.write('{"execute":"qmp_capabilities"}\n'); f.flush(); f.readline()
    f.write(json.dumps({"execute": cmd}) + "\n"); f.flush()
    try: f.readline()
    except Exception: pass
    s.close()

def read_until(c, pats, limit):
    buf = b""; t = time.time()
    while time.time() - t < limit:
        try:
            d = c.recv(65536)
            if not d: break
            buf += d
        except socket.timeout: pass
        for p in pats:
            if p in buf: return p, buf
    return None, buf

bad = 0
q, c, qp = start()
for r in range(rounds):
    p, buf = read_until(c, [b"NOTICE:  BL31: v", b"ERROR:", b"PANIC"], 40)
    ok = p == b"NOTICE:  BL31: v"
    if not ok:
        bad += 1; open(f"{ROOT}/work/powercut-fail-{r}.log", "wb").write(buf)
    t = random.uniform(2, 45)
    p2, _ = read_until(c, [], t)
    mode = random.choice(["quit", "reset"])
    print(f"round {r}: fip {'ok' if ok else 'FAIL ' + str(p)}; then {mode} after {t:.1f}s", flush=True)
    if mode == "quit":
        qmp(qp, "quit"); q.wait(10); c.close(); q, c, qp = start()
    else:
        qmp(qp, "system_reset")
qmp(qp, "quit"); q.wait(10)
print("FAILS:", bad); sys.exit(1 if bad else 0)

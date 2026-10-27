#!/usr/bin/env python3
"""Fast emulator check: start QEMU with the console on a socket, wait for
console strings in order, optionally send input, always clean up.

  quick.py [--win] [--limit SEC] [-P PRESET] [-n NANDDIR] [-M machine,opts]
           [--qemu "ARGS"] STEP...

-P takes the board hardware from presets/PRESET.ini (default
cudy-wr3000p-v1) and -n the dump folder (default: the preset's dump-dir);
-M replaces both with a complete machine option string; -o k=v,... adds or
overrides machine options of the preset.

STEP is "PATTERN" (regex to wait for, 120 s), "PATTERN@SEC" (own timeout),
">TEXT" (send TEXT + CR) or "!SEC" (sleep).  Exit code 0 = all matched.
"""
import argparse, os, re, shlex, socket, subprocess, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ap = argparse.ArgumentParser()
ap.add_argument("--win", action="store_true", help="Windows build under Wine")
ap.add_argument("--limit", type=int, default=300, help="hard limit, seconds")
ap.add_argument("-P", default="cudy-wr3000p-v1", help="board preset")
ap.add_argument("-n", help="dump folder (default: the preset's dump-dir)")
ap.add_argument("-M", help="complete -M string (overrides -P/-n)")
ap.add_argument("-o", default="", help="machine options on top of the preset, k=v,k=v")
ap.add_argument("--log", default=ROOT + "/work/quick.log")
ap.add_argument("--qemu", default="", help="extra QEMU args (one string)")
ap.add_argument("steps", nargs="*")
a = ap.parse_args()

def preset_args(name, nand):
    """-M / -m arguments for a presets/*.ini board"""
    path = name if os.path.isfile(name) else os.path.join(ROOT, "presets", name + ".ini")
    opts, ram, ndir, soc = [], "512", "nand", "mt7981"
    for line in open(path):
        line = line.strip()
        if not line or line[0] in ";#[" or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k in ("name", "description") or k.startswith(("openwrt", "lan-")):
            continue
        if k == "ram":
            ram = v
        elif k == "soc":
            soc = v
        elif k == "dump-dir":
            ndir = v
        else:
            opts.append(f"{k}={v}")
    for kv in filter(None, a.o.split(",")):
        k = kv.split("=", 1)[0]
        opts = [o for o in opts if o.split("=", 1)[0] != k] + [kv]
    nand = nand or os.path.join(ROOT, ndir)
    if a.win:
        nand = "Z:" + os.path.abspath(nand).replace("/", "\\")
    return ["-M", ",".join([soc + "-router", "dump-dir=" + nand.replace(",", ",,")] + opts),
            "-m", ram + "M"]

port = 46000 + os.getpid() % 1000
margs = ["-M", a.M] if a.M else preset_args(a.P, a.n)
# MT7621 boards (MIPS) run on the second QEMU binary
qname = "qemu-system-mipsel" if margs[1].startswith("mt7621-") else "qemu-system-aarch64"
if a.win:
    cmd = ["wine", os.environ.get("QEXE", ROOT + "/work/winpkg/Router-Emulator/qemu/" + qname + ".exe")]
else:
    own = ROOT + "/src/qemu/build/" + qname
    if not os.path.exists(own):                 # Linux package layout
        own = ROOT + "/qemu/" + qname
    cmd = [os.environ.get("QEMU_BIN", own)]
cmd += margs + ["-display", "none",
        "-chardev", f"socket,id=con,mux=on,host=127.0.0.1,port={port},server=on,wait=on",
        "-serial", "chardev:con", "-mon", "chardev=con"] + shlex.split(a.qemu)
err = open(a.log + ".err", "w")
q = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=err)
t0 = time.time()
deadline = t0 + a.limit
log = open(a.log, "wb")
rc = 1
try:
    s = None
    while time.time() < deadline and q.poll() is None:
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=1)
            break
        except OSError:
            time.sleep(0.2)
    if not s:
        raise SystemExit("QEMU did not start (see %s.err)" % a.log)
    s.settimeout(0.5)
    buf = b""
    pos = 0

    def pump():
        global buf
        try:
            d = s.recv(65536)
            if not d:
                return False
            buf += d
            log.write(d)
            log.flush()
        except socket.timeout:
            pass
        return True

    for st in a.steps:
        if st.startswith(">"):
            s.sendall(st[1:].encode() + b"\r")
            continue
        if st.startswith("!"):
            end = time.time() + float(st[1:])
            while time.time() < end and pump():
                pass
            continue
        pat, _, to = st.rpartition("@") if re.search(r"@\d+$", st) else (st, "", "120")
        end = min(time.time() + float(to), deadline)
        rx = re.compile(pat.encode())
        m = None
        while time.time() < end and q.poll() is None:
            m = rx.search(buf, pos)
            if m:
                break
            if not pump():
                break
        if not m:
            m = rx.search(buf, pos)
        if not m:
            print(f"{time.time() - t0:6.1f}s FAIL  {pat}")
            tail = re.sub(rb"\x1b\[[0-9;?]*[A-Za-z]", b"", buf[-600:])
            print(tail.decode("latin1"))
            break
        pos = m.end()
        line = buf[buf.rfind(b"\n", 0, m.start()) + 1:m.end()]
        print(f"{time.time() - t0:6.1f}s ok    {pat}   | {line.decode('latin1').strip()[:100]}")
    else:
        rc = 0
finally:
    q.kill()
    if a.win:
        subprocess.run(["wineserver", "-k"], stderr=subprocess.DEVNULL)
sys.exit(rc)

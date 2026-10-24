#!/usr/bin/env python3
"""Headless, isolated and reproducible router-emulator scenario runner.

Stdlib only. Evidence levels are reported as provided by the scenario, never
upgraded on a successful run. Host WAN is restricted; LAN is localhost only.
All guest firmware/flash input stays on the local runner (never auto-uploaded).
"""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET


ALLOWED_ACTIONS = {"wait_serial", "send_serial", "probe_http", "reset", "sleep"}
ALLOWED_EVIDENCE = {
    "OPENWRT_QEMU_SMOKE",
    "KEENETIC_PORTED_EXPERIMENTAL",
    "KEENETICOS_VIRTUAL",
}


def load_scenario(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("steps"), list) or not data["steps"]:
        raise ValueError("scenario must have a non-empty steps list")
    if data.get("evidence") not in ALLOWED_EVIDENCE:
        raise ValueError("scenario evidence must be explicitly classified")
    for index, step in enumerate(data["steps"], 1):
        if not isinstance(step, dict) or step.get("action") not in ALLOWED_ACTIONS:
            raise ValueError("step %d: unsupported action" % index)
        action = step["action"]
        if action == "wait_serial":
            re.compile(step["pattern"].encode())
            if "reject_pattern" in step:
                re.compile(step["reject_pattern"].encode())
        if action == "probe_http":
            url = urlsplit(step["url"])
            if url.scheme != "http" or url.hostname != "127.0.0.1" or url.username or url.password:
                raise ValueError("step %d: HTTP probe must target 127.0.0.1 only" % index)
            if "body_regex" in step:
                re.compile(step["body_regex"])
            statuses = step.get("statuses", [200])
            if not isinstance(statuses, list) or not statuses or not all(isinstance(x, int) for x in statuses):
                raise ValueError("step %d: statuses must be a nonempty list of codes" % index)
        if action == "send_serial":
            if ("text" in step) == ("credential" in step):
                raise ValueError("step %d: specify text or private credential, exclusively" % index)
            if "text" in step and ("\n" in step["text"] or "\r" in step["text"]):
                raise ValueError("step %d: one console command per step" % index)
            if "credential" in step and step["credential"] not in ("username", "password"):
                raise ValueError("step %d: unsupported credential field" % index)
            if not isinstance(step.get("enter", True), bool):
                raise ValueError("step %d: enter must be boolean" % index)
        if action in ("sleep", "wait_serial", "probe_http"):
            if float(step.get("seconds" if action == "sleep" else "timeout_sec", 0)) <= 0:
                raise ValueError("step %d: positive timeout/seconds required" % index)
    return data


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never leave localhost via HTTP redirect.


def probe_local_http(step):
    # Explicitly bypass proxy environment variables: never turn an HTTP probe
    # into a network request through a CI runner's configured proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    req = urllib.request.Request(step["url"], headers={"User-Agent": "router-emulator-lab/1"})
    try:
        with opener.open(req, timeout=3) as response:
            status = response.status
            body = response.read(262144).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as error:
        status = error.code
        body = error.read(262144).decode("utf-8", errors="replace")
    return status in step.get("statuses", [200]) and (
        "body_regex" not in step or bool(re.search(step["body_regex"], body))
    )


def send_monitor(path, text):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(3)
        sock.connect(str(path))
        sock.sendall(text.encode() + b"\n")


def serial_payload(step, credentials=None):
    if "credential" in step:
        if credentials is None:
            raise ValueError("private credentials required")
        text = credentials[step["credential"]]
    else:
        text = step["text"]
    return text.encode() + (b"\r" if step.get("enter", True) else b"")


def monitor_wait(path, command):
    """Wait for the monitor acknowledgement before copying paused disks."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(5)
        sock.connect(str(path))
        greeting = b''
        while b'(qemu)' not in greeting:
            part = sock.recv(65536)
            if not part:
                raise RuntimeError('monitor disconnected')
            greeting += part
        sock.sendall(command.encode() + b'\n')
        response = b''
        while b'(qemu)' not in response:
            part = sock.recv(65536)
            if not part:
                raise RuntimeError('monitor disconnected')
            response += part
        if b'Error:' in response:
            raise RuntimeError('monitor command rejected')


def drain_serial(reader):
    """Keep socket TX flowing; evidence is stored by QEMU's private logfile."""
    while True:
        try:
            if not reader.recv(65536):
                return
        except socket.timeout:
            continue
        except OSError:
            return


def write_report(out, scenario, steps, outcome, reason, elapsed):
    out.mkdir(parents=True, exist_ok=True)
    os.chmod(out, 0o700)
    report = {
        "scenario": scenario["name"],
        "evidence": scenario["evidence"],
        "outcome": outcome,
        "elapsed_seconds": round(elapsed, 2),
        "steps": steps,
        "failure": reason,
        "warning": "Virtual test evidence is NOT physical-router acceptance.",
    }
    (out / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    suite = ET.Element("testsuite", name=scenario["name"], tests=str(len(steps)),
                       failures=str(sum(x["outcome"] == "FAIL" for x in steps)),
                       time=str(round(elapsed, 2)))
    for row in steps:
        test = ET.SubElement(suite, "testcase", name=row["action"],
                             time=str(row["elapsed_seconds"]))
        if row["outcome"] == "FAIL":
            ET.SubElement(test, "failure", message=row.get("reason", "step failed"))
    ET.ElementTree(suite).write(out / "junit.xml", encoding="utf-8", xml_declaration=True)


def run(args):
    scenario = load_scenario(args.scenario)
    credentials = None
    if getattr(args, "credentials_file", None):
        cp = Path(args.credentials_file)
        if cp.is_symlink() or not cp.is_file():
            raise ValueError("invalid private credentials file")
        if os.name == "posix" and cp.stat().st_mode & 0o077:
            raise ValueError("credentials file must be private (0600)")
        credentials = json.loads(cp.read_text(encoding="utf8"))
        if (not isinstance(credentials, dict)
                or not re.fullmatch(r"[a-z][a-z0-9_-]{2,30}", credentials.get("username", ""))
                or not re.fullmatch(r"[A-Za-z0-9_-]{24,128}", credentials.get("password", ""))):
            raise ValueError("invalid lab credential format")
    root = Path(args.emulator_root).resolve()
    source = Path(args.nand_dir).resolve()
    if not (root / "emulator.sh").is_file():
        raise ValueError("missing emulator.sh")
    if not source.is_dir():
        raise ValueError("NAND/flash input directory missing")
    out = Path(args.output_dir).resolve()
    # Never reuse UART logs: a prior successful boot could fake a new PASS.
    if out.exists() and any(out.iterdir()):
        raise ValueError("output directory must be new or empty")
    out.mkdir(parents=True, exist_ok=True)
    if source == out or out in source.parents or source in out.parents:
        raise ValueError("flash input and reports must be in separate directories")

    # Private workspace, snapshot copy prevents QEMU writes to caller's flash.
    # Never attach the original (potentially production) NAND image directly.
    workbase = root / "work"
    workbase.mkdir(exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="keenlab-", dir=workbase))
    nand = work / "flash"
    logdir = out / "console"
    logdir.mkdir(exist_ok=True)
    monitor = work / "monitor.sock"
    serial = work / "serial.sock"
    app_log = out / "emulator.log"
    proc = None
    conn = None
    rows = []
    failure = None
    begin = time.monotonic()
    hard_deadline = begin + args.max_seconds
    cursor = 0
    logfile = None
    app_fd = None

    def snapshot(destination):
        destination = Path(destination).resolve()
        if destination.exists():
            raise ValueError('private snapshot destination must be new')
        destination.mkdir(parents=True, mode=0o700)
        monitor_wait(monitor, 'stop')
        try:
            shutil.copytree(nand, destination / 'flash')
            if (work / 'opt.img').is_file():
                shutil.copyfile(work / 'opt.img', destination / 'opt.img')
                os.chmod(destination / 'opt.img', 0o600)
        finally:
            monitor_wait(monitor, 'cont')

    def ensure_running():
        if time.monotonic() >= hard_deadline:
            raise TimeoutError("scenario overall time budget exceeded")
        if proc.poll() is not None:
            raise RuntimeError("emulator exited unexpectedly (exit=%s)" % proc.returncode)

    def deadline(seconds):
        return min(hard_deadline, time.monotonic() + seconds)

    def console_file():
        nonlocal logfile
        if logfile is None:
            found = sorted(logdir.glob("console_*.log"))
            if found:
                logfile = found[-1]
        return logfile

    def wait_serial(pattern, seconds, reject_pattern=None):
        nonlocal cursor
        rx = re.compile(pattern.encode())
        reject = re.compile(reject_pattern.encode()) if reject_pattern else None
        until = deadline(seconds)
        # Read in bytes: read cursor is valid despite guest reboot, and regex
        # cannot accidentally match a message from an earlier boot stage.
        pending = b""
        while time.monotonic() < until:
            ensure_running()
            path = console_file()
            if path and path.exists():
                with path.open("rb") as inp:
                    inp.seek(cursor)
                    new = inp.read()
                    if new:
                        cursor += len(new)
                        pending = (pending + new)[-1048576:]
                        match = rx.search(pending)
                        rejected = reject.search(pending) if reject else None
                        if rejected and (not match or rejected.start() < match.start()):
                            raise RuntimeError("guest rejected serial stage")
                        if match:
                            # Preserve bytes after the match for the next step;
                            # boot messages can arrive in a single log write.
                            cursor = cursor - len(pending) + match.end()
                            return
            time.sleep(0.25)
        raise TimeoutError("serial marker not reached")

    def ensure_serial():
        nonlocal conn
        until = deadline(10)
        while time.monotonic() < until:
            ensure_running()
            try:
                conn = socket.socket(socket.AF_UNIX)
                conn.settimeout(2)
                conn.connect(str(serial))
                threading.Thread(target=drain_serial, args=(conn,), daemon=True).start()
                return
            except OSError:
                if conn:
                    conn.close()
                    conn = None
                time.sleep(0.2)
        raise TimeoutError("QEMU serial socket unavailable")

    try:
        shutil.copytree(source, nand, symlinks=False)
        app_fd = app_log.open("wb")
        command = [
            str(root / "emulator.sh"), "-P", args.preset, "-n", str(nand),
            "-w", "offline", "-l", "user", "-u", "none",
            "-S", str(serial), "-m", str(monitor), "-L", str(logdir),
            "-o", "lan-forwards=%s" % args.lan_forwards,
        ]
        if getattr(args, "opt_image", None):
            source_opt = Path(args.opt_image)
            if source_opt.is_symlink() or not source_opt.is_file() or source_opt.suffix != ".img":
                raise ValueError("USB input must be a regular .img file")
            source_opt = source_opt.resolve()
            opt_copy = work / "opt.img"
            shutil.copyfile(source_opt, opt_copy)
            command += ["--", "-drive", "if=none,id=labopt,format=raw,file=" + str(opt_copy),
                        "-device", "usb-storage,drive=labopt"]
        env = os.environ.copy()
        # Caller environment cannot silently add an external slirp tunnel.
        env.pop("WAN_EXTRA", None)
        env.pop("LAN_EXTRA", None)
        if getattr(args, "mirror_port", None):
            # A single explicit guest-to-loopback mapping. Both slirp networks
            # remain restrict=on; no arbitrary WAN or host filesystem access.
            port = int(args.mirror_port)
            if not 1024 <= port <= 65535:
                raise ValueError("invalid loopback mirror port")
            env["LAN_EXTRA"] = ",guestfwd=tcp:192.168.1.100:80-cmd:socat STDIO TCP4:127.0.0.1:%d" % port
            if getattr(args, 'mirror_tls_port', None):
                tls_port = int(args.mirror_tls_port)
                if not 1024 <= tls_port <= 65535:
                    raise ValueError('invalid loopback TLS mirror port')
                env['LAN_EXTRA'] += ',guestfwd=tcp:192.168.1.100:443-cmd:socat STDIO TCP4:127.0.0.1:%d' % tls_port
        proc = subprocess.Popen(command, cwd=root, stdout=app_fd,
                                stderr=subprocess.STDOUT, start_new_session=True, env=env,
                                umask=0o077)
        if any(step["action"] == "send_serial" for step in scenario["steps"]):
            ensure_serial()
        for index, step in enumerate(scenario["steps"], 1):
            action = step["action"]
            start = time.monotonic()
            print("[%d/%d] %s" % (index, len(scenario["steps"]), action), flush=True)
            try:
                if action == "wait_serial":
                    wait_serial(step["pattern"], float(step["timeout_sec"]), step.get("reject_pattern"))
                elif action == "probe_http":
                    until = deadline(float(step["timeout_sec"]))
                    while time.monotonic() < until:
                        ensure_running()
                        try:
                            if probe_local_http(step):
                                break
                        except (OSError, ValueError):
                            pass
                        time.sleep(1)
                    else:
                        raise TimeoutError("HTTP localhost response did not match")
                elif action == "send_serial":
                    if conn is None:
                        ensure_serial()
                    conn.sendall(serial_payload(step, credentials))
                elif action == "reset":
                    # Mark the log EOF BEFORE reset, so BL2 from first boot
                    # cannot be mistaken for a second boot.
                    path = console_file()
                    cursor = path.stat().st_size if path and path.exists() else 0
                    send_monitor(monitor, "system_reset")
                elif action == "sleep":
                    until = deadline(float(step["seconds"]))
                    while time.monotonic() < until:
                        ensure_running()
                        time.sleep(0.25)
                rows.append({"action": action, "outcome": "PASS",
                             "elapsed_seconds": round(time.monotonic() - start, 2)})
            except Exception as error:
                # Do not echo guest console, commands, URL or firmware content
                # into the structured report or status line.
                reason = type(error).__name__ + ": " + (
                    str(error) if isinstance(error, TimeoutError) else "step execution failed"
                )
                rows.append({"action": action, "outcome": "FAIL",
                             "elapsed_seconds": round(time.monotonic() - start, 2),
                             "reason": reason})
                failure = reason
                break
        if failure is None and getattr(args, "after_scenario", None):
            # Trusted Python controller hook, never loaded from scenario data.
            # Used for authenticated guest probes while the disposable VM lives.
            args.after_scenario(monitor=monitor, console_dir=logdir, deadline=hard_deadline,
                                snapshot=snapshot)
    except Exception as error:
        failure = "%s: setup or emulator start failed" % type(error).__name__
        print('CONTROLLER EXCEPTION: %s: %s'
              % (type(error).__name__, str(error)[:200]), flush=True)
        rows.append({"action": "setup", "outcome": "FAIL",
                     "elapsed_seconds": round(time.monotonic() - begin, 2), "reason": failure})
    finally:
        if conn:
            conn.close()
        if proc:
            if proc.poll() is None:
                try:
                    send_monitor(monitor, "quit")
                    proc.wait(timeout=3)
                except (OSError, subprocess.TimeoutExpired):
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    proc.wait(timeout=5)
        if app_fd:
            app_fd.close()
        # Copies of proprietary flash never enter artifacts; always delete.
        shutil.rmtree(work, ignore_errors=True)
        write_report(out, scenario, rows, "FAIL" if failure else "PASS",
                     failure, time.monotonic() - begin)
    print("RESULT %s — %s" % ("FAIL" if failure else "PASS", out / "report.json"))
    return 1 if failure else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--preset", required=True)
    ap.add_argument("--nand-dir", required=True, help="local private flash directory (copied into a disposable workspace)")
    ap.add_argument("--emulator-root", default=".")
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--lan-forwards", default="8080:80;8443:443;8022:22")
    ap.add_argument("--max-seconds", type=int, default=1200)
    ap.add_argument("--credentials-file", help="private runtime credentials; never included in reports")
    ap.add_argument("--opt-image", help="disposable copy of a raw USB storage image")
    ap.add_argument("--mirror-port", type=int, help="explicit loopback-only package mirror port")
    opts = ap.parse_args()
    if opts.max_seconds <= 0:
        ap.error("--max-seconds must be positive")
    try:
        return run(opts)
    except (ValueError, OSError) as error:
        ap.error(str(error))


if __name__ == "__main__":
    sys.exit(main())

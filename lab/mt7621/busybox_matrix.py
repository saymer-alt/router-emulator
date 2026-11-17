#!/usr/bin/env python3
"""Issue #226 (section F): MT7621/MIPSel Entware BusyBox lifecycle matrix.

Runs the positive/negative matrix of the 17.11 plan against a disposable
Ported 5.1.8 guest with a real opkg database: fresh bootstrap baseline,
a real version transition with genuine Entware IPKs, feed/TLS failures,
maintainer-script interruption windows and package-owned recovery.

All package sources are pinned by SHA-256 and fetched fail-closed; the KAS
read-only preflight is supplied by the caller (--preflight-file) and its
digest is re-verified before any guest transfer.  Nothing outside the
disposable QEMU guest and the private workspace is modified.

Every scenario runs on a freshly built EXT4 /opt image and a fresh mirror
(build() refuses to reuse them), so the NAND source stays pristine.
Private credentials and full logs stay in --workspace; only the derived
evidence JSON/JUnit land in --report-dir.
"""
import argparse
import hashlib
import io
import json
import re
import sys
import tarfile
import time
import traceback
import urllib.request
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from lab.build_entware import build  # noqa: E402
from lab.guest import Guest  # noqa: E402
from lab.kas_lifecycle import put as guest_put  # noqa: E402
from lab.mirror import Mirror  # noqa: E402
from lab.run import run, send_monitor  # noqa: E402

LOCK = REPO / "lab" / "entware-mipselsf.lock.json"
PRESET = "xiaomi-mi-router-3g-breed"
FORWARDS = "8080:80;8022:222;8023:23"

# Real Entware packages for the transition matrix (fail-closed pinning).
# The historical 1.37.0-6 is no longer published (bin/test.entware.net both
# answer 404, verified 2026-11-17/18), so the oldest verifiable published
# package is used as the pre-upgrade baseline instead of inventing a file.
OLD_IPK_URL = "https://test.entware.net/mipselsf-k3.4/busybox_1.38.0-3_mipsel-3.4.ipk"
OLD_IPK_SHA256 = "29954f847e9442fc30cebf6bc7dd17a1fc1d391c2fd037c6df582a30c0e40645"
OLD_IPK_VERSION = "1.38.0-3"

BOOT_PATTERN = "Boot and Recovery Environment"


def boot_steps(password_ready=True):
    """Pristine NAND -> system ready -> OPKG mount -> full pinned bootstrap."""
    return [
        {"action": "wait_serial", "pattern": BOOT_PATTERN, "timeout_sec": 120},
        {"action": "wait_serial", "pattern": "Starting kernel", "timeout_sec": 240},
        {"action": "wait_serial", "pattern": "Linux version", "timeout_sec": 120},
        {"action": "wait_serial", "pattern": "Brought up 4 CPUs", "timeout_sec": 240},
        {"action": "wait_serial", "pattern": "system ready", "timeout_sec": 600},
        {"action": "wait_serial", "pattern": "waiting for connections", "timeout_sec": 120},
        {"action": "sleep", "seconds": 15},
        {"action": "telnet", "port": 8023, "greeting": r"\(config\)>", "timeout_sec": 60},
        {"action": "telnet", "port": 8023, "send": "show version",
         "expect": "release: 5\\.01\\.C\\.8\\.0-2", "timeout_sec": 60,
         "greeting": r"\(config\)>"},
        {"action": "telnet", "port": 8023, "greeting": r"\(config\)>",
         "send": ["user admin", "password plain {password}", "exit",
                  "system configuration save", "opkg disk KASOPT:/"],
         "expect": r"\(config\)>", "timeout_sec": 180},
        {"action": "wait_serial", "pattern": "mounted to /opt", "timeout_sec": 180},
        {"action": "wait_serial", "pattern": r"\[1/5\]",
         "reject_pattern": "Critical error:|Please use ext2/ext3/ext4",
         "timeout_sec": 300},
        {"action": "wait_serial", "pattern": "configured init script",
         "reject_pattern": "Critical error:|Please use ext2/ext3/ext4",
         "timeout_sec": 600},
        {"action": "ssh", "port": 8022, "send": "/opt/bin/opkg print-architecture",
         "expect": "mipsel-3\\.4_kn 200", "timeout_sec": 120},
        {"action": "ssh", "port": 8022, "send": "/opt/bin/opkg list-installed",
         "expect": "entware-release - ", "timeout_sec": 60},
        {"action": "ssh", "port": 8022,
         "send": "/opt/bin/busybox test ! -e /opt/etc/init.d/doinstall"
                 " && printf 'DOINSTALL_%s\\n' ABSENT",
         "expect": "DOINSTALL_ABSENT", "timeout_sec": 60},
        {"action": "ssh", "port": 8022,
         "send": "printf MARK$((7*6)) > /opt/.lab-marker; /opt/bin/busybox cat /opt/.lab-marker",
         "expect": "MARK42", "timeout_sec": 60},
    ]


def fetch(url, digest, destination):
    """Fail-closed download of a pinned artifact (trusted origin + SHA-256)."""
    if not url.startswith("https://bin.entware.net/") and \
            not url.startswith("https://test.entware.net/"):
        raise ValueError("untrusted origin: %s" % url)
    destination = Path(destination)
    if destination.is_file():
        cached = destination.read_bytes()
        if hashlib.sha256(cached).hexdigest() == digest:
            return cached
        destination.unlink()
    for attempt in range(2):
        try:
            with urllib.request.urlopen(url, timeout=90) as response:
                raw = response.read()
            break
        except Exception:
            if attempt == 1:
                raise
            time.sleep(2)
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError("pinned digest mismatch: %s" % url)
    Path(destination).write_bytes(raw)
    return raw


class Recorder:
    """Observation-first evidence recorder: actual output is stored verbatim
    and only afterwards graded against the explicit expectation."""

    def __init__(self, kind, meta):
        self.kind = kind
        self.meta = meta
        self.entries = []

    def record(self, entry_id, label, command, output, expect):
        rc = None
        match = re.search(r"__RC__(-?[0-9]+)\s*$", output)
        if match:
            rc = int(match.group(1))
            output = output[:match.start()].rstrip("\n")
        status = "OBSERVED"
        reasons = []
        if expect:
            ok = True
            if "rc" in expect and rc != expect["rc"]:
                ok = False
                reasons.append("rc=%r expected %r" % (rc, expect["rc"]))
            for needle in expect.get("contains", []):
                if needle not in output:
                    ok = False
                    reasons.append("missing %r" % needle)
            for needle in expect.get("absent", []):
                if needle in output:
                    ok = False
                    reasons.append("unexpected %r" % needle)
            status = "PASS" if ok else "FAIL"
        self.entries.append({
            "id": entry_id, "label": label, "command": command,
            "rc": rc, "status": status, "reasons": reasons,
            "output_tail": output[-4000:],
        })
        print("[%s] %s -> %s%s" % (self.kind, entry_id, status,
                                   (" (%s)" % "; ".join(reasons)) if reasons else ""),
              flush=True)
        return self.entries[-1]

    def to_json(self):
        return {"kind": self.kind, "meta": self.meta, "entries": self.entries}

    def write_junit(self, path):
        cases = []
        for entry in self.entries:
            failure = ""
            if entry["status"] == "FAIL":
                failure = ("<failure message='%s'>%s</failure>"
                           % ("; ".join(entry["reasons"]).replace("'", ""),
                              entry["output_tail"][:200].replace("<", "&lt;")))
            cases.append(
                '  <testcase name="%s" classname="busybox-matrix.%s" '
                'status="%s">%s</testcase>'
                % (entry["id"], self.kind, entry["status"], failure))
        Path(path).write_text(
            '<?xml version="1.0" encoding="utf-8"?>\n<testsuite name="%s" '
            'tests="%d">\n%s\n</testsuite>\n'
            % (self.kind, len(cases), "\n".join(cases)), encoding="utf8")


class Matrix:
    def __init__(self, guest, recorder, deadline):
        self.guest = guest
        self.rec = recorder
        self.deadline = deadline

    def run(self, entry_id, label, command, expect=None, timeout=120):
        remaining = max(15, int(self.deadline - time.monotonic()))
        timeout = min(timeout, remaining)
        try:
            code, data, errors = self.guest.execute(command, timeout=timeout)
        except Exception as error:  # session loss is itself evidence
            self.rec.record(entry_id, label, command,
                            "<guest exception: %s>" % type(error).__name__, expect)
            raise
        return self.rec.record(entry_id, label, command,
                               data + ("\n" + errors if errors else "") + "\n__RC__%d" % code,
                               expect)

    def reconnect(self, timeout=120):
        self.guest.close()
        time.sleep(3)
        self.guest.connect(timeout)

    def preflight(self, entry_id, label):
        """KAS read-only preflight via the always-present busybox shell."""
        entry = self.run(
            entry_id, label,
            "/opt/bin/busybox sh /opt/lab/preflight.sh > /opt/lab/preflight-out.txt 2>&1;"
            " printf 'PREFLIGHT_RC=%s\\n' \"$?\";"
            " /opt/bin/busybox tail -40 /opt/lab/preflight-out.txt",
            timeout=180)
        match = re.search(r"PREFLIGHT_RC=([0-9]+)", entry["output_tail"])
        if match:
            entry["preflight_rc"] = int(match.group(1))
        return entry

    def baseline(self):
        self.run("BASELINE_BUSYBOX_STATUS", "opkg status busybox",
                 "/opt/bin/opkg status busybox | /opt/bin/busybox grep -E 'Version|Status'",
                 {"rc": 0})
        self.run("BASELINE_SH_LINK", "/opt/bin/sh symlink identity",
                 "/opt/bin/busybox ls -l /opt/bin/sh; /opt/bin/busybox readlink -f /opt/bin/sh",
                 {"rc": 0})
        self.run("BASELINE_VERSION", "busybox banner",
                 "/opt/bin/busybox | /opt/bin/busybox head -1; /opt/bin/sh -c 'echo SHELL_EXEC_OK'",
                 {"rc": 0, "contains": ["SHELL_EXEC_OK"]})
        self.run("BASELINEAPPLETS", "core applets executable",
                 "/opt/bin/busybox sh -c 'chmod --help >/dev/null 2>&1; printf A_%s $((40+$?));"
                 " rm --help >/dev/null 2>&1; printf R_%s $((40+$?));"
                 " sleep 0; printf S_%s $((40+$?)); ls /opt >/dev/null; printf L_%s $((40+$?))'",
                 {"rc": 0, "contains": ["A_40", "R_40", "S_40", "L_40"]})
        self.run("BASELINE_WGET_TLS", "wget/curl TLS capability inventory",
                 "/opt/bin/busybox ls -l /opt/bin/wget /opt/bin/wget-ssl 2>&1;"
                 " /opt/bin/busybox command -v curl && /opt/bin/curl --version | head -1",
                 timeout=30)
        self.run("BASELINE_Dropbear", "dropbear config + listener",
                 "/opt/bin/busybox cat /opt/etc/config/dropbear.conf;"
                 " /opt/bin/busybox netstat -tln 2>/dev/null | /opt/bin/busybox grep :222"
                 " || echo NETSTAT_UNAVAILABLE",
                 {"rc": 0}, timeout=30)

    def reboot_cycle(self, entry_prefix):
        self.guest.command('/bin/ndmc -c "system configuration save"')
        self.run(entry_prefix + "_REBOOT", "native system reboot",
                 "/bin/ndmc -c 'system reboot'", {"rc": 0}, timeout=30)
        self.guest.close()
        time.sleep(8)
        self.guest.connect(300)
        self.run(entry_prefix + "_POST_REBOOT", "/opt ext4 + opkg + busybox after reboot",
                 "/opt/bin/busybox grep ' /opt ext4' /proc/mounts | head -1;"
                 " /opt/bin/opkg status busybox | /opt/bin/busybox grep '^Version';"
                 " /opt/bin/sh -c 'echo SHELL_OK_AFTER_REBOOT'",
                 {"rc": 0, "contains": ["SHELL_OK_AFTER_REBOOT"]}, timeout=60)


def phase_fresh(mx):
    mx.baseline()
    mx.run("P01_FRESH_BOOTSTRAP_STATE", "busybox version from fresh pinned bootstrap",
           "/opt/bin/opkg status busybox | /opt/bin/busybox grep -E 'Package|Version'",
           {"rc": 0})
    mx.run("P02_OPKG_UPDATE", "opkg update against pinned local mirror",
           "/opt/bin/opkg update", {"rc": 0, "contains": ["Updated list"]}, timeout=180)
    mx.run("P03_INSTALL_CURRENT", "opkg install busybox at current version",
           "/opt/bin/opkg install busybox", {"rc": 0}, timeout=180)
    mx.run("P10_FORCE_REINSTALL", "opkg install --force-reinstall busybox (real scripts)",
           "/opt/bin/opkg install --force-reinstall busybox", {"rc": 0}, timeout=240)
    mx.run("P10_POST_REINSTALL_STATE", "applets + sh after forced reinstall",
           "/opt/bin/sh -c 'echo SHELL_OK';"
           " /opt/bin/busybox ls -l /opt/bin/.busybox.opkg-keep 2>&1 | head -1",
           {"rc": 0, "contains": ["SHELL_OK"]}, timeout=60)
    mx.preflight("PREFLIGHT_FRESH", "KAS preflight on healthy fresh bootstrap")
    mx.reboot_cycle("P11")
    mx.run("P12_IDEMPOTENT_UPGRADE", "opkg update && opkg upgrade busybox (no version delta)",
           "/opt/bin/opkg update >/dev/null 2>&1; /opt/bin/opkg upgrade busybox",
           {"rc": 0}, timeout=240)
    mx.run("P12_FINAL_STATE", "final busybox state",
           "/opt/bin/opkg status busybox | /opt/bin/busybox grep '^Version';"
           " /opt/bin/sh -c 'echo FINAL_SHELL_OK'",
           {"rc": 0, "contains": ["FINAL_SHELL_OK"]}, timeout=60)


def phase_transition(mx):
    mx.baseline()
    mx.run("P04_DOWNGRADE_TO_OLD", "real opkg downgrade 1.38.0-5 -> 1.38.0-3 (local pinned IPK)",
           "/opt/bin/opkg install --force-downgrade /opt/lab/busybox-old.ipk",
           {"rc": 0}, timeout=300)
    mx.run("P04_STATE_OLD", "installed version + .busybox.opkg-keep mechanism after downgrade",
           "/opt/bin/opkg status busybox | /opt/bin/busybox grep '^Version';"
           " /opt/bin/busybox ls -l /opt/bin/.busybox.opkg-keep 2>&1;"
           " /opt/bin/busybox readlink /opt/bin/sh;"
           " /opt/bin/busybox /opt/bin/busybox 2>/dev/null | head -1",
           {"rc": 0}, timeout=60)
    mx.preflight("PREFLIGHT_AFTER_DOWNGRADE", "KAS preflight after downgrade")
    mx.run("P08_HOLD_SET", "flag hold busybox while an older version is installed",
           "/opt/bin/opkg flag hold busybox;"
           " /opt/bin/opkg status busybox | /opt/bin/busybox grep -E '^Status'",
           {"rc": 0}, timeout=30)
    mx.run("P07A_OPKG_UPDATE", "opkg update (feed index now offers 1.38.0-5)",
           "/opt/bin/opkg update", {"rc": 0, "contains": ["Updated list"]}, timeout=180)
    mx.run("P07B_INSTALL_BUSYBOX", "the advised 'opkg install busybox' step (real upgrade here)",
           "/opt/bin/opkg install busybox", {"rc": 0}, timeout=300)
    mx.run("P07C_OPKG_UPGRADE", "mass opkg upgrade after the install step (busybox still held)",
           "/opt/bin/opkg upgrade", {"rc": 0}, timeout=600)
    mx.run("P08_UNHOLD", "unhold busybox after the transition",
           "/opt/bin/opkg flag unhold busybox;"
           " /opt/bin/opkg status busybox | /opt/bin/busybox grep -E '^Status'",
           {"rc": 0}, timeout=30)
    mx.run("P05_STATE_AFTER_TRANSITION", "final version + links + fresh SSH exec",
           "/opt/bin/opkg status busybox | /opt/bin/busybox grep '^Version';"
           " /opt/bin/busybox readlink /opt/bin/sh;"
           " /opt/bin/busybox ls -l /opt/bin/.busybox.opkg-keep 2>&1 | head -1",
           {"rc": 0}, timeout=60)
    mx.run("P05_APPLETS_AFTER_TRANSITION", "applets and warnings inventory",
           "/opt/bin/sh -c 'echo SHELL_OK';"
           " /opt/bin/busybox sh -c 'chmod --help >/dev/null 2>&1; printf A_%s $((40+$?));"
           " rm --help >/dev/null 2>&1; printf R_%s $((40+$?))'",
           {"rc": 0, "contains": ["SHELL_OK", "A_40", "R_40"]}, timeout=60)
    mx.preflight("PREFLIGHT_AFTER_TRANSITION", "KAS preflight after the full transition")
    mx.reboot_cycle("P11")
    mx.run("P12_IDEMPOTENT_UPGRADE", "second opkg upgrade (idempotence)",
           "/opt/bin/opkg upgrade busybox", {"rc": 0}, timeout=240)
    mx.run("P12_FINAL_STATE", "final state after idempotent upgrade",
           "/opt/bin/opkg status busybox | /opt/bin/busybox grep '^Version';"
           " /opt/bin/sh -c 'echo FINAL_SHELL_OK'",
           {"rc": 0, "contains": ["FINAL_SHELL_OK"]}, timeout=60)
    # Field case 17.11: a package touch alongside a modified conffile makes
    # opkg preserve the operator file as <name>-opkg. Reproduced on the
    # dropbear package itself (a busybox-only upgrade cannot touch it).
    mx.run("N14_MODIFY_DROPCONF", "operator modification of dropbear.conf",
           "/opt/bin/busybox sh -c 'echo \"# N14 lab modification\""
           " >> /opt/etc/config/dropbear.conf';"
           " /opt/bin/busybox tail -2 /opt/etc/config/dropbear.conf",
           {"rc": 0, "contains": ["N14 lab modification"]}, timeout=30)
    try:
        mx.run("N14_REINSTALL_DROPCONF", "opkg force-reinstall dropbear (conffile owner)",
               "/opt/bin/opkg install --force-reinstall dropbear", timeout=240)
    except Exception:
        pass  # dropbear restart may drop the channel; reconnect and inspect
    try:
        mx.reconnect(120)
        mx.run("N14_CONFILE_BACKUP", "dropbear.conf-opkg backup preserved",
               "/opt/bin/busybox ls /opt/etc/config/;"
               " /opt/bin/busybox ls -l /opt/etc/config/dropbear.conf-opkg 2>&1 | head -1",
               timeout=60)
    except Exception:
        mx.rec.record("N14_CONFILE_BACKUP", "dropbear.conf-opkg backup preserved",
                      "", "<session lost after dropbear reinstall>", {"contains": ["never"]})


def phase_feedfail(mx):
    mx.baseline()
    mx.run("N07_FEED_DOWN_UPDATE", "opkg update with feed unreachable (port closed)",
           "/opt/bin/busybox sed -i 's|http://192.168.1.100/entware|http://192.168.1.100:9/entware|'"
           " /opt/etc/opkg.conf; /opt/bin/opkg update; printf 'UPDATE_RC_MARKER_%s\\n' $((40+$?))",
           timeout=180)
    mx.run("N07_STATE_UNCHANGED", "busybox version and shell integrity after failed update",
           "/opt/bin/opkg status busybox | /opt/bin/busybox grep '^Version';"
           " /opt/bin/sh -c 'echo SHELL_UNCHANGED'",
           {"rc": 0, "contains": ["SHELL_UNCHANGED"]}, timeout=60)
    mx.run("N07_INSTALL_REFUSAL", "opkg install with broken feed must refuse cleanly",
           "/opt/bin/opkg install --force-reinstall bash", timeout=180)
    mx.preflight("N07_PREFLIGHT", "KAS preflight after feed outage")
    mx.run("N07_FEED_RESTORED", "restore feed and verify recovery",
           "/opt/bin/busybox sed -i 's|http://192.168.1.100:9/entware|http://192.168.1.100/entware|'"
           " /opt/etc/opkg.conf; /opt/bin/opkg update",
           {"rc": 0, "contains": ["Updated list"]}, timeout=180)
    mx.run("N08_TLS_CLIENT_ERRORS", "TLS failure modes of the available HTTP clients",
           "/opt/bin/busybox wget -T 5 -O /dev/null https://192.168.1.100/lab/preflight.sh 2>&1 | head -2;"
           " /opt/bin/busybox command -v curl &&"
           " /opt/bin/curl -sS --max-time 8 https://192.168.1.100/lab/preflight.sh -o /dev/null 2>&1 | head -2",
           timeout=60)
    mx.preflight("N08_PREFLIGHT", "KAS preflight after TLS probes")
    mx.run("N08_FINAL_STATE", "final integrity after failure scenarios",
           "/opt/bin/sh -c 'echo FINAL_SHELL_OK';"
           " /opt/bin/opkg status busybox | /opt/bin/busybox grep '^Version'",
           {"rc": 0, "contains": ["FINAL_SHELL_OK"]}, timeout=60)


def phase_interrupt(mx):
    mx.baseline()
    mx.run("N01_PIN_POSTINST", "package-owned recovery script pinned aside before the experiment",
           "/opt/bin/busybox cp /opt/lib/opkg/info/busybox.postinst /opt/lab/postinst-pinned;"
           " /opt/bin/busybox sha256sum /opt/lab/postinst-pinned",
           {"rc": 0}, timeout=30)
    # One persistent session: after the remove-branch prerm deletes the applet
    # links, no new SSH exec can start until recovery; all observations and the
    # package-owned recovery happen inside this single channel.
    mx.run(
        "N01_PRERM_WINDOW", "installed busybox.prerm (remove branch) executed outside opkg",
        "/opt/lib/opkg/info/busybox.prerm; printf 'PRERM_%s\\n' $((40+$?));"
        " /opt/bin/busybox ls -l /opt/bin/sh 2>&1 | head -1;"
        " /opt/bin/busybox test -e /opt/bin/chmod; printf 'CHMOD_PRESENT_%s\\n' $((40+$?));"
        " /bin/sh -c 'echo X' 2>&1 | head -1; printf 'NATIVE_SH_%s\\n' $((40+$?));"
        " /opt/bin/busybox sh /opt/lab/preflight.sh >/opt/lab/preflight-broken.txt 2>&1;"
        " printf 'PREFLIGHT_BROKEN_RC=%s\\n' \"$?\";"
        " /opt/bin/busybox tail -25 /opt/lab/preflight-broken.txt;"
        " /opt/bin/busybox sh /opt/lab/postinst-pinned; printf 'RECOVERY_%s\\n' $((40+$?));"
        " /opt/bin/busybox test -x /opt/bin/sh; printf 'SH_RESTORED_%s\\n' $((40+$?));"
        " /opt/bin/busybox test -x /opt/bin/chmod; printf 'CHMOD_RESTORED_%s\\n' $((40+$?))",
        timeout=300)
    mx.run("N01_NEW_SSH_SESSION", "fresh SSH exec after in-channel recovery",
           "/opt/bin/sh -c 'echo NEW_SESSION_SHELL_OK';"
           " /opt/bin/busybox sh -c 'rm --help >/dev/null 2>&1; printf RM_%s $((40+$?));"
           " sleep 0; printf SLEEP_%s $((40+$?))'",
           {"rc": 0, "contains": ["NEW_SESSION_SHELL_OK", "RM_40", "SLEEP_40"]}, timeout=60)
    mx.preflight("N01_PREFLIGHT_RECOVERED", "KAS preflight after package-owned recovery")
    mx.run("N10_KILL_MID_TRANSACTION", "kill -9 opkg during forced reinstall",
           "/opt/bin/opkg install --force-reinstall busybox > /opt/lab/reinstall.log 2>&1 & p=$!;"
           " sleep 0.3; kill -9 $p 2>/dev/null; wait $p; printf 'KILL_%s\\n' $((40+$?));"
           " /opt/bin/opkg status busybox | /opt/bin/busybox grep -E '^Status|^Version'",
           timeout=120)
    mx.run("N10_STATE_AFTER_KILL", "shell and applet integrity after killed transaction",
           "/opt/bin/sh -c 'echo SH_OK' 2>&1 | head -1;"
           " /opt/bin/busybox test -x /opt/bin/busybox; printf 'BB_PRESENT_%s\\n' $((40+$?))",
           timeout=60)
    mx.run("N15_RERUN_COMPLETES", "re-running the transaction completes (opkg self-recovery)",
           "/opt/bin/opkg install --force-reinstall busybox", {"rc": 0}, timeout=300)
    mx.run("N15_STATE_CLEAN", "clean state after the completed rerun",
           "/opt/bin/opkg status busybox | /opt/bin/busybox grep -E '^Status|^Version';"
           " /opt/bin/sh -c 'echo FINAL_SHELL_OK'",
           {"rc": 0, "contains": ["FINAL_SHELL_OK"]}, timeout=60)
    mx.preflight("PREFLIGHT_FINAL", "KAS preflight after interruption scenarios")


def after_scenario_factory(args, recorder, phases):
    def after_scenario(monitor=None, console_dir=None, deadline=None, snapshot=None):
        guest = Guest(args.credentials_file, args.workspace, port=8022,
                      deadline=deadline)
        guest.connect(180)
        mx = Matrix(guest, recorder, deadline if deadline is not None
                    else time.monotonic() + 600)
        try:
            guest.command("mkdir -p /opt/lab")
            guest_put(guest, "/opt/lab/preflight.sh", args.preflight_data)
            guest.command("/opt/bin/busybox sha256sum /opt/lab/preflight.sh"
                          " | /opt/bin/busybox grep -q %s" % args.preflight_sha256)
            if args.kind == "transition":
                guest_put(guest, "/opt/lab/busybox-old.ipk", args.old_ipk_data)
                guest.command("/opt/bin/busybox sha256sum /opt/lab/busybox-old.ipk"
                              " | /opt/bin/busybox grep -q %s" % OLD_IPK_SHA256)
            for phase in phases:
                try:
                    phase(mx)
                except Exception:
                    recorder.entries.append({
                        "id": "%s_PHASE_ABORT" % phase.__name__, "label": phase.__name__,
                        "command": "", "rc": None, "status": "FAIL",
                        "reasons": ["phase aborted: %s" % traceback.format_exc(limit=3)],
                        "output_tail": ""})
                    print("[%s] phase %s aborted" % (args.kind, phase.__name__), flush=True)
        finally:
            report_dir = Path(args.report_dir)
            report_dir.mkdir(parents=True, exist_ok=True)
            (report_dir / "evidence.json").write_text(
                json.dumps(recorder.to_json(), indent=2), encoding="utf8")
            recorder.write_junit(report_dir / "junit.xml")
            guest.close()
    return after_scenario


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--emulator-root", required=True)
    ap.add_argument("--nand-dir", required=True)
    ap.add_argument("--workspace", required=True,
                    help="fresh private directory for media, cache and reports")
    ap.add_argument("--cache-dir", required=True,
                    help="shared IPK cache (may already exist)")
    ap.add_argument("--credentials-file", required=True,
                    help='private {"username": "root", "password": "..."} JSON')
    ap.add_argument("--preflight-file", required=True,
                    help="host path of kas-entware-preflight.sh")
    ap.add_argument("--preflight-sha256", required=True)
    ap.add_argument("--kind", required=True,
                    choices=("fresh", "transition", "feedfail", "interrupt"))
    ap.add_argument("--report-dir", required=True)
    ap.add_argument("--max-seconds", type=int, default=3000)
    ap.add_argument("--prep-only", action="store_true",
                    help="host-side network phase only (fetch + build), then exit")
    ap.add_argument("--skip-build", action="store_true",
                    help="reuse an existing workspace prepared by --prep-only")
    args = ap.parse_args()

    workspace = Path(args.workspace)
    workspace.mkdir(mode=0o700, exist_ok=args.skip_build)
    credentials = json.loads(Path(args.credentials_file).read_text(encoding="utf8"))
    if credentials.get("username") != "root":
        raise ValueError("dropbear SSH probes authenticate as root")

    preflight_data = Path(args.preflight_file).read_bytes()
    if hashlib.sha256(preflight_data).hexdigest() != args.preflight_sha256:
        raise ValueError("preflight digest mismatch (fail closed)")
    Path(args.cache_dir).mkdir(parents=True, exist_ok=True)
    old_ipk_data = fetch(OLD_IPK_URL, OLD_IPK_SHA256,
                         Path(args.cache_dir) / ("busybox_%s_mipsel-3.4.ipk" % OLD_IPK_VERSION))
    args.preflight_data = preflight_data
    args.old_ipk_data = old_ipk_data
    lock = json.loads(LOCK.read_text(encoding="utf8"))
    pinned_bb = next(p for p in lock["packages"] if p["Package"] == "busybox")

    image, mirror = workspace / "opt.img", workspace / "mirror"
    if not args.skip_build:
        build(str(image), str(mirror), str(args.cache_dir), args.credentials_file,
              arch="mipsel", lock_path=str(LOCK))
    if args.prep_only:
        print("PREP_ONLY_DONE (mirror and image built; network phase complete)",
              flush=True)
        return 0
    recorder = Recorder(args.kind, {
        "arch": "mipsel-3.4", "preset": PRESET,
        "pinned_bootstrap_busybox": pinned_bb["Version"],
        "old_ipk": {"url": OLD_IPK_URL, "version": OLD_IPK_VERSION,
                    "sha256": OLD_IPK_SHA256},
        "preflight_sha256": args.preflight_sha256,
    })
    phases = [{"fresh": phase_fresh, "transition": phase_transition,
               "feedfail": phase_feedfail, "interrupt": phase_interrupt}[args.kind]]
    scenario = workspace / "scenario.json"
    scenario.write_text(json.dumps({
        "name": "MT7621 Entware BusyBox matrix (%s): pristine NAND + fresh pinned bootstrap"
                % args.kind,
        "evidence": "KEENETIC_PORTED_EXPERIMENTAL",
        "steps": boot_steps()}), encoding="utf8")
    with Mirror(Path(mirror)) as port:
        return run(SimpleNamespace(
            scenario=str(scenario), emulator_root=args.emulator_root,
            nand_dir=args.nand_dir, output_dir=workspace / "report",
            max_seconds=args.max_seconds, preset=PRESET, lan_forwards=FORWARDS,
            opt_image=str(image), archive_image=None, mirror_port=port,
            credentials_file=args.credentials_file,
            after_scenario=after_scenario_factory(args, recorder, phases)))


if __name__ == "__main__":
    sys.exit(main())

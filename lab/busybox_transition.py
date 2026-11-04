"""Issue #356: native disposable Entware BusyBox recovery/upgrade research.

The real old maintainer script is run in the *QEMU guest* after pinned Entware
bootstrap; the verified new IPK is installed by the guest's actual opkg.
No host /opt, user router, system feed, or production package is touched.
An interruption is reproduced by running the old prerm outside opkg, NOT by
interrupting an opkg transaction; the latter remains NOT TESTED.
"""
import hashlib
import io
from pathlib import Path
import re
import tarfile
import urllib.request
from urllib.parse import urlsplit

from lab.kas_lifecycle import put as put_guest_file

VERSION_OLD = "1.37.0-6"
VERSION_NEW = "1.38.0-3"
PACKAGE_URL = "https://test.entware.net/aarch64-k3.10/busybox_1.38.0-3_aarch64-3.10.ipk"
PACKAGE_SHA = "4d69a41404abd5df0aaf55633331d43e28b90b4446ca5b53ae987a9f04b5a381"
PRERM_OLD_SHA = "d9d1c4e7d81a5b46c39f22cded480c75e8b8d2849744a4cb84ca494b1dd407e4"
POSTINST_NEW_SHA = "479b2ad3cc2256e1ab537b85d6435282d658d3efdc9813253acafffad6647541"
# Digest of the pinned 1.37.0-6 monolithic postinst (verified against the real
# IPK bytes; identical to 1.38.0-3 postinst-pkg). opkg install of 1.38 replaces
# /opt/lib/opkg/info/busybox.postinst with the new functions.sh wrapper, so the
# upgrade script copies the old script aside BEFORE the real opkg install and
# re-verifies this digest before using it for the disposable-guest recovery.
POSTINST_OLD_SHA = "cb8c496360d70ac7581941cc397cfb059cc1e683e521e8b63360b2fe9f3671c7"
# Real foreign GNU grep binary: Entware grep 3.12-1 ships /opt/libexec/grep-gnu
# and exposes it through the opkg Alternatives symlink /opt/bin/grep, so
# /opt/bin/grep is NEVER a regular file and must not be digest-tracked itself.
FOREIGN_GREP = "/opt/libexec/grep-gnu"


def verify_package(raw):
    if len(raw) > 1_500_000 or hashlib.sha256(raw).hexdigest() != PACKAGE_SHA:
        raise ValueError("pinned BusyBox IPK differs")
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:*") as outer:
        compressed = next(
            (outer.extractfile(m).read() for m in outer.getmembers()
             if m.isfile() and m.name.removeprefix("./") == "control.tar.gz"), None)
    if compressed is None:
        raise ValueError("IPK has no control.tar.gz")
    with tarfile.open(fileobj=io.BytesIO(compressed), mode="r:gz") as ctrl:
        files = {m.name.removeprefix("./"): ctrl.extractfile(m).read()
                 for m in ctrl.getmembers() if m.isfile()
                 and m.name.removeprefix("./") in ("control", "postinst")}
    control = files.get("control", b"")
    if b"Package: busybox\n" not in control or b"Version: 1.38.0-3\n" not in control:
        raise ValueError("unexpected package metadata")
    if b"Architecture: aarch64-3.10\n" not in control:
        raise ValueError("unexpected package ABI")
    if hashlib.sha256(files.get("postinst", b"")).hexdigest() != POSTINST_NEW_SHA:
        raise ValueError("new postinst digest differs")
    if not files["postinst"].startswith(b"#!/bin/sh\n"):
        raise ValueError("new postinst interpreter differs")
    return raw


def fetch_verified():
    req = urllib.request.Request(PACKAGE_URL, headers={"User-Agent": "KAS-356-QEMU-lab/1"})
    with urllib.request.urlopen(req, timeout=50) as stream:
        final = urlsplit(stream.geturl())
        if final.scheme != "https" or final.hostname != "test.entware.net":
            raise ValueError("unexpected test-feed redirect")
        raw = stream.read(1_500_001)
    return verify_package(raw)


def upload(guest, path, content):
    if path not in ("/opt/lab/busybox-1.38.ipk", "/opt/lab/kas-entware-preflight.sh",
                    "/opt/lab/busybox-transition.sh", "/opt/lab/busybox-upgrade.sh"):
        raise ValueError("unexpected guest upload path")
    # Reuse the already native-QEMU-proven KAS transfer. The emulated
    # firmware does not promise /bin/mkdir or /bin/cat; Entware PATH does.
    # put_guest_file checks the destination path, writes mode 0600,
    # and verifies the received file's SHA256 inside the guest.
    guest.command("mkdir -p /opt/lab")
    put_guest_file(guest, path, content, mode='600')


def phase_script():
    # One persistent system-shell SSH command is critical: after prerm deletes
    # /opt/bin/sh, a *new* SSH login may be impossible until postinst repairs it.
    # A trap runs the old postinst on failure, but this guest is disposable even
    # if its runtime cannot be rescued.
    return ("""#!/bin/sh
set -eu
BB=/opt/bin/busybox
OLD_PRERM=/opt/lib/opkg/info/busybox.prerm
OLD_POSTINST=/opt/lib/opkg/info/busybox.postinst
[ -f "$OLD_PRERM" ] && [ -f "$OLD_POSTINST" ] || exit 61
set -- $("$BB" sha256sum "$OLD_PRERM")
[ "$1" = """ + PRERM_OLD_SHA + """ ] || exit 62
trap '"$BB" sh "$OLD_POSTINST" >/dev/null 2>&1 || :' EXIT
"$BB" sh "$OLD_PRERM" >/dev/null 2>&1 || exit 63
[ ! -e /opt/bin/sh ] || exit 64
[ ! -e /opt/bin/chmod ] || exit 65
printf 'BROKEN_BIN_COUNT=%s\n' "$("$BB" ls /opt/bin | "$BB" wc -l)"
set +e
/bin/sh /opt/lab/kas-entware-preflight.sh > /opt/lab/busybox-broken.txt 2>&1
rc=$?
"$BB" sh /opt/lab/kas-entware-preflight.sh > /opt/lab/busybox-broken-bb.txt 2>&1
bbsrc=$?
set -e
printf 'AFTER_PREFLIGHT_SH=%s\n' "$([ -e /opt/bin/sh ] && printf present || printf missing)"
printf 'AFTER_BIN_COUNT=%s\n' "$("$BB" ls /opt/bin | "$BB" wc -l)"
if [ "$rc" -ne 1 ] || [ "$bbsrc" -ne 1 ]; then
    printf 'PREFLIGHT_RC=%s\n' "$rc"
    printf 'PREFLIGHT_BB_RC=%s\n' "$bbsrc"
    printf 'PREFLIGHT_FAIL_IDS=%s\n' "$("$BB" grep -o '^FAIL: [a-zA-Z-]*' /opt/lab/busybox-broken.txt 2>/dev/null | "$BB" tr '\n' ',')"
    printf 'PREFLIGHT_UNKNOWN_IDS=%s\n' "$("$BB" grep -o '^UNKNOWN: [a-zA-Z-]*' /opt/lab/busybox-broken.txt 2>/dev/null | "$BB" tr '\n' ',')"
    printf 'PREFLIGHT_OUT_SHA=%s\n' "$("$BB" sha256sum /opt/lab/busybox-broken.txt | "$BB" cut -d ' ' -f1)"
    printf 'PREFLIGHT_BB_OUT_SHA=%s\n' "$("$BB" sha256sum /opt/lab/busybox-broken-bb.txt | "$BB" cut -d ' ' -f1)"
    printf '%s\n' 'PREFLIGHT_OUTPUT_BEGIN'
    "$BB" head -n 40 /opt/lab/busybox-broken.txt
    printf '%s\n' 'PREFLIGHT_OUTPUT_END'
    exit 66
fi
"$BB" grep -q '^FAIL: opt-sh:' /opt/lab/busybox-broken.txt || exit 67
"$BB" sh "$OLD_POSTINST" >/dev/null 2>&1 || exit 68
[ -x /opt/bin/sh ] && [ -x /opt/bin/chmod ] || exit 69
set +e
/bin/sh /opt/lab/kas-entware-preflight.sh > /opt/lab/busybox-restored.txt 2>&1
rc=$?
set -e
[ "$rc" -eq 0 ] || exit 70
trap - EXIT
printf 'NATIVE_PRERM_INTERRUPTED_PASS\\nNATIVE_POSTINST_RECOVERY_PASS\\n'
""")


def upgrade_script():
    # Same persistent-session rule as phase_script(): the real `opkg install`
    # runs the old 1.37 prerm, which deletes /opt/bin/sh. A *new* SSH exec
    # would then be impossible, so every post-install observation and the
    # sanctioned recovery must happen inside this one command. The 1.38
    # postinst is an OpenWrt-style wrapper requiring /opt/lib/functions.sh,
    # which no Entware package ships (static-analysis finding, 2026-11-05),
    # so the wrapper exits 0 without restoring links: whether /opt/bin/sh
    # survives is therefore OBSERVED and reported, never assumed.
    return ("""#!/bin/sh
set -eu
BB=/opt/bin/busybox
OPKG=/opt/bin/opkg
OLD_POSTINST=/opt/lib/opkg/info/busybox.postinst
SAVED_POSTINST=/opt/lab/busybox-old.postinst
[ -f "$OLD_POSTINST" ] || exit 71
"$BB" cp "$OLD_POSTINST" "$SAVED_POSTINST"
set -- $("$BB" sha256sum "$SAVED_POSTINST")
[ "$1" = """ + POSTINST_OLD_SHA + """ ] || exit 72
set +e
"$OPKG" install /opt/lab/busybox-1.38.ipk > /opt/lab/opkg-install.log 2>&1
rc=$?
set -e
[ "$rc" -eq 0 ] || exit 73
"$OPKG" list-installed > /opt/lab/installed-after.txt
"$BB" grep -q '^busybox - 1\\.38\\.0-3$' /opt/lab/installed-after.txt || exit 74
if [ -e /opt/bin/sh ] && [ -e /opt/bin/chmod ]; then
    printf 'NATIVE_UPGRADE_SH_RESTORED_BY_POSTINST\\n'
else
    printf 'NATIVE_UPGRADE_SH_MISSING_REPRODUCED\\n'
fi
"$BB" sh "$SAVED_POSTINST" >/dev/null 2>&1 || exit 75
[ -x /opt/bin/sh ] && [ -x /opt/bin/chmod ] || exit 76
printf 'NATIVE_UPGRADE_RECOVERY_PASS\\n'
""")


def require_busybox_version(guest, expected):
    """Use the proven native guest inventory API, not opkg status formatting.

    Guest.entware() already establishes the complete package versions from
    `opkg list-installed`. Reject zero or duplicate busybox entries and any
    unexpected version. Never publish the full (potentially sensitive) list.
    """
    rows = [line for line in guest.command("/opt/bin/opkg list-installed").splitlines()
            if line.startswith("busybox - ")]
    if rows != ["busybox - " + expected]:
        raise RuntimeError("unexpected installed BusyBox package version")
    return expected


def accept(guest, kas_dir, evidence):
    evidence.check("ENTWARE_BUSYBOX_TRANSITION", "NOT_TESTED", "unattempted")
    # Public log includes fixed phase + exception class only, never guest data.
    phase = "baseline-opkg"
    try:
        # The disposable VM already has real, pinned 1.37 and native opkg.
        require_busybox_version(guest, VERSION_OLD)
        phase = "baseline-abi"
        if guest.command("uname -m") != "aarch64":
            raise RuntimeError("unsupported guest ABI")
        phase = "download-verified-ipk"
        raw = fetch_verified()
        phase = "preflight-source"
        source = Path(kas_dir, "kas-entware-preflight.sh")
        if not source.is_file():
            raise RuntimeError("read-only preflight missing from KAS checkout")
        phase = "upload-ipk"
        upload(guest, "/opt/lab/busybox-1.38.ipk", raw)
        phase = "upload-preflight"
        upload(guest, "/opt/lab/kas-entware-preflight.sh", source.read_bytes())
        phase = "upload-scenario"
        upload(guest, "/opt/lab/busybox-transition.sh", phase_script().encode())
        # Capture the real foreign GNU grep binary. Entware grep 3.12-1 ships
        # /opt/libexec/grep-gnu (real ELF) and exposes it via the opkg
        # Alternatives symlink /opt/bin/grep, so the digest must track the
        # real file, not the symlink path (previous run failed exactly here).
        phase = "foreign-grep-baseline"
        if guest.command("test -f " + FOREIGN_GREP + " && test ! -L " + FOREIGN_GREP
                         + "; echo $?").strip() != "0":
            raise RuntimeError("expected separate GNU grep not present")
        grep_before = guest.command("/opt/bin/busybox sha256sum " + FOREIGN_GREP).split()[0]

        phase = "prerm-interruption-recovery"
        rc, out, err = guest.execute("/bin/sh /opt/lab/busybox-transition.sh", timeout=120)
        if rc != 0:
            # The guest script uses only synthetic, fixed exit codes 61-70.
            # Never print or parse its private stdout/stderr; the PREFLIGHT_*
            # tokens below are fixed-vocabulary check ids / a hex digest of the
            # preflight's own sanitized output, used only to label the failure.
            phase = ("prerm-interruption-recovery-exit-" + str(rc)
                     if rc in range(61, 71) else "prerm-interruption-recovery-exit-other")
            if rc == 66:
                rc_token = re.search(r"PREFLIGHT_RC=(\d+)", out)
                bb_token = re.search(r"PREFLIGHT_BB_RC=(\d+)", out)
                if rc_token:
                    phase += "-preflight-rc-" + rc_token.group(1)
                if bb_token:
                    phase += "-bb-rc-" + bb_token.group(1)
            raise RuntimeError("native scenario refused")
        if "NATIVE_PRERM_INTERRUPTED_PASS" not in out or "NATIVE_POSTINST_RECOVERY_PASS" not in out:
            phase = "prerm-interruption-recovery-markers"
            raise RuntimeError("native phase markers missing")
        phase = "foreign-grep-post-recovery"
        if guest.command("/opt/bin/busybox sha256sum " + FOREIGN_GREP).split()[0] != grep_before:
            raise RuntimeError("foreign GNU grep changed during maintainer scripts")

        phase = "upload-upgrade-script"
        upload(guest, "/opt/lab/busybox-upgrade.sh", upgrade_script().encode())
        # Actual upgrade with the real package manager, no feed mutation:
        # native opkg runs the ORIGINAL maintainer scripts, not a shell model.
        phase = "native-opkg-upgrade"
        rc, out, err = guest.execute("/bin/sh /opt/lab/busybox-upgrade.sh", timeout=300)
        if rc != 0:
            # The guest script uses only synthetic, fixed exit codes 71-76.
            # Never print or parse its private stdout/stderr.
            phase = ("native-opkg-upgrade-exit-" + str(rc)
                     if rc in range(71, 77) else "native-opkg-upgrade-exit-other")
            raise RuntimeError("native offline-local-IPK opkg upgrade refused")
        if "NATIVE_UPGRADE_RECOVERY_PASS" not in out:
            phase = "native-opkg-upgrade-markers"
            raise RuntimeError("native upgrade markers missing")
        reproduced = "NATIVE_UPGRADE_SH_MISSING_REPRODUCED" in out
        restored = "NATIVE_UPGRADE_SH_RESTORED_BY_POSTINST" in out
        if reproduced == restored:
            phase = "native-opkg-upgrade-observed-state"
            raise RuntimeError("exactly one post-install shell state must be reported")
        phase = "post-upgrade-opkg-inventory"
        require_busybox_version(guest, VERSION_NEW)
        phase = "foreign-grep-post-upgrade"
        if guest.command("/opt/bin/busybox sha256sum " + FOREIGN_GREP).split()[0] != grep_before:
            raise RuntimeError("foreign GNU grep changed during actual upgrade")
        if reproduced:
            evidence.check("ENTWARE_BUSYBOX_TRANSITION", "PASS",
                           "native_sh_disappearance_reproduced")
            print("ENTWARE BUSYBOX: native 1.38 upgrade left /opt/bin/sh and applets "
                  "missing; recovered in-guest with the pinned old postinst", flush=True)
        else:
            evidence.check("ENTWARE_BUSYBOX_TRANSITION", "PASS", "native_command_verified")
        print("ENTWARE BUSYBOX: native 1.37 prerm interruption + postinst recovery PASS", flush=True)
        print("ENTWARE BUSYBOX: real guest opkg local-IPK upgrade 1.37 -> 1.38 PASS", flush=True)
        print("ENTWARE BUSYBOX: actual interrupted opkg transaction NOT TESTED", flush=True)
        return True
    except (RuntimeError, ValueError, OSError, TimeoutError) as exc:
        evidence.check("ENTWARE_BUSYBOX_TRANSITION", "FAIL", "native_command_failed")
        # Never emit exception messages: SSH errors can contain sensitive data.
        # The diagnostic tokens below are fixed-vocabulary check ids, small
        # integers, present/missing words and a hex digest of the preflight's
        # own sanitized output; nothing else from the guest is printed.
        tokens = ""
        if rc == 66:
            pairs = re.findall(r"(BROKEN_BIN_COUNT|AFTER_PREFLIGHT_SH|AFTER_BIN_COUNT"
                               r"|PREFLIGHT_RC|PREFLIGHT_BB_RC|PREFLIGHT_FAIL_IDS"
                               r"|PREFLIGHT_UNKNOWN_IDS|PREFLIGHT_OUT_SHA"
                               r"|PREFLIGHT_BB_OUT_SHA)=([0-9a-zA-Z,-]*)", out)
            if pairs:
                tokens = " " + " ".join("%s=%s" % p for p in pairs)
            block = re.search(r"PREFLIGHT_OUTPUT_BEGIN\n(.*?)PREFLIGHT_OUTPUT_END",
                              out, re.DOTALL)
            if block:
                tokens += "\nENTWARE BUSYBOX: preflight output (sanitized, issue #356 diagnostic):\n" + block.group(1).strip()
        print("ENTWARE BUSYBOX: FAIL phase=%s class=%s%s (other details redacted)"
              % (phase, type(exc).__name__, tokens), flush=True)
        return False

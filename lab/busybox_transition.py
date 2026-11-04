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
                    "/opt/lab/busybox-transition.sh"):
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
set +e
/bin/sh /opt/lab/kas-entware-preflight.sh > /opt/lab/busybox-broken.txt 2>&1
rc=$?
set -e
[ "$rc" -eq 1 ] || exit 66
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
        # Capture a separate real foreign GNU grep binary; preserve its hash.
        phase = "foreign-grep-baseline"
        if guest.command("test -f /opt/bin/grep && test ! -L /opt/bin/grep; echo $?").strip() != "0":
            raise RuntimeError("expected separate GNU grep not present")
        grep_before = guest.command("/opt/bin/busybox sha256sum /opt/bin/grep").split()[0]

        phase = "prerm-interruption-recovery"
        rc, out, err = guest.execute("/bin/sh /opt/lab/busybox-transition.sh", timeout=120)
        if rc != 0:
            # The guest script uses only synthetic, fixed exit codes 61-70.
            # Never print or parse its private stdout/stderr.
            phase = ("prerm-interruption-recovery-exit-" + str(rc)
                     if rc in range(61, 71) else "prerm-interruption-recovery-exit-other")
            raise RuntimeError("native scenario refused")
        if "NATIVE_PRERM_INTERRUPTED_PASS" not in out or "NATIVE_POSTINST_RECOVERY_PASS" not in out:
            phase = "prerm-interruption-recovery-markers"
            raise RuntimeError("native phase markers missing")
        phase = "foreign-grep-post-recovery"
        if guest.command("/opt/bin/busybox sha256sum /opt/bin/grep").split()[0] != grep_before:
            raise RuntimeError("foreign GNU grep changed during maintainer scripts")

        # Actual upgrade with the real package manager, no feed mutation:
        # native opkg runs the ORIGINAL maintainer scripts, not a shell model.
        phase = "native-opkg-upgrade"
        rc, out, err = guest.execute(
            "/opt/bin/opkg install /opt/lab/busybox-1.38.ipk", timeout=300)
        if rc != 0:
            raise RuntimeError("native offline-local-IPK opkg upgrade failed")
        phase = "post-upgrade-opkg-inventory"
        require_busybox_version(guest, VERSION_NEW)
        phase = "post-upgrade-applets"
        if guest.command("/bin/sh -c '/opt/bin/sh -c \"exit 0\"; /opt/bin/chmod --help >/dev/null 2>&1; echo $?'") != "0":
            raise RuntimeError("installed 1.38 shell/applets unusable")
        phase = "foreign-grep-post-upgrade"
        if guest.command("/opt/bin/busybox sha256sum /opt/bin/grep").split()[0] != grep_before:
            raise RuntimeError("foreign GNU grep changed during actual upgrade")
        evidence.check("ENTWARE_BUSYBOX_TRANSITION", "PASS", "native_command_verified")
        print("ENTWARE BUSYBOX: native 1.37 prerm interruption + postinst recovery PASS", flush=True)
        print("ENTWARE BUSYBOX: real guest opkg local-IPK upgrade 1.37 -> 1.38 PASS", flush=True)
        print("ENTWARE BUSYBOX: actual interrupted opkg transaction NOT TESTED", flush=True)
        return True
    except (RuntimeError, ValueError, OSError, TimeoutError) as exc:
        evidence.check("ENTWARE_BUSYBOX_TRANSITION", "FAIL", "native_command_failed")
        # Never emit exception messages: SSH errors can contain sensitive data.
        print("ENTWARE BUSYBOX: FAIL phase=%s class=%s (private details redacted)"
              % (phase, type(exc).__name__), flush=True)
        return False

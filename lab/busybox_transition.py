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
# The ported guest's /bin/sh itself routes script execution through the
# Entware shell: with /opt/bin/sh deleted by the prerm it prints
# Bad interpreter "/opt/bin/sh" and exits 0 (run 41932162970 output).
# The preflight is therefore executed via the always-present busybox
# binary, one of the KAS-verified POSIX dialects.
"$BB" sh /opt/lab/kas-entware-preflight.sh > /opt/lab/busybox-broken.txt 2>&1
rc=$?
set -e
printf 'AFTER_PREFLIGHT_SH=%s\n' "$([ -e /opt/bin/sh ] && printf present || printf missing)"
printf 'AFTER_BIN_COUNT=%s\n' "$("$BB" ls /opt/bin | "$BB" wc -l)"
if [ "$rc" -ne 1 ]; then
    printf 'PREFLIGHT_RC=%s\n' "$rc"
    printf 'PREFLIGHT_FAIL_IDS=%s\n' "$("$BB" grep -o '^FAIL: [a-zA-Z-]*' /opt/lab/busybox-broken.txt 2>/dev/null | "$BB" tr '\n' ',')"
    printf 'PREFLIGHT_UNKNOWN_IDS=%s\n' "$("$BB" grep -o '^UNKNOWN: [a-zA-Z-]*' /opt/lab/busybox-broken.txt 2>/dev/null | "$BB" tr '\n' ',')"
    printf 'PREFLIGHT_OUT_SHA=%s\n' "$("$BB" sha256sum /opt/lab/busybox-broken.txt | "$BB" cut -d ' ' -f1)"
    exit 66
fi
"$BB" grep -q '^FAIL: opt-sh:' /opt/lab/busybox-broken.txt || exit 67
"$BB" sh "$OLD_POSTINST" >/dev/null 2>&1 || exit 68
[ -x /opt/bin/sh ] && [ -x /opt/bin/chmod ] || exit 69
set +e
"$BB" sh /opt/lab/kas-entware-preflight.sh > /opt/lab/busybox-restored.txt 2>&1
rc=$?
set -e
if [ "$rc" -ne 0 ]; then
    # Strictly fixed-vocabulary diagnostics, never print actual preflight
    # descriptions, guest paths, command stderr or raw package output.
    case "$rc" in 1|2) printf 'RESTORED_PREFLIGHT_RC=%s\n' "$rc" ;;
                     *) printf 'RESTORED_PREFLIGHT_RC=OTHER\n' ;; esac
    for _id in busybox-binary busybox-applets opt-sh \
        applet-chmod applet-ln applet-rm applet-cp applet-mv applet-ls \
        applet-cat applet-grep applet-sed applet-awk applet-wget \
        wget-capability opkg-view; do
        for _severity in FAIL UNKNOWN; do
            if "$BB" grep -q "^$_severity: $_id:" /opt/lab/busybox-restored.txt; then
                printf 'RESTORED_%s_%s=1\n' "$_severity" "$_id"
            fi
        done
    done
    # Fixed-vocabulary classification of the ACTUAL opkg results behind an
    # opkg-view UNKNOWN (issue #356): which preflight branch fired. Shell
    # builtins only; exit codes and fixed category words, never raw output.
    OPKG_BIN=""
    if [ -x /opt/bin/opkg ]; then OPKG_BIN=/opt/bin/opkg
    elif [ -x /opt/sbin/opkg ]; then OPKG_BIN=/opt/sbin/opkg; fi
    if [ -n "$OPKG_BIN" ]; then
        set +e
        "$OPKG_BIN" status busybox > /opt/lab/opkg-status.txt 2>/dev/null
        _st=$?
        "$OPKG_BIN" list-installed > /opt/lab/opkg-list.txt 2>/dev/null
        _li=$?
        set -e
        printf 'RESTORED_OPKG_STATUS_EXIT=%s\n' "$_st"
        if [ "$_st" -eq 0 ]; then
            if [ -s /opt/lab/opkg-status.txt ]; then
                printf 'RESTORED_OPKG_STATUS_OUT=nonempty\n'
            else
                printf 'RESTORED_OPKG_STATUS_OUT=empty\n'
            fi
        else
            printf 'RESTORED_OPKG_STATUS_OUT=failed\n'
        fi
        printf 'RESTORED_OPKG_LIST_EXIT=%s\n' "$_li"
        _rows=none
        if [ "$_li" -eq 0 ]; then
            _rows=none
            while IFS= read -r _l; do
                case "$_l" in
                    "busybox - "?*) if [ "$_rows" = "one" ]; then _rows=many; else _rows=one; fi ;;
                    "busybox - ") _rows=nover ;;
                esac
            done < /opt/lab/opkg-list.txt
        else
            _rows=list_failed
        fi
        printf 'RESTORED_OPKG_BUSYBOX_ROWS=%s\n' "$_rows"
        # Decompose the Status line of the saved record into its three fixed
        # fields (want/flag/state) so the exact wording that failed the
        # preflight's literal match is known without printing guest text.
        _want=unknown; _flag=unknown; _state=unknown
        while IFS= read -r _l; do
            case "$_l" in
                "Status: install ok installed")
                    _want=install; _flag=ok; _state=installed ;;
                "Status: install user installed")
                    _want=install; _flag=user; _state=installed ;;
                "Status: install auto installed")
                    _want=install; _flag=auto; _state=installed ;;
                "Status: install hold installed")
                    _want=install; _flag=hold; _state=installed ;;
                "Status: deinstall ok installed")
                    _want=deinstall; _flag=ok; _state=installed ;;
                "Status: install ok half-configured")
                    _want=install; _flag=ok; _state=half-configured ;;
            esac
        done < /opt/lab/opkg-status.txt
        printf 'RESTORED_OPKG_STATUS_WANT=%s\n' "$_want"
        printf 'RESTORED_OPKG_STATUS_FLAG=%s\n' "$_flag"
        printf 'RESTORED_OPKG_STATUS_STATE=%s\n' "$_state"
        if "$BB" grep -q 'install ok installed' /opt/lab/opkg-status.txt; then
            printf 'RESTORED_OPKG_STATUS_HAS_OK=1\n'
        else
            printf 'RESTORED_OPKG_STATUS_HAS_OK=0\n'
        fi
        if "$BB" grep -q 'hold' /opt/lab/opkg-status.txt; then
            printf 'RESTORED_OPKG_STATUS_HAS_HOLD=1\n'
        else
            printf 'RESTORED_OPKG_STATUS_HAS_HOLD=0\n'
        fi
        # Structure of the Status line itself: presence, word count and the
        # class of each of the first three words against the opkg status
        # vocabulary. Unknown words collapse to a fixed `other` class, so the
        # record shape is measurable without printing any guest text.
        _sl_present=0; _sl_words=0
        _w1=none; _w2=none; _w3=none
        while IFS= read -r _l; do
            case "$_l" in
                "Status: "*)
                    _sl_present=1
                    _rest=${_l#"Status: "}
                    _n=0
                    for _w in $_rest; do
                        _n=$((_n+1))
                        case "$_w" in
                            install|deinstall|hold|ok|user|auto|installed|unpacked|not-installed|half-configured|config-files|post-inst-failed|reinst-required|unknown) _cls=$_w ;;
                            *) _cls=other ;;
                        esac
                        case $_n in
                            1) _w1=$_cls ;;
                            2) _w2=$_cls ;;
                            3) _w3=$_cls ;;
                        esac
                    done
                    _sl_words=$_n
                    ;;
            esac
        done < /opt/lab/opkg-status.txt
        printf 'RESTORED_OPKG_STATUS_LINE=%s\n' "$_sl_present"
        printf 'RESTORED_OPKG_STATUS_WORDS=%s\n' "$_sl_words"
        printf 'RESTORED_OPKG_STATUS_W1=%s\n' "$_w1"
        printf 'RESTORED_OPKG_STATUS_W2=%s\n' "$_w2"
        printf 'RESTORED_OPKG_STATUS_W3=%s\n' "$_w3"
    else
        printf 'RESTORED_OPKG_BIN=missing\n'
    fi
    exit 70
fi
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
    transition_rc, transition_out = -1, ""
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
        transition_rc, transition_out = rc, out
        if rc != 0:
            # The guest script uses only synthetic, fixed exit codes 61-70.
            # Never print or parse its private stdout/stderr; the PREFLIGHT_*
            # tokens below are fixed-vocabulary check ids / a hex digest of the
            # preflight's own sanitized output, used only to label the failure.
            phase = ("prerm-interruption-recovery-exit-" + str(rc)
                     if rc in range(61, 71) else "prerm-interruption-recovery-exit-other")
            if rc == 66:
                rc_token = re.search(r"PREFLIGHT_RC=(\d+)", out)
                if rc_token:
                    phase += "-preflight-rc-" + rc_token.group(1)
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
        if transition_rc == 70:
            # Whitelist literal preflight IDs and strict return-code tokens.
            # Do not emit arbitrary guest output, even on an assumed
            # "sanitized" diagnostic channel.
            allowed_ids = (
                "busybox-binary", "busybox-applets", "opt-sh",
                "applet-chmod", "applet-ln", "applet-rm", "applet-cp",
                "applet-mv", "applet-ls", "applet-cat", "applet-grep",
                "applet-sed", "applet-awk", "applet-wget",
                "wget-capability", "opkg-view",
            )
            for literal in ("RESTORED_PREFLIGHT_RC=1",
                            "RESTORED_PREFLIGHT_RC=2",
                            "RESTORED_PREFLIGHT_RC=OTHER"):
                if literal in transition_out.splitlines():
                    tokens += " " + literal
            output_lines = set(transition_out.splitlines())
            for _severity in ("FAIL", "UNKNOWN"):
                for _id in allowed_ids:
                    literal = "RESTORED_%s_%s=1" % (_severity, _id)
                    if literal in output_lines:
                        tokens += " " + literal
            # Fixed opkg-view sub-category (issue #356): exact result class of
            # opkg status / list-installed. Only strict digits or one of the
            # fixed category words are forwarded; anything else stays redacted.
            for prefix in ("RESTORED_OPKG_STATUS_EXIT=", "RESTORED_OPKG_LIST_EXIT="):
                for line in output_lines:
                    if line.startswith(prefix) and line[len(prefix):].isdigit():
                        tokens += " " + line
                        break
            for literal in ("RESTORED_OPKG_STATUS_OUT=nonempty",
                            "RESTORED_OPKG_STATUS_OUT=empty",
                            "RESTORED_OPKG_STATUS_OUT=failed",
                            "RESTORED_OPKG_BIN=missing",
                            "RESTORED_OPKG_BUSYBOX_ROWS=none",
                            "RESTORED_OPKG_BUSYBOX_ROWS=one",
                            "RESTORED_OPKG_BUSYBOX_ROWS=nover",
                            "RESTORED_OPKG_BUSYBOX_ROWS=many",
                            "RESTORED_OPKG_BUSYBOX_ROWS=list_failed",
                            "RESTORED_OPKG_STATUS_HAS_OK=1",
                            "RESTORED_OPKG_STATUS_HAS_OK=0",
                            "RESTORED_OPKG_STATUS_HAS_HOLD=1",
                            "RESTORED_OPKG_STATUS_HAS_HOLD=0"):
                if literal in output_lines:
                    tokens += " " + literal
            for prefix, allowed_words in (
                    ("RESTORED_OPKG_STATUS_WANT=", ("install", "deinstall", "unknown")),
                    ("RESTORED_OPKG_STATUS_FLAG=", ("ok", "user", "auto", "hold", "unknown")),
                    ("RESTORED_OPKG_STATUS_STATE=",
                     ("installed", "half-configured", "unknown"))):
                for line in output_lines:
                    if line.startswith(prefix) and line[len(prefix):] in allowed_words:
                        tokens += " " + line
                        break
            if "RESTORED_OPKG_STATUS_LINE=0" in output_lines:
                tokens += " RESTORED_OPKG_STATUS_LINE=0"
            if "RESTORED_OPKG_STATUS_LINE=1" in output_lines:
                tokens += " RESTORED_OPKG_STATUS_LINE=1"
            for prefix in ("RESTORED_OPKG_STATUS_WORDS=",):
                for line in output_lines:
                    if line.startswith(prefix) and line[len(prefix):].isdigit():
                        tokens += " " + line
                        break
            word_classes = ("install", "deinstall", "hold", "ok", "user", "auto",
                            "installed", "unpacked", "not-installed",
                            "half-configured", "config-files", "post-inst-failed",
                            "reinst-required", "unknown", "other", "none")
            for prefix in ("RESTORED_OPKG_STATUS_W1=", "RESTORED_OPKG_STATUS_W2=",
                           "RESTORED_OPKG_STATUS_W3="):
                for line in output_lines:
                    if line.startswith(prefix) and line[len(prefix):] in word_classes:
                        tokens += " " + line
                        break
        print("ENTWARE BUSYBOX: FAIL phase=%s class=%s%s (other details redacted)"
              % (phase, type(exc).__name__, tokens), flush=True)
        return False

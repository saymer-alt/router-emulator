#!/usr/bin/env python3
"""Real pinned KAS installer smoke inside isolated Ported MT7621/MIPSel.

The controller never executes KAS on the Ubuntu host. The public KAS
checkout is verified by immutable commit SHA, then its exact install.sh
is copied by authenticated loopback SSH into a disposable guest. Guest
stdout/stderr remain private; only fixed result categories are published.
The default mode enforces the original KAS component guard. A separate
explicit MT7621 lab-only mode disables ONLY the ext-utils guard in a
throwaway in-memory copy after guest EXT4 and component checks; this mode
is NEVER suitable for real routers or for production KAS acceptance.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
from types import SimpleNamespace

from lab.build_entware import build
from lab.mirror import Mirror
from lab.run import run
from lab.kas_mirror import load_sources

LOCK = Path(__file__).resolve().parents[1] / "entware-mipselsf.lock.json"
PIN = re.compile(r"^[0-9a-f]{40}$")



REQUIRED_KEENETIC_COMPONENTS = ("proxy", "dns-filter", "opkg-kmod-netfilter",
                               "ext", "ext-utils")
ONE_SECURE_DNS = ("dns-tls", "dns-https")

# Exact guard in the immutable pinned KAS source. Changing any other part of
# KAS, faking ndmc component IDs or silently accepting upstream drift is
# forbidden. Never publish/install this modified copy on a physical router.
LAB_EXT_UTILS_GUARD = (b'if [ "$OPT_CLASS" = "external" ] && '
                       b'! component_list_has "$EXT_UTILS_COMPONENT_ID"; then')
LAB_EXT_UTILS_DISABLED = (b'if [ "$OPT_CLASS" = "external" ] && '
                          b'! component_list_has "$EXT_UTILS_COMPONENT_ID" && false; then')


def lab_only_without_ext_utils_guard(source):
    """One exact in-memory guard edit; fail closed on unexpected KAS source."""
    if not isinstance(source, bytes) or source.count(LAB_EXT_UTILS_GUARD) != 1:
        raise ValueError("pinned KAS ext-utils guard changed or missing")
    # The only byte change is the append of ' && false' on one if-condition.
    modified = source.replace(LAB_EXT_UTILS_GUARD, LAB_EXT_UTILS_DISABLED, 1)
    if (modified.count(LAB_EXT_UTILS_DISABLED) != 1
            or modified.replace(LAB_EXT_UTILS_DISABLED,
                                LAB_EXT_UTILS_GUARD, 1) != source):
        raise ValueError("unexpected lab-only installer mutation")
    return modified


def require_exact_lab_component_gap(component_gap):
    """A lab exception is allowed ONLY for the single known missing ID."""
    if component_gap != ["ext-utils"]:
        raise ValueError("MT7621 lab exception requires exactly ext-utils missing")


def missing_required_components(version_output):
    """Return only known component IDs, never raw device/version text.

    Mirrors KAS install.sh component_list_from_show_version field parsing:
    continuation lines can wrap even across a hyphenated identifier.
    An absent/unparseable inventory is UNKNOWN, not an invented missing set.
    """
    collect = False
    rows = []
    for line in version_output.splitlines():
        if not collect:
            match = re.match(r"^\s*components:\s*(.*)$", line)
            if match is None:
                continue
            collect = True
            rows.append(re.sub(r"\s+", "", match.group(1)))
            continue
        value = line.strip().replace(" ", "").replace("\t", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]+(?:,[A-Za-z0-9_-]+)*,?", value):
            break
        rows.append(value)
    if not collect:
        return None
    observed = set("".join(rows).split(","))
    absent = [item for item in REQUIRED_KEENETIC_COMPONENTS if item not in observed]
    if not any(x in observed for x in ONE_SECURE_DNS):
        absent.append("dns-tls-or-dns-https")
    return absent

def classify_failure(code, output):
    """Map private installer output to an allowlisted public failure category."""
    if code == 0 and "[OK] Done" in output:
        return "INSTALLER_COMPLETED"
    low = output.lower()
    if ("full required component contract" in low
            or "enable the missing component" in low
            or "required component" in low and "missing" in low):
        return "KEENETICOS_COMPONENTS_MISSING"
    if "opkg update failed" in low:
        return "OPKG_FEED_UNAVAILABLE"
    if ("failed to install" in low or "package unavailable" in low
            or "mihomo install failed" in low):
        return "IPK_UNAVAILABLE"
    if "architecture" in low and ("unsupported" in low or "cannot" in low):
        return "MIPSEL_ABI_UNSUPPORTED"
    if "download failed" in low or "curl" in low and "failed" in low:
        return "PINNED_ASSET_UNAVAILABLE"
    if code == 0:
        return "SUCCESS_MARKER_MISSING"
    return "INSTALLER_EXIT_NONZERO"


def guarded_refusal_proven(exit_code, category, component_gap, package_list, no_ownership):
    """Actual exit 1, exactly ext-utils missing, no owned KAS packages/state."""
    if exit_code != 1 or category != "KEENETICOS_COMPONENTS_MISSING":
        return False
    if component_gap != ["ext-utils"] or not no_ownership:
        return False
    return not any(re.search(r"^" + re.escape(p) + r" - ", package_list, re.M)
                   for p in ("mihomo", "magitrickle"))


def write_public(path, kas_ref, phase, reason, exit_code=None, component_gap=None,
                 guard_verified=False, guarded_refusal_expected=False,
                 lab_exception_applied=False):
    """No guest output, passwords, paths, script bodies, or arbitrary diagnostics."""
    record = {"schema": "mt7621-kas-smoke-v1", "kas_ref": kas_ref,
              "phase": phase, "reason": reason, "kas_installed": phase == "PASS",
              "exit_code": exit_code if isinstance(exit_code, int) else None,
              "component_inventory_seen": component_gap is not None,
              "missing_component_ids": component_gap if component_gap is not None else [],
              "test_expectation": ("guarded_refusal" if guarded_refusal_expected
                                   else "full_install"),
              "guard_contract_verified": guard_verified,
              "lab_ext_utils_exception_applied": bool(lab_exception_applied),
              "source_variant": ("DISPOSABLE_LAB_EXT_UTILS_EXCEPTION"
                                 if lab_exception_applied else "PINNED_UNMODIFIED")}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, sort_keys=True, indent=2) + "\n",
                    encoding="utf-8")


def guest_probe(credentials, workspace, deadline, source, kas_ref, public,
                guarded_refusal_expected=False, lab_ext_utils_exception=False):
    """Trusted run.py post-scenario hook; VM remains alive for the whole probe."""
    from lab.guest import Guest  # lazy: unit tests do not require Paramiko
    guest = Guest(credentials, workspace, deadline=deadline)
    phase, reason, exit_code = "NOT_TESTED", "GUEST_NOT_REACHED", None
    component_gap = None
    guard_verified = False
    exception_applied = False
    if guarded_refusal_expected and lab_ext_utils_exception:
        raise ValueError("lab exception is incompatible with guarded-refusal test")
    try:
        guest.connect(120)
        phase, reason = "BLOCKED", "ENTWARE_PREFLIGHT_UNAVAILABLE"
        required = ('test -x /opt/bin/opkg && test -x /opt/bin/bash && '
                    'test -r /opt/etc/init.d/rc.unslung && '
                    'grep -q " /opt ext4 " /proc/mounts && '
                    '/opt/bin/opkg print-architecture | '
                    'grep -q "mipsel-3.4_kn 200"')
        guest.command(required, timeout=45)
        version_output = guest.command('/bin/ndmc -c "show version"', timeout=45)
        component_gap = missing_required_components(version_output)
        if component_gap is None:
            print("KAS COMPONENT INVENTORY: UNKNOWN", flush=True)
        else:
            print("KAS COMPONENT INVENTORY: verified required IDs, missing=%d" %
                  len(component_gap), flush=True)
        print("KAS MIPSEL PREFLIGHT: native Entware and ndmc reachable", flush=True)
        phase, reason = "BLOCKED", "PINNED_SOURCE_DELIVERY_FAILED"
        guest.command("mkdir -p /opt/lab; chmod 700 /opt/lab", timeout=30)
        if lab_ext_utils_exception:
            # Fail closed before upload: on this exact Ported image ALL other
            # original KAS components are present. No spoofed component list.
            phase, reason = "BLOCKED", "LAB_COMPONENT_SET_UNEXPECTED"
            require_exact_lab_component_gap(component_gap)
            # The initial preflight already verified a mounted /opt ext4.
            # Explicit round-trip demonstrates it is writable, and then the
            # synthetic file is removed from this disposable guest only.
            phase, reason = "BLOCKED", "LAB_EXT4_READ_WRITE_UNVERIFIED"
            guest.command(
                "umask 077; printf LAB_EXT4_RW_OK > /opt/lab/.kas-ext4-rw; "
                "grep -q ' /opt ext4 rw' /proc/mounts && "
                'test "$(cat /opt/lab/.kas-ext4-rw)" = LAB_EXT4_RW_OK && '
                "rm -f /opt/lab/.kas-ext4-rw",
                timeout=30)
            phase, reason = "BLOCKED", "LAB_SOURCE_PATCH_UNAVAILABLE"
            source = lab_only_without_ext_utils_guard(source)
            exception_applied = True
            print("KAS MT7621 LAB EXCEPTION: only ext-utils guard disabled "
                  "in disposable source", flush=True)
            phase, reason = "BLOCKED", "PINNED_SOURCE_DELIVERY_FAILED"
        # Entware Dropbear does not necessarily ship an SFTP subsystem.
        # Reuse the proven Paramiko SSH-stdin upload contract from the ARM64 lab:
        # authenticated loopback exec + private stdin + native checksum.
        guest.command("umask 077; cat > /opt/lab/install.sh; "
                      "chmod 600 /opt/lab/install.sh",
                      timeout=90, input_data=source)
        expected = hashlib.sha256(source).hexdigest()
        observed = guest.command("/opt/bin/busybox sha256sum /opt/lab/install.sh",
                                 timeout=30).split()[0]
        if expected != observed:
            raise RuntimeError("pinned guest source digest mismatch")
        print("KAS MIPSEL SOURCE: pinned checkout transferred and verified", flush=True)

        # A real KAS installation attempt. No host-side mock or fake PASS.
        # The guest has an offline WAN and a pinned 25-IPK local feed. Current
        # Ported images may not have all production KeeneticOS components.
        phase, reason = "BLOCKED", "INSTALLER_NOT_COMPLETED"
        command = ("KEENETIC_AUTO_SETUP_REF=" + kas_ref +
                   " PATH=/opt/bin:/opt/sbin:/bin:/sbin:/usr/bin:/usr/sbin" +
                   " sh /opt/lab/install.sh disk")
        exit_code, stdout, stderr = guest.execute(command, timeout=300)
        category = classify_failure(exit_code, stdout + "\n" + stderr)
        if category != "INSTALLER_COMPLETED":
            reason = category
            print("KAS MIPSEL INSTALL: " + category, flush=True)
            if guarded_refusal_expected:
                package_list = guest.command("/opt/bin/opkg list-installed", timeout=45)
                no_owner = (guest.command("test ! -e /opt/etc/keenetic-auto-setup/ownership.v1 && "
                                          "printf '%s\\n' LAB_GUARD_UNTOUCHED",
                                          timeout=30).strip() == "LAB_GUARD_UNTOUCHED")
                guard_verified = guarded_refusal_proven(
                    exit_code, category, component_gap, package_list, no_owner)
                if guard_verified:
                    print("KAS GUARDED REFUSAL: PASS (KAS not installed, no managed state)",
                          flush=True)
                    return
            raise RuntimeError("guest KAS install not accepted: " + category)
        if guarded_refusal_expected:
            reason = "UNEXPECTED_INSTALLER_SUCCESS_IN_GUARD_TEST"
            raise RuntimeError("guard expected but KAS installer succeeded")
        installed = guest.command("/opt/bin/opkg list-installed", timeout=45)
        if not all(name + " - " in installed for name in ("mihomo", "magitrickle")):
            reason = "INSTALLED_PACKAGES_MISSING"
            raise RuntimeError("KAS package postcondition failed")
        phase, reason = "PASS", "PINNED_INSTALLER_AND_PACKAGE_CONTRACT"
        print("KAS MIPSEL INSTALL: PASS (actual installed packages)", flush=True)
    except TimeoutError:
        reason = "GUEST_PROBE_TIMEOUT"
        raise RuntimeError("KAS guest timed out") from None
    finally:
        guest.close()
        write_public(public, kas_ref, phase, reason, exit_code,
                     component_gap=component_gap, guard_verified=guard_verified,
                     guarded_refusal_expected=guarded_refusal_expected,
                     lab_exception_applied=exception_applied)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--emulator-root", required=True)
    ap.add_argument("--nand-dir", required=True)
    ap.add_argument("--workspace", required=True, help="MUST be a fresh, private directory")
    ap.add_argument("--credentials-file", required=True)
    ap.add_argument("--kas-dir", required=True, help="immutable pinned public KAS checkout")
    ap.add_argument("--kas-ref", required=True)
    ap.add_argument("--max-seconds", type=int, default=2400)
    ap.add_argument("--expect-guarded-refusal", action="store_true",
                    help="negative test ONLY: exact ext-utils refusal, no KAS packages/state")
    ap.add_argument("--lab-ext-utils-exception", action="store_true",
                    help="ONLY disposable Ported MT7621 QEMU: skip one KAS ext-utils guard")
    args = ap.parse_args()
    if args.expect_guarded_refusal and args.lab_ext_utils_exception:
        ap.error("lab exception and guarded refusal are mutually exclusive")
    if not PIN.fullmatch(args.kas_ref):
        ap.error("--kas-ref must be full 40-character SHA")
    source = load_sources(args.kas_dir, args.kas_ref)["install.sh"]
    if not source.startswith(b"#!/bin/sh"):
        ap.error("KAS installer header mismatch")
    workspace = Path(args.workspace)
    workspace.mkdir(mode=0o700, exist_ok=False)
    creds = json.loads(Path(args.credentials_file).read_text(encoding="utf-8"))
    if creds.get("username") != "root":
        ap.error("expected isolated root credential")
    image, feed = workspace / "opt.img", workspace / "mirror"
    build(image, feed, workspace / "cache", args.credentials_file,
          arch="mipsel", lock_path=LOCK)
    scenario = Path(__file__).resolve().parents[1] / "scenarios" / "ported-mt7621-entware-tz-skip.json"
    public = workspace / "report" / "kas-smoke.json"
    with Mirror(feed) as port:
        return run(SimpleNamespace(
            scenario=scenario, emulator_root=args.emulator_root,
            nand_dir=args.nand_dir, output_dir=workspace / "report",
            max_seconds=args.max_seconds, preset="xiaomi-mi-router-3g-breed",
            lan_forwards="8080:80;8022:222;8023:23", opt_image=str(image),
            archive_image=None, mirror_port=port,
            credentials_file=args.credentials_file,
            after_scenario=lambda **ctx: guest_probe(args.credentials_file,
                workspace, ctx["deadline"], source, args.kas_ref, public,
                guarded_refusal_expected=args.expect_guarded_refusal,
                lab_ext_utils_exception=args.lab_ext_utils_exception)))


if __name__ == "__main__":
    raise SystemExit(main())

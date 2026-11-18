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
import calendar
import hashlib
import json
import time
from contextlib import ExitStack
from pathlib import Path
import re
from types import SimpleNamespace

from lab.build_entware import add_packages, build
from lab.mt7621.kas_base_deps import (
    OVERLAY_LOCK, REQUIRED_BASE, validated_kas_base_overlay,
)
from lab.mt7621.kas_magitrickle_deps import (
    MAGITRICKLE_VERSION, SOCAT_VERSION, add_magitrickle_packages,
    validated_kas_magitrickle_overlay,
)
from lab.mt7621.kas_mihomo_deps import (
    MIHOMO_LOCK, MIHOMO_VERSION, validated_kas_mihomo_overlay,
)
from lab.mt7621.kas_mipsel_https import (
    HELPER_SHA256, validated_kas_mipsel_https_mirror,
)
from lab.mirror import Mirror
from lab.run import run
from lab.kas_mirror import load_sources

LOCK = Path(__file__).resolve().parents[1] / "entware-mipselsf.lock.json"
PIN = re.compile(r"^[0-9a-f]{40}$")


# Read-only post-install evidence. Never upload raw config, logs or process lists.
# Command outputs are restricted to finite categories before publication.
SERVICE_PROBES = {
    "mihomo_init": (
        "if [ -L /opt/etc/init.d/S28mihomo ] || [ -L /opt/etc/init.d/S99mihomo ]; "
        "then echo invalid; "
        "elif [ -x /opt/etc/init.d/S28mihomo ] && [ -x /opt/etc/init.d/S99mihomo ]; "
        "then echo ambiguous; "
        "elif [ -x /opt/etc/init.d/S28mihomo ]; then echo s28; "
        "elif [ -x /opt/etc/init.d/S99mihomo ]; then echo s99; "
        "elif [ -e /opt/etc/init.d/S28mihomo ] || [ -e /opt/etc/init.d/S99mihomo ]; "
        "then echo invalid; else echo absent; fi",
        ("s28", "s99", "ambiguous", "invalid", "absent")),
    "mihomo_config": (
        "if [ -L /opt/etc/mihomo/config.yaml ]; then echo symlink; "
        "elif [ -s /opt/etc/mihomo/config.yaml ]; then echo present; "
        "elif [ -e /opt/etc/mihomo/config.yaml ]; then echo empty; "
        "else echo missing; fi",
        ("symlink", "present", "empty", "missing")),
    "mihomo_process": (
        "if ! command -v pidof >/dev/null 2>&1; then echo unknown; "
        "elif pidof mihomo >/dev/null 2>&1; then echo running; else echo stopped; fi",
        ("running", "stopped")),
    "mihomo_port_7890": (
        "if ! command -v netstat >/dev/null 2>&1; then echo unknown; "
        "else n=$(netstat -tln 2>/dev/null) || { echo unknown; exit 0; }; "
        "if printf '%s\n' \"$n\" | "
        "grep -Eq ':7890[[:space:]].*[[:space:]]LISTEN([[:space:]]|$)'; "
        "then echo listening; else echo not_listening; fi; fi",
        ("listening", "not_listening")),
    "magitrickle_uptime": (
        "u=$(cut -d. -f1 /proc/uptime 2>/dev/null); "
        "case \"$u\" in ''|*[!0-9]*) echo unknown ;; "
        "*) if [ \"$u\" -lt 300 ]; then echo under_300; "
        "else echo at_least_300; fi ;; esac",
        ("under_300", "at_least_300")),
    "magitrickle_process": (
        "if ! command -v pidof >/dev/null 2>&1; then echo unknown; "
        "elif pidof magitrickled >/dev/null 2>&1; then echo running; "
        "else echo stopped; fi",
        ("running", "stopped")),
    "tun_device": (
        "if [ -c /dev/net/tun ]; then echo present; else echo missing; fi",
        ("present", "missing")),
    "memory_available": (
        "m=$(awk '$1==\"MemAvailable:\" {print $2; exit}' "
        "/proc/meminfo 2>/dev/null); "
        "case \"$m\" in ''|*[!0-9]*) echo unknown ;; "
        "*) if [ \"$m\" -lt 16384 ]; then echo under_16m; "
        "elif [ \"$m\" -lt 65536 ]; then echo under_64m; "
        "else echo at_least_64m; fi ;; esac",
        ("under_16m", "under_64m", "at_least_64m")),
    "kernel_oom_evidence": (
        "if ! command -v dmesg >/dev/null 2>&1; then echo unknown; "
        "else k=$(dmesg 2>/dev/null) || { echo unknown; exit 0; }; "
        "if printf '%s\\n' \"$k\" | grep -Eiq "
        "'(out of memory|killed process.*mihomo|oom-killer)'; "
        "then echo observed; else echo not_observed; fi; fi",
        ("observed", "not_observed")),
}


def sanitize_service_diagnostics(observations):
    """Fail closed: public JSON contains only known keys and fixed values."""
    observations = observations if isinstance(observations, dict) else {}
    return {name: (observations.get(name) if observations.get(name) in allowed
                   else "unknown")
            for name, (_command, allowed) in SERVICE_PROBES.items()}


def read_only_service_diagnostics(guest):
    """One bounded snapshot after the real KAS attempt, without restarts."""
    observed = {}
    for name, (command, _allowed) in SERVICE_PROBES.items():
        try:
            observed[name] = guest.command(command, timeout=20).strip()
        except (RuntimeError, TimeoutError):
            observed[name] = "unknown"
    return sanitize_service_diagnostics(observed)


def two_phase_service_diagnostics(guest):
    """Observe before any post-install ELF probing, then after five seconds.

    Both snapshots are read-only: no extra Mihomo launch or service start.
    An unavailable delayed probe is UNKNOWN, never inferred to be stopped.
    """
    initial = read_only_service_diagnostics(guest)
    try:
        guest.command("sleep 5", timeout=15)
    except (RuntimeError, TimeoutError):
        return initial, sanitize_service_diagnostics(None)
    return initial, read_only_service_diagnostics(guest)


def may_probe_mihomo_version(early, delayed):
    """Fail closed before executing a second memory-heavy Go process.

    Only two *observed* stopped states and two non-listening sockets justify
    another Mihomo invocation. Missing probes are UNKNOWN, not permission.
    Not a claim that the service itself is healthy or KAS installed.
    """
    first = sanitize_service_diagnostics(early)
    last = sanitize_service_diagnostics(delayed)
    return (first["mihomo_process"] == last["mihomo_process"] == "stopped"
            and first["mihomo_port_7890"] == last["mihomo_port_7890"]
            == "not_listening")


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



# KAS calls the init script with "start" or "restart" and turns nonzero
# return codes into one of these constant WARN messages. Report this
# classification instead of uploading the full private installer output.
# "not_observed" is not proof that the daemon started successfully.
MIHOMO_INIT_MARKERS = ("not_observed", "start_failed", "restart_failed")


def mihomo_init_marker(output):
    if not isinstance(output, str):
        return "not_observed"
    if "Mihomo restart failed" in output:
        return "restart_failed"
    if "Mihomo start failed" in output:
        return "start_failed"
    return "not_observed"


ERR_LITERAL = re.compile(r'\berr\s+"((?:[^"\\]|\\.)*)"')


def matched_installer_error(output, source):
    """Identify the pinned KAS err() message that fired, from its own source.

    err() exits the installer immediately, so guest output holds at most one
    [ERROR] message. Only constant prefixes extracted from the pinned
    installer bytes are ever returned (variable tails are dropped); raw guest
    output is never published. Longest constant prefix wins so a short
    literal can never shadow the exact one that fired.
    """
    if not isinstance(output, str):
        return None
    low = output.lower()
    best = None
    for literal in ERR_LITERAL.findall(source.decode("utf-8", "replace")):
        prefix = literal.split("$", 1)[0].strip()
        if len(prefix) < 12:
            continue
        if prefix.lower() in low and (best is None or len(prefix) > len(best)):
            best = prefix
    return best


FINAL_ERR_CATEGORIES = (
    # err() literals from the pinned installer mapped to the operation that
    # actually terminated it. matched_installer_error() returns the longest
    # constant prefix, so the final err literal always wins over the earlier
    # WARN lines (e.g. the GitHub Mihomo warning printed before a successful
    # Entware fallback install). All categories are fixed enum values.
    ("Mihomo install failed: GitHub package unavailable and Entware feed fallback failed",
     "MIHOMO_INSTALL_FAILED"),
    ("Mihomo package installation completed but no executable canonical binary was found",
     "MIHOMO_BINARY_MISSING"),
    ("Failed to download MagiTrickle repository helper through curl/wget",
     "MAGITRICKLE_HELPER_DOWNLOAD_FAILED"),
    ("Downloaded MagiTrickle repository helper failed shell syntax validation",
     "MAGITRICKLE_HELPER_SYNTAX_FAILED"),
    ("Failed to add MagiTrickle package repository",
     "MAGITRICKLE_HELPER_EXEC_FAILED"),
    ("opkg update after changing the MagiTrickle repository failed",
     "MAGITRICKLE_FEED_UPDATE_FAILED"),
    ("MagiTrickle package is not present after installation",
     "MAGITRICKLE_INSTALL_FAILED"),
    ("MagiTrickle init script missing after installation",
     "MAGITRICKLE_INSTALL_FAILED"),
    ("MagiTrickle start failed",
     "MAGITRICKLE_START_FAILED"),
    # CI run 44162737407: the pinned installer aborts here after its own
    # init-script start fails the pidof-based liveness confirmation; the
    # earlier GitHub-mihomo WARN must not classify this as IPK_UNAVAILABLE.
    ("Mihomo start/restart could not be confirmed.",
     "MIHOMO_START_NOT_CONFIRMED"),
)


def classify_installer_failure(code, output, err_prefix):
    """Classify by the terminating pinned err(), not by an earlier WARN.

    Legacy heuristic categories remain the fallback when no pinned err
    literal matched, so existing behaviors (guarded refusal, feed failure)
    are preserved unchanged.
    """
    if code == 0 and "[OK] Done" in output:
        return "INSTALLER_COMPLETED"
    if err_prefix == "Failed to install":
        # pkg_ensure emits "Failed to install $_pkg"; only the fixed KAS
        # package names may complete the pinned literal in guest output.
        if re.search(r"\[ERROR\] Failed to install magitrickle\b", output):
            return "MAGITRICKLE_INSTALL_FAILED"
        if re.search(r"\[ERROR\] Failed to install mihomo\b", output):
            return "MIHOMO_INSTALL_FAILED"
        return "IPK_UNAVAILABLE"
    for prefix, category in FINAL_ERR_CATEGORIES:
        if err_prefix == prefix:
            return category
    fallback = classify_failure(code, output)
    # A historical GitHub-download WARN mentions "package unavailable" even
    # when opkg later installed Mihomo successfully. Do not turn a nonzero
    # *later* installer exit into IPK_UNAVAILABLE unless a real terminating
    # [ERROR] line independently establishes a package-install failure.
    # This protects the guest reports when stdout was truncated or an
    # unrecognized shell termination occurred (run 44353215646).
    if fallback == "IPK_UNAVAILABLE" and not re.search(
            r"(?im)^\[ERROR\]\s+(?:Failed to install\b|"
            r"Mihomo install failed\b|MagiTrickle package is not present\b)",
            output):
        return "INSTALLER_EXIT_NONZERO"
    return fallback


MIHOMO_OUTPUT_MARKERS = (
    # Genuine version banner (printed on the -v success path).
    "Mihomo Meta",
    # mihomo main() resolver tripwire: "panic: should never be called" plus a
    # goroutine dump, then os.Exit(2); fires whenever DefaultResolver is used.
    "panic: should never be called",
    # Go flag package parse failure (ExitOnError -> os.Exit(2)).
    "flag provided but not defined",
    # Go runtime allocation/reservation fatals (exit 2).
    "runtime: out of memory",
    "fatal error:",
)


def mihomo_output_marker(stdout, stderr):
    """Match guest output against fixed mihomo/Go constants; never raw text.

    Same policy as matched_installer_error: only allowlisted constants from
    the pinned upstream source may be published, in fixed priority order.
    """
    if not isinstance(stdout, str) or not isinstance(stderr, str):
        return None
    text = stdout + "\n" + stderr
    for marker in MIHOMO_OUTPUT_MARKERS:
        if marker in text:
            return marker
    return None


def mihomo_output_categories(stdout, stderr):
    """Fixed booleans about Mihomo -v output; never the raw guest text."""
    if not isinstance(stdout, str) or not isinstance(stderr, str):
        return (False, False)
    output = stdout + "\n" + stderr
    return (re.search(r"\bmihomo\b", output, re.I) is not None,
            re.search(r"(?<![0-9])v?1\.19\.33(?![0-9])", output) is not None)


MIHOMO_HEAD_MARKERS = (
    # Success banner head (the -v fast path).
    "Mihomo Meta",
    # mihomo resolver tripwire panic (its specific message first).
    "panic: should never be called",
    # Go flag parse failure.
    "flag provided but not defined",
    # Generic Go panic header.
    "panic: ",
    # Go runtime throw header.
    "fatal error: ",
    # Go runtime message (e.g. "runtime: out of memory").
    "runtime: ",
    # Go futexwakeup failure path (prints before its deliberate fault).
    "futexwakeup addr=",
    # Bare goroutine dump: the crash header was not in the captured head.
    "goroutine ",
)
MIHOMO_HEAD_MARKER_VALUES = tuple(marker.rstrip() for marker in MIHOMO_HEAD_MARKERS)


def mihomo_head_marker(head_text):
    """Classify the first captured bytes against pinned constants only.

    The SSH rings keep only the tail of long output, so the crash header is
    read from a bounded head capture instead. The raw head never leaves the
    private log; only a fixed enum constant is published.
    """
    if not isinstance(head_text, str):
        return None
    for marker in MIHOMO_HEAD_MARKERS:
        if marker in head_text:
            return marker.rstrip()
    return None


def verified_mihomo_version(exit_code, stdout, stderr):
    """Only genuine native binary -v success for the pinned Mihomo version."""
    if exit_code != 0:
        return False
    has_name, has_version = mihomo_output_categories(stdout, stderr)
    return has_name and has_version


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
                 lab_exception_applied=False, guest_source_verified=False,
                 kas_base_deps_feed=False, installed_base_packages=None,
                 kas_mihomo_feed=False, mihomo_package_installed=False,
                 mihomo_binary_version_verified=False,
                 installer_error_prefix=None, mihomo_init_result="not_observed",
                 mihomo_binary_path="none",
                 mihomo_probe_exit_code=None, mihomo_output_has_name=False,
                 mihomo_output_has_version=False, mihomo_output_marker=None,
                 mihomo_version_probe_policy="not_reached",
                 kas_magitrickle_feed=False,
                 socat_package_installed=False,
                 magitrickle_package_installed=False,
                 magitrickle_daemon_running=False,
                 magitrickle_init_script_present=False,
                 mihomo_output_head_marker=None,
                 mihomo_stdout_size=None, mihomo_stderr_size=None,
                 kas_https_stage=False, kas_https_dns_configured=False,
                 kas_https_clock_synced=False,
                 kas_https_ca_trust_applied=False,
                 kas_https_helper_tls_verified=False,
                 service_diagnostics=None, service_diagnostics_delayed=None):
    """No guest output, passwords, paths, script bodies, or arbitrary diagnostics.

    installer_error_prefix is a constant prefix extracted from the pinned KAS
    source itself; mihomo_output_head_marker is a constant from the pinned
    upstream output enum; every other field is a boolean, a fixed path bucket
    or an integer exit code / byte count observed from allowlisted probes.
    """
    record = {"schema": "mt7621-kas-smoke-v3", "kas_ref": kas_ref,
              "phase": phase, "reason": reason, "kas_installed": phase == "PASS",
              "exit_code": exit_code if isinstance(exit_code, int) else None,
              "component_inventory_seen": component_gap is not None,
              "missing_component_ids": component_gap if component_gap is not None else [],
              "test_expectation": ("guarded_refusal" if guarded_refusal_expected
                                   else "full_install"),
              "guard_contract_verified": guard_verified,
              "lab_ext_utils_exception_applied": bool(lab_exception_applied),
              "guest_source_verified": bool(guest_source_verified),
              "kas_base_deps_feed": bool(kas_base_deps_feed),
              "base_packages_installed": [x for x in REQUIRED_BASE
                                          if x in (installed_base_packages or ())],
              "kas_mihomo_feed": bool(kas_mihomo_feed),
              "mihomo_package_installed": bool(mihomo_package_installed),
              "mihomo_binary_version_verified": bool(mihomo_binary_version_verified),
              "mihomo_init_result": (mihomo_init_result
                                      if mihomo_init_result in MIHOMO_INIT_MARKERS
                                      else "not_observed"),
              "installer_error_prefix": (installer_error_prefix[:120]
                                         if isinstance(installer_error_prefix, str)
                                         else None),
              "mihomo_binary_path": (mihomo_binary_path
                                     if mihomo_binary_path in ("none", "opt-sbin",
                                                               "opt-bin")
                                     else "none"),
              "mihomo_probe_exit_code": (mihomo_probe_exit_code
                                         if isinstance(mihomo_probe_exit_code, int)
                                         else None),
              "mihomo_version_probe_policy": (
                  mihomo_version_probe_policy if mihomo_version_probe_policy
                  in ("not_reached", "legacy_probe", "safe_to_probe",
                      "skipped_runtime_possible") else "not_reached"),
              "mihomo_output_has_name": bool(mihomo_output_has_name),
              "mihomo_output_has_version": bool(mihomo_output_has_version),
              "mihomo_output_marker": (mihomo_output_marker
                                       if mihomo_output_marker in MIHOMO_OUTPUT_MARKERS
                                       else None),
              "kas_magitrickle_feed": bool(kas_magitrickle_feed),
              "socat_package_installed": bool(socat_package_installed),
              "magitrickle_package_installed": bool(magitrickle_package_installed),
              "magitrickle_daemon_running": bool(magitrickle_daemon_running),
              "magitrickle_init_script_present": bool(magitrickle_init_script_present),
              "mihomo_output_head_marker": (mihomo_output_head_marker
                                            if mihomo_output_head_marker in
                                            MIHOMO_HEAD_MARKER_VALUES else None),
              "mihomo_stdout_size": (mihomo_stdout_size
                                     if isinstance(mihomo_stdout_size, int) else None),
              "mihomo_stderr_size": (mihomo_stderr_size
                                     if isinstance(mihomo_stderr_size, int) else None),
              "kas_https_stage": bool(kas_https_stage),
              "kas_https_dns_configured": bool(kas_https_dns_configured),
              "kas_https_clock_synced": bool(kas_https_clock_synced),
              "kas_https_ca_trust_applied": bool(kas_https_ca_trust_applied),
              "kas_https_helper_tls_verified": bool(kas_https_helper_tls_verified),
              "service_diagnostics": sanitize_service_diagnostics(service_diagnostics),
              "service_diagnostics_delayed": sanitize_service_diagnostics(
                  service_diagnostics_delayed),
              "source_variant": ("DISPOSABLE_LAB_EXT_UTILS_EXCEPTION"
                                 if lab_exception_applied else "PINNED_UNMODIFIED")}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, sort_keys=True, indent=2) + "\n",
                    encoding="utf-8")


def guest_probe(credentials, workspace, deadline, source, kas_ref, public,
                guarded_refusal_expected=False, lab_ext_utils_exception=False,
                kas_base_deps_feed=False, kas_mihomo_feed=False,
                kas_magitrickle_feed=False, kas_https_mirror=False,
                https_ca=None):
    """Trusted run.py post-scenario hook; VM remains alive for the whole probe."""
    from lab.guest import Guest  # lazy: unit tests do not require Paramiko
    guest = Guest(credentials, workspace, deadline=deadline)
    phase, reason, exit_code = "NOT_TESTED", "GUEST_NOT_REACHED", None
    component_gap = None
    guard_verified = False
    exception_applied = False
    guest_source_verified = False
    installed_base_packages = []
    mihomo_package_installed = False
    mihomo_binary_version_verified = False
    installer_error_prefix = None
    mihomo_binary_path = "none"
    mihomo_probe_exit_code = None
    mihomo_version_probe_policy = "not_reached"
    mihomo_init_result = "not_observed"
    mihomo_output_has_name = False
    mihomo_output_has_version = False
    mihomo_output_marker_value = None
    socat_package_installed = False
    magitrickle_package_installed = False
    magitrickle_daemon_running = False
    magitrickle_init_script_present = False
    mihomo_output_head_marker_value = None
    mihomo_stdout_size = None
    mihomo_stderr_size = None
    kas_https_dns_configured = False
    kas_https_clock_synced = False
    kas_https_ca_trust_applied = False
    kas_https_helper_tls_verified = False
    service_diagnostics = None
    service_diagnostics_delayed = None
    if kas_magitrickle_feed and not kas_mihomo_feed:
        raise ValueError("MagiTrickle IPK lab overlay requires the Mihomo feed")
    if kas_mihomo_feed and not kas_base_deps_feed:
        raise ValueError('Mihomo IPK lab overlay requires KAS base feed')
    if kas_base_deps_feed and not lab_ext_utils_exception:
        raise ValueError('KAS base extra packages only allowed with lab ext-utils exception')
    if guarded_refusal_expected and lab_ext_utils_exception:
        raise ValueError("lab exception is incompatible with guarded-refusal test")
    if kas_https_mirror and not kas_magitrickle_feed:
        raise ValueError("HTTPS mirror stage requires the MagiTrickle feed")
    if kas_https_mirror and (not isinstance(https_ca, bytes)
                             or not https_ca.startswith(b"-----BEGIN CERTIFICATE-----")):
        raise ValueError("HTTPS mirror stage requires the disposable lab CA certificate")
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
        guest_source_verified = True
        print("KAS MIPSEL SOURCE: pinned checkout transferred and verified", flush=True)

        if kas_https_mirror:
            # Disposable-guest trust setup for the REAL KAS HTTPS stage. The
            # pinned feed delivers the same ca-bundle and curl the installer
            # itself installs, the disposable lab CA is appended to the
            # guest's bundle (TLS verification stays fully ON: no -k, no
            # insecure flag, no KAS source change), and bin.magitrickle.dev
            # resolves only through the lab loopback mapping.
            phase, reason = "BLOCKED", "HTTPS_STAGE_SETUP_FAILED"
            guest.command('/bin/ndmc -c "service dns-proxy"', timeout=30)
            guest.command('/bin/ndmc -c "ip host bin.magitrickle.dev 192.168.1.100"',
                          timeout=30)
            config = guest.command('/bin/ndmc -c "show running-config"', timeout=45)
            kas_https_dns_configured = re.search(
                r"(?m)^ip host bin\.magitrickle\.dev 192\.168\.1\.100$",
                config) is not None
            guest.command("/opt/bin/opkg update", timeout=180)
            guest.command("/opt/bin/opkg install ca-bundle curl", timeout=240)
            stamp = guest.command(
                "curl -fSs --connect-timeout 2 --max-time 5 "
                "http://192.168.1.100/lab/clock", timeout=30).strip()
            if re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", stamp):
                guest.command("date -s '" + stamp + "'", timeout=30)
                epoch_out = guest.command("date -u +%s", timeout=30).strip()
                if epoch_out.isdigit():
                    expected = calendar.timegm(time.strptime(
                        stamp, "%Y-%m-%d %H:%M:%S"))
                    kas_https_clock_synced = abs(int(epoch_out) - expected) <= 5
            guest.command("umask 077; cat > /opt/lab/ca.pem; chmod 600 /opt/lab/ca.pem",
                          timeout=60, input_data=https_ca)
            ca_digest = guest.command("/opt/bin/busybox sha256sum /opt/lab/ca.pem",
                                      timeout=30).split()[0]
            if ca_digest != hashlib.sha256(https_ca).hexdigest():
                raise RuntimeError("lab CA transfer digest mismatch")
            bundle = "/opt/etc/ssl/certs/ca-certificates.crt"
            bundle_size = int(guest.command("wc -c < " + bundle, timeout=30))
            guest.command("cat /opt/lab/ca.pem >> " + bundle, timeout=30)
            kas_https_ca_trust_applied = int(guest.command(
                "wc -c < " + bundle, timeout=30)) == bundle_size + len(https_ca)
            helper_digest = guest.command(
                "curl -fSs --max-time 20 "
                "https://bin.magitrickle.dev/packages/add_repo.sh | sha256sum",
                timeout=60).split()[0]
            kas_https_helper_tls_verified = helper_digest == HELPER_SHA256
            print("KAS MIPSEL HTTPS STAGE: dns=%s clock=%s ca=%s tls=%s" %
                  (kas_https_dns_configured, kas_https_clock_synced,
                   kas_https_ca_trust_applied, kas_https_helper_tls_verified),
                  flush=True)

        # A real KAS installation attempt. No host-side mock or fake PASS.
        # The guest has an offline WAN and a pinned 25-IPK local feed. Current
        # Ported images may not have all production KeeneticOS components.
        phase, reason = "BLOCKED", "INSTALLER_NOT_COMPLETED"
        command = ("KEENETIC_AUTO_SETUP_REF=" + kas_ref +
                   " PATH=/opt/bin:/opt/sbin:/bin:/sbin:/usr/bin:/usr/sbin" +
                   " sh /opt/lab/install.sh disk")
        exit_code, stdout, stderr = guest.execute(command, timeout=300)
        combined = stdout + "\n" + stderr
        mihomo_init_result = mihomo_init_marker(combined)
        installer_error_prefix = matched_installer_error(combined, source)
        category = classify_installer_failure(exit_code, combined,
                                              installer_error_prefix)
        if kas_https_mirror:
            # Capture true immediate post-install state BEFORE launching
            # another Mihomo -v process or waiting for MagiTrickle.
            service_diagnostics, service_diagnostics_delayed = (
                two_phase_service_diagnostics(guest))
            for label, snapshot in (("early", service_diagnostics),
                                    ("delayed", service_diagnostics_delayed)):
                print("KAS MIPSEL SERVICE SNAPSHOT %s: " % label + " ".join(
                    "%s=%s" % (key, value)
                    for key, value in snapshot.items()), flush=True)
        if installer_error_prefix:
            print("KAS INSTALLER ERR PREFIX MATCHED (pinned source constant)",
                  flush=True)
        if kas_base_deps_feed:
            # Query actual guest package database, never trust the mirror index
            # as evidence that KAS installed a package. Publish allowlisted IDs.
            installed_now = guest.command("/opt/bin/opkg list-installed", timeout=45)
            installed_base_packages = [x for x in REQUIRED_BASE
                if re.search(r"^" + re.escape(x) + r" - ", installed_now, re.M)]
            print("KAS MIPSEL BASE PACKAGES INSTALLED: %d/%d" %
                  (len(installed_base_packages), len(REQUIRED_BASE)), flush=True)
            if kas_mihomo_feed:
                mihomo_package_installed = bool(re.search(
                    r"^mihomo - " + re.escape(MIHOMO_VERSION) + r"$",
                    installed_now, re.M))
                # Probe the REAL installed MIPSel ELF; opkg metadata alone is
                # insufficient evidence that it executes under native NDMS.
                # Publish only fixed booleans, a path bucket and the exit code;
                # raw guest output stays private.
                if mihomo_package_installed:
                    path_out = guest.command(
                        "for p in /opt/sbin/mihomo /opt/bin/mihomo; do "
                        "if [ -x \"$p\" ]; then echo \"$p\"; break; fi; done",
                        timeout=30).strip()
                    mihomo_binary_path = (
                        "opt-sbin" if "/opt/sbin/mihomo" in path_out
                        else "opt-bin" if "/opt/bin/mihomo" in path_out
                        else "none")
                    if mihomo_binary_path != "none":
                        # In the full HTTPS guest, the real installer just
                        # started Mihomo. Do not start a SECOND Go process if
                        # either snapshot saw it running or was inconclusive.
                        # The historical non-HTTPS diagnostic keeps its
                        # independent version proof and original behavior.
                        allow_version_probe = (
                            not kas_https_mirror or may_probe_mihomo_version(
                                service_diagnostics, service_diagnostics_delayed))
                        mihomo_version_probe_policy = (
                            "legacy_probe" if not kas_https_mirror
                            else "safe_to_probe" if allow_version_probe
                            else "skipped_runtime_possible")
                        if allow_version_probe:
                            binary = path_out.splitlines()[0].strip()
                            # Full -v capture lives in disposable guest tmpfs:
                            # SSH rings keep only the tail (64 KiB stderr) and
                            # can discard a crash header. Publish fixed
                            # categories only; full output remains private.
                            rc_out = guest.command(
                                "rm -f /tmp/.mihomo-v.out /tmp/.mihomo-v.err; "
                                + binary + " -v >/tmp/.mihomo-v.out "
                                "2>/tmp/.mihomo-v.err; printf 'rc=%s\\n' \"$?\"",
                                timeout=90).strip()
                            match = re.fullmatch(r"rc=(\d+)", rc_out)
                            if match:
                                mihomo_probe_exit_code = int(match.group(1))
                            sizes = [row.strip() for row in guest.command(
                                "wc -c < /tmp/.mihomo-v.out; wc -c < /tmp/.mihomo-v.err",
                                timeout=30).splitlines()]
                            if len(sizes) == 2 and all(row.isdigit() for row in sizes):
                                mihomo_stdout_size = int(sizes[0])
                                mihomo_stderr_size = int(sizes[1])
                            head_out = guest.command(
                                "cat /tmp/.mihomo-v.out /tmp/.mihomo-v.err "
                                "2>/dev/null | head -c 240", timeout=30)
                            mihomo_output_head_marker_value = mihomo_head_marker(
                                head_out)
                            mihomo_output_marker_value = mihomo_output_marker(
                                head_out, "")
                            mihomo_output_has_name = "NAME_OK" in guest.command(
                                "grep -iq mihomo /tmp/.mihomo-v.out /tmp/.mihomo-v.err "
                                "2>/dev/null && printf NAME_OK", timeout=30)
                            mihomo_output_has_version = "VER_OK" in guest.command(
                                "grep -Fq '1.19.33' /tmp/.mihomo-v.out /tmp/.mihomo-v.err "
                                "2>/dev/null && printf VER_OK", timeout=30)
                            mihomo_binary_version_verified = (
                                mihomo_probe_exit_code == 0
                                and mihomo_output_has_name
                                and mihomo_output_has_version)
                            guest.command(
                                "rm -f /tmp/.mihomo-v.out /tmp/.mihomo-v.err",
                                timeout=30)
                        else:
                            print("KAS MIPSEL MIHOMO VERSION PROBE: SKIPPED "
                                  "(service running or liveness uncertain)",
                                  flush=True)
                print("KAS MIPSEL MIHOMO: opkg=%s binary_version=%s probe_rc=%s "
                      "path=%s name=%s version=%s marker=%s" %
                      (mihomo_package_installed, mihomo_binary_version_verified,
                       mihomo_probe_exit_code, mihomo_binary_path,
                       mihomo_output_has_name, mihomo_output_has_version,
                       mihomo_output_marker_value),
                      flush=True)
            if kas_magitrickle_feed and not kas_https_mirror:
                # Lab-direct pinned-dependency probe. The real installer's
                # MagiTrickle stage downloads an HTTPS helper this offline
                # guest cannot reach; installing the two SHA256-pinned IPKs
                # directly proves the NEXT KAS dependency executes natively.
                # This is NOT KAS installer acceptance and never a KAS PASS.
                install_rc, _install_out, _install_err = guest.execute(
                    "/opt/bin/opkg install socat; "
                    "/opt/bin/opkg install magitrickle", timeout=240)
                print("KAS MIPSEL MAGITRICKLE LAB-DIRECT: opkg install rc=%s" %
                      install_rc, flush=True)
            elif kas_magitrickle_feed:
                # HTTPS mode: the real installer runs its own verified
                # MagiTrickle stage (pinned helper download, feed add, opkg
                # install, init start); the checks below observe its actual
                # outcomes only.
                print("KAS MIPSEL MAGITRICKLE VIA KAS: real installer stage",
                      flush=True)
            if kas_magitrickle_feed:
                installed_now = guest.command("/opt/bin/opkg list-installed",
                                              timeout=45)
                socat_package_installed = bool(re.search(
                    r"^socat - " + re.escape(SOCAT_VERSION) + r"$",
                    installed_now, re.M))
                magitrickle_package_installed = bool(re.search(
                    r"^magitrickle - " + re.escape(MAGITRICKLE_VERSION) + r"$",
                    installed_now, re.M))
                print("KAS MIPSEL MAGITRICKLE STATE: socat=%s magitrickle=%s" %
                      (socat_package_installed, magitrickle_package_installed),
                      flush=True)
                if magitrickle_package_installed:
                    init_out = guest.command(
                        "test -x /opt/etc/init.d/S99magitrickle && "
                        "printf '%s\\n' MT_INIT_OK", timeout=30)
                    magitrickle_init_script_present = "MT_INIT_OK" in init_out
                    # The package postinst starts S99magitrickle; a live pidof
                    # proves the UPX-packed MIPSel ELF decompresses and runs
                    # natively. Retry briefly: the init script defers startup
                    # in a background loop until guest uptime exceeds 300s.
                    for _attempt in range(3):
                        pid_out = guest.command(
                            "pidof magitrickled || true", timeout=30)
                        magitrickle_daemon_running = bool(pid_out.strip())
                        if magitrickle_daemon_running:
                            break
                        guest.command("sleep 5", timeout=30)
                    print("KAS MIPSEL MAGITRICKLE RUNTIME: daemon=%s init=%s" %
                          (magitrickle_daemon_running,
                           magitrickle_init_script_present), flush=True)
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
        installed = (installed_now if kas_base_deps_feed else
                     guest.command("/opt/bin/opkg list-installed", timeout=45))
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
                     lab_exception_applied=exception_applied,
                     guest_source_verified=guest_source_verified,
                     kas_base_deps_feed=kas_base_deps_feed,
                     installed_base_packages=installed_base_packages,
                     kas_mihomo_feed=kas_mihomo_feed,
                     mihomo_package_installed=mihomo_package_installed,
                     mihomo_binary_version_verified=mihomo_binary_version_verified,
                     installer_error_prefix=installer_error_prefix,
                     mihomo_init_result=mihomo_init_result,
                     mihomo_binary_path=mihomo_binary_path,
                     mihomo_probe_exit_code=mihomo_probe_exit_code,
                     mihomo_version_probe_policy=mihomo_version_probe_policy,
                     mihomo_output_has_name=mihomo_output_has_name,
                     mihomo_output_has_version=mihomo_output_has_version,
                     mihomo_output_marker=mihomo_output_marker_value,
                     kas_magitrickle_feed=kas_magitrickle_feed,
                     socat_package_installed=socat_package_installed,
                     magitrickle_package_installed=magitrickle_package_installed,
                     magitrickle_daemon_running=magitrickle_daemon_running,
                     magitrickle_init_script_present=magitrickle_init_script_present,
                     mihomo_output_head_marker=mihomo_output_head_marker_value,
                     mihomo_stdout_size=mihomo_stdout_size,
                     mihomo_stderr_size=mihomo_stderr_size,
                     kas_https_stage=kas_https_mirror,
                     kas_https_dns_configured=kas_https_dns_configured,
                     kas_https_clock_synced=kas_https_clock_synced,
                     kas_https_ca_trust_applied=kas_https_ca_trust_applied,
                     kas_https_helper_tls_verified=kas_https_helper_tls_verified,
                     service_diagnostics=service_diagnostics,
                     service_diagnostics_delayed=service_diagnostics_delayed)


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
    ap.add_argument("--kas-base-deps", action="store_true",
                    help="ONLY disposable lab exception: add 11 SHA256-pinned MIPSel base IPKs to mirror")
    ap.add_argument("--kas-mihomo", action="store_true",
                    help="ONLY disposable Ported 5.1.8 guest: pin native MIPSel Mihomo in KAS mirror")
    ap.add_argument("--kas-magitrickle", action="store_true",
                    help="ONLY disposable Ported 5.1.8 guest: pin socat+MagiTrickle IPKs and lab-probe native execution")
    ap.add_argument("--kas-https-mirror", action="store_true",
                    help="ONLY disposable Ported 5.1.8 guest: serve the pinned MagiTrickle HTTPS helper and feeds through the disposable lab CA mirror so the real KAS MagiTrickle stage runs unmodified")
    args = ap.parse_args()
    if args.expect_guarded_refusal and args.lab_ext_utils_exception:
        ap.error("lab exception and guarded refusal are mutually exclusive")
    if args.kas_base_deps and not args.lab_ext_utils_exception:
        ap.error("--kas-base-deps requires --lab-ext-utils-exception")
    if args.kas_mihomo and not args.kas_base_deps:
        ap.error("--kas-mihomo requires --kas-base-deps")
    if args.kas_magitrickle and not args.kas_mihomo:
        ap.error("--kas-magitrickle requires --kas-mihomo")
    if args.kas_https_mirror and not args.kas_magitrickle:
        ap.error("--kas-https-mirror requires --kas-magitrickle")
    if not PIN.fullmatch(args.kas_ref):
        ap.error("--kas-ref must be full 40-character SHA")
    source = load_sources(args.kas_dir, args.kas_ref)["install.sh"]
    if not source.startswith(b"#!/bin/sh"):
        ap.error("KAS installer header mismatch")
    if args.kas_base_deps:
        validated_kas_base_overlay(kas_ref=args.kas_ref)
    if args.kas_mihomo:
        validated_kas_mihomo_overlay(kas_ref=args.kas_ref)
    if args.kas_magitrickle:
        validated_kas_magitrickle_overlay(kas_ref=args.kas_ref)
    workspace = Path(args.workspace)
    workspace.mkdir(mode=0o700, exist_ok=False)
    creds = json.loads(Path(args.credentials_file).read_text(encoding="utf-8"))
    if creds.get("username") != "root":
        ap.error("expected isolated root credential")
    image, feed = workspace / "opt.img", workspace / "mirror"
    build(image, feed, workspace / "cache", args.credentials_file,
          arch="mipsel", lock_path=LOCK)
    if args.kas_base_deps:
        # Only the disposable mirror is extended, not the proven bootstrap
        # lock or original production KAS; add_packages verifies every digest.
        add_packages(feed, workspace / "cache", OVERLAY_LOCK)
        print("KAS BASE MIPSEL MIRROR: 11 extra verified IPKs", flush=True)
    if args.kas_mihomo:
        # SHA256 verification executes on the host before any guest access;
        # this adds exactly one official MIPSel soft-float package.
        add_packages(feed, workspace / "cache", MIHOMO_LOCK)
        print("KAS MIHOMO MIPSEL MIRROR: 1 extra verified IPK", flush=True)
    if args.kas_magitrickle:
        # Both overlay IPKs are digest-verified on the host before the guest
        # runs; this extends only the disposable lab mirror, not the proven
        # 25+11+1 locks or any production KAS source.
        add_magitrickle_packages(feed, workspace / "cache")
        print("KAS MAGITRICKLE MIPSEL MIRROR: 2 extra verified IPKs", flush=True)
    tls_mirror = None
    https_ca = None
    if args.kas_https_mirror:
        # Exactly five pinned records (HTTPS helper + both mipsel feed
        # indexes and IPKs); the disposable lab CA private key never leaves
        # the workspace and the shared aarch64 asset lock stays untouched.
        tls_mirror = validated_kas_mipsel_https_mirror(
            args.kas_dir, args.kas_ref, workspace, workspace / "cache")
        https_ca = (tls_mirror.private / "ca.pem").read_bytes()
        print("KAS MIPSEL HTTPS MIRROR: pinned helper and mipsel feed routes verified",
              flush=True)
    scenario = Path(__file__).resolve().parents[1] / "scenarios" / "ported-mt7621-entware-tz-skip.json"
    public = workspace / "report" / "kas-smoke.json"
    with ExitStack() as stack:
        port = stack.enter_context(Mirror(
            feed, extra_routes=(tls_mirror.http_routes if tls_mirror else None)))
        tls_port = stack.enter_context(tls_mirror) if tls_mirror else None
        return run(SimpleNamespace(
            scenario=scenario, emulator_root=args.emulator_root,
            nand_dir=args.nand_dir, output_dir=workspace / "report",
            max_seconds=args.max_seconds, preset="xiaomi-mi-router-3g-breed",
            lan_forwards="8080:80;8022:222;8023:23", opt_image=str(image),
            archive_image=None, mirror_port=port, mirror_tls_port=tls_port,
            credentials_file=args.credentials_file,
            after_scenario=lambda **ctx: guest_probe(args.credentials_file,
                workspace, ctx["deadline"], source, args.kas_ref, public,
                guarded_refusal_expected=args.expect_guarded_refusal,
                lab_ext_utils_exception=args.lab_ext_utils_exception,
                kas_base_deps_feed=args.kas_base_deps,
                kas_mihomo_feed=args.kas_mihomo,
                kas_magitrickle_feed=args.kas_magitrickle,
                kas_https_mirror=args.kas_https_mirror,
                https_ca=https_ca)))


if __name__ == "__main__":
    raise SystemExit(main())

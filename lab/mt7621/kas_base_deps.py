"""Validate a small, additive, SHA256-pinned MIPSel KAS *base* package feed.

This is an experimental Ported 5.1.8 QEMU-only feed overlay. The known-good
25-package native Entware bootstrap lock remains unchanged. No package
downloads, guest writes or production KAS changes happen in this module.
"""
import json
from pathlib import Path
import re

from lab.build_entware import MIPSLSF_ORIGIN, validate_mipsel_lock

BOOTSTRAP_LOCK = Path(__file__).resolve().parents[1] / "entware-mipselsf.lock.json"
OVERLAY_LOCK = Path(__file__).with_name("kas-base-mipsel.lock.json")
KAS_REF = "2fcd16089374604e10c201c6cd5a805fba3b100d"
REQUIRED_BASE = ("ca-bundle", "curl", "jq", "iptables", "nano", "cron")
PACKAGE_KEYS = frozenset(("Package", "Version", "Architecture", "SHA256sum",
                          "Filename", "Size", "Depends", "feed", "url"))
PACKAGE_ID = re.compile(r"[a-z0-9][a-z0-9+_.-]*")
VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+~_-]*")
SHA256 = re.compile(r"[0-9a-f]{64}")


def validated_kas_base_overlay(bootstrap_path=BOOTSTRAP_LOCK, overlay_path=OVERLAY_LOCK,
                               kas_ref=KAS_REF):
    """Return already-verified *metadata* for a closed additive MIPSel feed.

    The builder subsequently downloads the listed IPKs and rejects a byte
    unless its SHA256 matches the lock. Exactly the six installer base
    requirements plus their transitive MIPSel dependencies are permitted.
    """
    original = json.loads(Path(bootstrap_path).read_text(encoding="utf8"))
    delta = json.loads(Path(overlay_path).read_text(encoding="utf8"))
    if (delta.get("schema") != "mt7621-kas-base-mipsel-v1"
            or delta.get("for_kas_ref") != kas_ref or kas_ref != KAS_REF
            or delta.get("origin") != MIPSLSF_ORIGIN
            or delta.get("required_base") != list(REQUIRED_BASE)):
        raise ValueError("unexpected KAS base dependency lock or pinned source")
    baseline = original.get("packages")
    extra = delta.get("packages")
    if not isinstance(baseline, list) or len(baseline) != 25:
        raise ValueError("MT7621 25-package Entware baseline changed")
    validate_mipsel_lock(original)
    if not isinstance(extra, list) or not 6 <= len(extra) <= 20:
        raise ValueError("unbounded KAS-only dependency overlay")
    existing = {row["Package"] for row in baseline}
    if existing.intersection(REQUIRED_BASE):
        raise ValueError("expected base requirements already in bootstrap lock")
    additions = {}
    total_size = 0
    for row in extra:
        if not isinstance(row, dict) or set(row) != PACKAGE_KEYS:
            raise ValueError("unexpected additional IPK metadata keys")
        name, version, arch = (row["Package"], row["Version"], row["Architecture"])
        if (not PACKAGE_ID.fullmatch(name) or not VERSION.fullmatch(version)
                or arch not in ("all", "mipsel-3.4")
                or name in existing or name in additions
                or not SHA256.fullmatch(row["SHA256sum"])
                or row["feed"] != "entware"
                or row["Filename"] != name + "_" + version + "_" + arch + ".ipk"
                or row["url"] != MIPSLSF_ORIGIN + row["Filename"]
                or not row["Depends"]):
            raise ValueError("invalid or unsafe KAS base IPK: " + str(name))
        try:
            size = int(row["Size"])
        except (TypeError, ValueError):
            raise ValueError("invalid KAS base IPK byte size") from None
        if size <= 0 or size > 5_000_000:
            raise ValueError("oversized KAS base IPK")
        total_size += size
        additions[name] = row
    if total_size > 12_000_000:
        raise ValueError("oversized aggregate KAS base package set")
    validate_mipsel_lock({"installer": original["installer"],
                          "packages": baseline + extra})

    # Closure and no unrelated additions. These 11 pinned packages currently
    # have plain comma-separated, single-name Depends (no complex OR groups);
    # if upstream changes the syntax, this test must be consciously updated.
    reached = set()
    todo = list(REQUIRED_BASE)
    while todo:
        name = todo.pop()
        if name in existing or name in reached:
            continue
        row = additions.get(name)
        if row is None:
            raise ValueError("incomplete KAS base dependency closure: " + name)
        reached.add(name)
        for dependency in row["Depends"].split(","):
            dependency = dependency.strip()
            if not PACKAGE_ID.fullmatch(dependency):
                raise ValueError("ambiguous KAS base dependency: " + dependency)
            if dependency not in existing:
                todo.append(dependency)
    if reached != set(additions):
        raise ValueError("unrelated or unreachable KAS base package in overlay")
    return delta

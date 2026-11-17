"""Strict one-package, original-ABI Mihomo feed for *disposable* MT7621 KAS tests.

The 25-package Entware bootstrap and 11-package KAS base overlay are not
modified. This module only validates pinned metadata; IPK bytes are independently
SHA256-verified by lab.build_entware.add_packages() before guest execution.
No production router, firmware or KAS source may use this overlay.
"""
import json
from pathlib import Path
import re

from lab.build_entware import MIPSLSF_ORIGIN, validate_mipsel_lock
from lab.mt7621.kas_base_deps import (
    BOOTSTRAP_LOCK, OVERLAY_LOCK as BASE_LOCK, KAS_REF,
    validated_kas_base_overlay,
)

MIHOMO_LOCK = Path(__file__).with_name("kas-mihomo-mipsel.lock.json")
MIHOMO_NAME = "mihomo"
MIHOMO_VERSION = "1.19.33-1"
MIHOMO_SHA256 = "3f37931cafa0ff08d99a718a0aba171ae4c1dea390a51d557a9baf77aad5f087"
MIHOMO_SIZE = 20301332
MIHOMO_FILENAME = "mihomo_1.19.33-1_mipsel-3.4.ipk"
IPK_FIELDS = {"Package", "Version", "Architecture", "SHA256sum", "Filename",
              "Size", "Depends", "feed", "url"}


def validated_kas_mihomo_overlay(bootstrap_path=BOOTSTRAP_LOCK,
                                 base_path=BASE_LOCK,
                                 mihomo_path=MIHOMO_LOCK, kas_ref=KAS_REF):
    """Fail closed on SHA/source/ABI/dependency/baseline drift before downloads."""
    base = validated_kas_base_overlay(bootstrap_path=bootstrap_path,
                                      overlay_path=base_path, kas_ref=kas_ref)
    original = json.loads(Path(bootstrap_path).read_text(encoding="utf8"))
    candidate = json.loads(Path(mihomo_path).read_text(encoding="utf8"))
    if (set(candidate) != {"schema", "for_kas_ref", "origin",
                           "expected_package_version", "packages"}
            or candidate["schema"] != "mt7621-kas-mihomo-mipsel-v1"
            or candidate["for_kas_ref"] != kas_ref or kas_ref != KAS_REF
            or candidate["origin"] != MIPSLSF_ORIGIN
            or candidate["expected_package_version"] != MIHOMO_VERSION):
        raise ValueError("unexpected pinned KAS Mihomo source or architecture")
    packages = candidate["packages"]
    if not isinstance(packages, list) or len(packages) != 1:
        raise ValueError("KAS Mihomo overlay must contain exactly one IPK")
    row = packages[0]
    if not isinstance(row, dict) or set(row) != IPK_FIELDS:
        raise ValueError("unexpected KAS Mihomo IPK metadata")
    if (row["Package"] != MIHOMO_NAME or row["Version"] != MIHOMO_VERSION
            or row["Architecture"] != "mipsel-3.4"
            or row["Filename"] != MIHOMO_FILENAME
            or row["url"] != MIPSLSF_ORIGIN + MIHOMO_FILENAME
            or row["feed"] != "entware"
            or row["Depends"] != "ca-bundle"
            or row["SHA256sum"] != MIHOMO_SHA256
            or not re.fullmatch("[0-9a-f]{64}", row["SHA256sum"])
            or str(row["Size"]) != str(MIHOMO_SIZE)):
        raise ValueError("unsafe or drifted MIPSel Mihomo source")
    # All 25+11 previous packages are still in the same dependency-complete
    # local Entware feed; introducing a different ABI or extra package is invalid.
    complete = original["packages"] + base["packages"] + packages
    if validate_mipsel_lock({"packages": complete}) != 37:
        raise ValueError("unexpected combined Mihomo dependency closure")
    return candidate

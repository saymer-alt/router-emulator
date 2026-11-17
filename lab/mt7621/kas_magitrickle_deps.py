"""Strict two-package MagiTrickle feed for *disposable* MT7621 KAS tests.

socat arrives from the pinned Entware MIPSel origin; magitrickle arrives from
the pinned upstream MagiTrickle origin (an HTTPS host this repository already
approves for pinned lab assets in lab/kas_mirror.py). Every byte is verified
against a committed SHA256 before any guest execution. The 25-IPK bootstrap,
11-IPK KAS base and single Mihomo locks are validated, never modified.
Lab-direct installation in the guest probes the NEXT KAS dependency; it is
NOT a substitute for the real KAS installer running its MagiTrickle stage.
"""
import gzip
import hashlib
import json
from pathlib import Path
import re
import shutil
import time
import urllib.error
import urllib.request

from lab.build_entware import MIPSLSF_ORIGIN, validate_mipsel_lock
from lab.mt7621.kas_base_deps import (
    BOOTSTRAP_LOCK, OVERLAY_LOCK as BASE_LOCK, KAS_REF,
)
from lab.mt7621.kas_mihomo_deps import (
    MIHOMO_LOCK, validated_kas_mihomo_overlay,
)

MAGITRICKLE_LOCK = Path(__file__).with_name("kas-magitrickle-mipsel.lock.json")
MAGITRICKLE_NAME = "magitrickle"
MAGITRICKLE_VERSION = "0.8.2-1"
MAGITRICKLE_SHA256 = ("5a10083cc063731536d8c7820c008348e99a16cf821534f51f1a"
                      "415f50421c0e")
MAGITRICKLE_SIZE = 2418259
MAGITRICKLE_FILENAME = "magitrickle_0.8.2-1_entware_mipsel-3.4_kn.ipk"
MAGITRICKLE_ARCH = "mipsel-3.4_kn"
MAGITRICKLE_ORIGIN = "https://bin.magitrickle.dev/packages/entware/mipsel-3.4_kn/"
SOCAT_NAME = "socat"
SOCAT_VERSION = "1.8.1.1-1"
SOCAT_SHA256 = ("5ea29a5a92f8cf6e777d9fe50aee028a15e8814ff6f63986c53ece5c78"
                "0f6b2a")
SOCAT_SIZE = 147555
SOCAT_FILENAME = "socat_1.8.1.1-1_mipsel-3.4.ipk"
SOCAT_DEPENDS = "libc, libssp, librt, libpthread, libopenssl"
MAGITRICKLE_DEPENDS = "libc, iptables, socat"
IPK_FIELDS = {"Package", "Version", "Architecture", "SHA256sum", "Filename",
              "Size", "Depends", "feed", "url"}
LOCK_FIELDS = {"schema", "for_kas_ref", "magitrickle_origin",
               "expected_magitrickle_version", "expected_socat_version",
               "packages"}


def _fetch_magitrickle(url, digest, destination):
    """Bounded pinned HTTPS fetch from the approved MagiTrickle asset origin."""
    if not url.startswith(MAGITRICKLE_ORIGIN):
        raise ValueError("unexpected pinned MagiTrickle origin: " + url)
    destination = Path(destination)
    for attempt in (1, 2, 3):
        try:
            with urllib.request.urlopen(url, timeout=120) as response:
                data = response.read()
            break
        except urllib.error.HTTPError as error:
            if attempt == 3 or error.code not in (502, 503, 504):
                raise ValueError(
                    "pinned MagiTrickle fetch failed: HTTP %d: %s"
                    % (error.code, url)) from error
            time.sleep(min(15, 3 * attempt))
    else:
        raise ValueError("pinned MagiTrickle fetch failed after retries: " + url)
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError("pinned MagiTrickle digest mismatch: " + url)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)


def validated_kas_magitrickle_overlay(bootstrap_path=BOOTSTRAP_LOCK,
                                      base_path=BASE_LOCK,
                                      mihomo_path=MIHOMO_LOCK,
                                      magitrickle_path=MAGITRICKLE_LOCK,
                                      kas_ref=KAS_REF):
    """Fail closed on SHA/source/ABI/dependency/digest drift before downloads."""
    validated_kas_mihomo_overlay(bootstrap_path=bootstrap_path,
                                 base_path=base_path,
                                 mihomo_path=mihomo_path, kas_ref=kas_ref)
    candidate = json.loads(Path(magitrickle_path).read_text(encoding="utf8"))
    if (set(candidate) != LOCK_FIELDS
            or candidate["schema"] != "mt7621-kas-magitrickle-mipsel-v1"
            or candidate["for_kas_ref"] != kas_ref or kas_ref != KAS_REF
            or candidate["magitrickle_origin"] != MAGITRICKLE_ORIGIN
            or candidate["expected_magitrickle_version"] != MAGITRICKLE_VERSION
            or candidate["expected_socat_version"] != SOCAT_VERSION):
        raise ValueError("unexpected pinned KAS MagiTrickle source or version")
    packages = candidate["packages"]
    if not isinstance(packages, list) or len(packages) != 2:
        raise ValueError("KAS MagiTrickle overlay must contain exactly two IPKs")
    rows = {row.get("Package"): row for row in packages
            if isinstance(row, dict)}
    if set(rows) != {SOCAT_NAME, MAGITRICKLE_NAME}:
        raise ValueError("unexpected KAS MagiTrickle package set")
    socat = rows[SOCAT_NAME]
    if (set(socat) != IPK_FIELDS
            or socat["Version"] != SOCAT_VERSION
            or socat["Architecture"] != "mipsel-3.4"
            or socat["Filename"] != SOCAT_FILENAME
            or socat["url"] != MIPSLSF_ORIGIN + SOCAT_FILENAME
            or socat["feed"] != "entware"
            or socat["Depends"] != SOCAT_DEPENDS
            or socat["SHA256sum"] != SOCAT_SHA256
            or str(socat["Size"]) != str(SOCAT_SIZE)):
        raise ValueError("unsafe or drifted pinned socat source")
    magitrickle = rows[MAGITRICKLE_NAME]
    if (set(magitrickle) != IPK_FIELDS
            or magitrickle["Version"] != MAGITRICKLE_VERSION
            or magitrickle["Architecture"] != MAGITRICKLE_ARCH
            or magitrickle["Filename"] != MAGITRICKLE_FILENAME
            or magitrickle["url"] != MAGITRICKLE_ORIGIN + MAGITRICKLE_FILENAME
            or magitrickle["feed"] != "entware"
            or magitrickle["Depends"] != MAGITRICKLE_DEPENDS
            or magitrickle["SHA256sum"] != MAGITRICKLE_SHA256
            or not re.fullmatch("[0-9a-f]{64}", magitrickle["SHA256sum"])
            or str(magitrickle["Size"]) != str(MAGITRICKLE_SIZE)):
        raise ValueError("unsafe or drifted pinned MagiTrickle source")
    # The existing 25+11+1 closure plus socat must still validate as a
    # complete MIPSel dependency closure (39 packages in total afterwards).
    bootstrap = json.loads(Path(bootstrap_path).read_text(encoding="utf8"))
    base = json.loads(Path(base_path).read_text(encoding="utf8"))
    mihomo = json.loads(Path(mihomo_path).read_text(encoding="utf8"))
    entware_closure = (bootstrap["packages"] + base["packages"]
                       + mihomo["packages"] + [socat])
    if validate_mipsel_lock({"packages": entware_closure}) != 38:
        raise ValueError("unexpected combined socat dependency closure")
    names = {row["Package"] for row in entware_closure}
    for group in magitrickle["Depends"].split(","):
        if group.strip() and group.strip() not in names:
            raise ValueError("incomplete MagiTrickle dependency: " + group.strip())
    return candidate


def add_magitrickle_packages(mirror, cache, lock=None):
    """Extend the disposable mirror with verified socat and magitrickle IPKs.

    socat comes through the trusted Entware fetch; magitrickle through
    _fetch_magitrickle (approved HTTPS origin, committed digest). Both rows
    merge into the local feed index exactly like lab.build_entware does.
    """
    from lab.build_entware import fetch
    data = lock or validated_kas_magitrickle_overlay()
    directory = Path(mirror) / "entware"
    index = directory / "Packages.gz"
    blocks = {}
    for block in gzip.decompress(index.read_bytes()).decode().split("\n\n"):
        if block.startswith("Package: "):
            blocks[block.splitlines()[0][9:]] = block
    for row in data["packages"]:
        destination = Path(cache) / row["Filename"]
        if row["Package"] == SOCAT_NAME:
            fetch(row["url"], row["SHA256sum"], destination)
        else:
            _fetch_magitrickle(row["url"], row["SHA256sum"], destination)
        shutil.copyfile(destination, directory / row["Filename"])
        blocks[row["Package"]] = "\n".join(
            key + ": " + value for key, value in row.items()
            if key not in ("feed", "url"))
    index.write_bytes(gzip.compress(
        ("\n\n".join(blocks.values()) + "\n\n").encode(), mtime=0))

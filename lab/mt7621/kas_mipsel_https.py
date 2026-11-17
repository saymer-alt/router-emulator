"""MIPSel HTTPS delivery contract for the real KAS MagiTrickle stage.

The pinned installer (KAS 2fcd1608...) has exactly one HTTPS dependency on
an Entware/MIPSel guest: https://bin.magitrickle.dev/packages/add_repo.sh
(verified against every https:// literal in the pinned install.sh bytes).
The MagiTrickle stage itself then registers plain-HTTP
bin.magitrickle.dev/packages/entware/<arch> package feeds, which ride the
existing port-80 lab mirror by Host header - no second HTTPS surface exists.

This module pins every byte the guest can receive (helper script, both
mipsel feed indexes and both feed IPKs), serves them through the existing
disposable lab CA mirror, and refuses any upstream drift. The original KAS
sources, the proven 25+11+1+2 IPK locks and the shared aarch64 asset lock
are never modified.
"""
import gzip
import hashlib
import json
import re
from pathlib import Path

from lab.kas_mirror import KASMirror, APPROVED_ASSET_HOSTS

LOCK = Path(__file__).with_name("kas-mipsel-https.lock.json")
SCHEMA = "mt7621-kas-mipsel-https-v1"
FOR_KAS_REF = "2fcd16089374604e10c201c6cd5a805fba3b100d"

HELPER_URL = "https://bin.magitrickle.dev/packages/add_repo.sh"
HELPER_SHA256 = "23a3a5ed92da77b79a2795ad16a7769787e8c5121023599f07c3bfcd58a9e766"
HELPER_ROUTE = ("https", "bin.magitrickle.dev", "/packages/add_repo.sh")

FEEDS = {
    "mipsel-3.4": {
        "index_route": ("http", "bin.magitrickle.dev",
                        "/packages/entware/mipsel-3.4/Packages.gz"),
        "index_sha256": "c977c9e3d65551ae1ba8809a9532518b4389269063694f219f97655ce506337f",
        "ipk_filename": "magitrickle_0.8.2-1_entware_mipsel-3.4.ipk",
        "ipk_sha256": "35dd3ae42cce54ba4952f9f579782bb7e174dd58efece19fa26e17491b75c5ac",
        "ipk_md5": "1856567cf5bb9836838ad3e3215f35f6",
        "ipk_size": 2413113,
        "depends": "libc, iptables",
    },
    "mipsel-3.4_kn": {
        "index_route": ("http", "bin.magitrickle.dev",
                        "/packages/entware/mipsel-3.4_kn/Packages.gz"),
        "index_sha256": "3dbd51f1ff78bdf7ca53b663aa6d125f36e57afd3e8bd8006e463e97aca8da6d",
        "ipk_filename": "magitrickle_0.8.2-1_entware_mipsel-3.4_kn.ipk",
        "ipk_sha256": "5a10083cc063731536d8c7820c008348e99a16cf821534f51f1a415f50421c0e",
        "ipk_md5": "293ee9b1e50c4c08dae8a30eef9e12fb",
        "ipk_size": 2418259,
        "depends": "libc, iptables, socat",
    },
}
MAGITRICKLE_VERSION = "0.8.2-1"


def validated_kas_mipsel_https_lock():
    """Fail closed on any drift of the five pinned HTTPS/feed records."""
    data = json.loads(LOCK.read_text(encoding="utf-8"))
    if data.get("schema") != SCHEMA:
        raise ValueError("mipsel HTTPS lock schema drift")
    if data.get("for_kas_ref") != FOR_KAS_REF:
        raise ValueError("mipsel HTTPS lock is pinned to a different KAS ref")
    if data.get("guest_arch") != "mipsel":
        raise ValueError("mipsel HTTPS lock guest architecture drift")
    if set(data) != {"schema", "for_kas_ref", "guest_arch",
                     "helper_route", "feed_records"}:
        raise ValueError("mipsel HTTPS lock has unexpected top-level keys")
    helper = data["helper_route"]
    if set(helper) != {"url", "fetch_url", "filename", "sha256"}:
        raise ValueError("mipsel HTTPS helper record drift")
    if helper["url"] != HELPER_URL:
        raise ValueError("mipsel HTTPS helper URL drift")
    if helper["fetch_url"] != HELPER_URL or helper["sha256"] != HELPER_SHA256:
        raise ValueError("mipsel HTTPS helper fetch/digest drift")
    parsed = re.match(r"https://([^/]+)/packages/add_repo\.sh$", helper["fetch_url"])
    if parsed is None or parsed.group(1) not in APPROVED_ASSET_HOSTS:
        raise ValueError("mipsel HTTPS helper origin not approved")
    records = data["feed_records"]
    if len(records) != 2 * len(FEEDS):
        raise ValueError("mipsel HTTPS feed record count drift")
    for record in records:
        if set(record) != {"url", "fetch_url", "filename", "sha256"}:
            raise ValueError("mipsel HTTPS feed record key drift")
        fetch = record["fetch_url"]
        if not fetch.startswith("https://") or re.match(
                r"https://([^/]+)/", fetch).group(1) not in APPROVED_ASSET_HOSTS:
            raise ValueError("mipsel HTTPS feed fetch origin not approved")
        # add_repo.sh registers plain-HTTP package feeds; the route (what the
        # guest requests) must stay HTTP exactly as upstream wrote it, while
        # the fetch stays HTTPS-verified on the host.
        if not record["url"].startswith("http://bin.magitrickle.dev/packages/entware/"):
            raise ValueError("mipsel HTTPS feed route URL drift")
        arch = record["url"].split("/packages/entware/")[1].split("/")[0]
        pins = FEEDS.get(arch)
        if pins is None:
            raise ValueError("mipsel HTTPS feed for unexpected architecture")
        if record["filename"].endswith("Packages.gz"):
            if record["url"] != "http://bin.magitrickle.dev/packages/entware/" \
                    + arch + "/Packages.gz":
                raise ValueError("mipsel HTTPS index URL drift")
            if record["sha256"] != pins["index_sha256"]:
                raise ValueError("mipsel HTTPS index digest drift")
        elif record["filename"] == pins["ipk_filename"]:
            if record["url"] != "http://bin.magitrickle.dev/packages/entware/" \
                    + arch + "/" + pins["ipk_filename"]:
                raise ValueError("mipsel HTTPS IPK URL drift")
            if record["sha256"] != pins["ipk_sha256"]:
                raise ValueError("mipsel HTTPS IPK digest drift")
        else:
            raise ValueError("mipsel HTTPS feed record filename drift")
    # The mirror must serve the HTTPS helper AND the plain-HTTP feed records;
    # the helper record comes first so its route lands in the HTTPS table.
    return [helper] + records


def validated_feed_index(index_bytes, arch):
    """The upstream Packages.gz must describe exactly the pinned IPK."""
    pins = FEEDS[arch]
    text = gzip.decompress(index_bytes).decode("utf-8", "replace")
    fields = dict(re.findall(r"(?m)^([A-Za-z0-9-]+):\s*(.*?)\s*$", text))
    expected = {
        "Package": "magitrickle",
        "Version": MAGITRICKLE_VERSION,
        "Architecture": arch,
        "Filename": pins["ipk_filename"],
        "Size": str(pins["ipk_size"]),
        "MD5Sum": pins["ipk_md5"],
        "Depends": pins["depends"],
    }
    for key, value in expected.items():
        if fields.get(key) != value:
            raise ValueError("mipsel feed index %s drift" % key)


def validated_kas_mipsel_https_mirror(kas_dir, kas_ref, private, cache):
    """Disposable lab TLS mirror serving exactly the five pinned records."""
    records = validated_kas_mipsel_https_lock()
    mirror = KASMirror(kas_dir, kas_ref, private, cache, asset_records=records)
    helper_bytes = mirror.routes.get(HELPER_ROUTE)
    if helper_bytes is None or hashlib.sha256(helper_bytes).hexdigest() \
            != HELPER_SHA256:
        raise ValueError("lab mirror does not serve the pinned MagiTrickle helper")
    if not helper_bytes.startswith(b"#!/bin/sh"):
        raise ValueError("pinned MagiTrickle helper lost its shell header")
    if b"packages/entware/" not in helper_bytes \
            or b"magitrickle.conf" not in helper_bytes:
        raise ValueError("pinned MagiTrickle helper content drift")
    http_routes = mirror.http_routes
    for arch, pins in FEEDS.items():
        index_bytes = http_routes.get(pins["index_route"])
        if index_bytes is None or hashlib.sha256(index_bytes).hexdigest() \
                != pins["index_sha256"]:
            raise ValueError("lab mirror does not serve pinned %s index" % arch)
        validated_feed_index(index_bytes, arch)
        ipk_route = (pins["index_route"][0], pins["index_route"][1],
                     "/packages/entware/" + arch + "/" + pins["ipk_filename"])
        ipk_bytes = http_routes.get(ipk_route)
        if ipk_bytes is None or hashlib.sha256(ipk_bytes).hexdigest() \
                != pins["ipk_sha256"]:
            raise ValueError("lab mirror does not serve pinned %s IPK" % arch)
    return mirror

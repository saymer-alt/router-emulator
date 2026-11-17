"""Host-only contract tests for the MIPSel KAS HTTPS mirror records.

No network access: every case validates the pinned lock bytes and the
index/IPK consistency logic that gates the disposable lab TLS mirror.
"""
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import lab.mt7621.kas_mipsel_https as mipsel_https_module
from lab.mt7621.kas_mipsel_https import (
    FEEDS, HELPER_SHA256, HELPER_URL, LOCK, SCHEMA,
    validated_feed_index, validated_kas_mipsel_https_lock,
)


def synthetic_index(arch):
    pins = FEEDS[arch]
    fields = "\n".join([
        "Package: magitrickle",
        "Version: 0.8.2-1",
        "Depends: " + pins["depends"],
        "Section: net",
        "Architecture: " + arch,
        "MD5Sum: " + pins["ipk_md5"],
        "Size: " + str(pins["ipk_size"]),
        "Filename: " + pins["ipk_filename"],
        "Description: DNS-based routing application",
        "",
    ])
    return gzip.compress(fields.encode())


class MipselHttpsLockTests(unittest.TestCase):
    def test_real_lock_passes_every_pin_and_returns_helper_first(self):
        records = validated_kas_mipsel_https_lock()
        self.assertEqual(len(records), 1 + 2 * len(FEEDS))
        self.assertEqual(records[0]["url"], HELPER_URL)
        self.assertEqual(records[0]["sha256"], HELPER_SHA256)
        routes = {(record["url"]) for record in records}
        for arch, pins in FEEDS.items():
            self.assertIn("http://bin.magitrickle.dev/packages/entware/"
                          + arch + "/Packages.gz", routes)
            self.assertIn("http://bin.magitrickle.dev/packages/entware/"
                          + arch + "/" + pins["ipk_filename"], routes)

    def test_feed_routes_stay_plain_http_like_upstream_add_repo_sh(self):
        # add_repo.sh writes src/gz lines with http:// for Entware; only the
        # helper itself is fetched over HTTPS. The mirror routes must match
        # byte-for-byte what the pinned helper registers, while every
        # fetch_url stays HTTPS-verified on the host.
        data = json.loads(LOCK.read_text(encoding="utf-8"))
        self.assertEqual(data["schema"], SCHEMA)
        for record in data["feed_records"]:
            self.assertTrue(record["url"].startswith("http://bin.magitrickle.dev/"))
            self.assertTrue(record["fetch_url"].startswith("https://bin.magitrickle.dev/"))

    def mutate(self, change):
        data = json.loads(LOCK.read_text(encoding="utf-8"))
        change(data)
        return json.dumps(data)

    def assert_lock_rejected(self, text):
        with tempfile.TemporaryDirectory() as td:
            fake = Path(td) / "lock.json"
            fake.write_text(text, encoding="utf-8")
            with patch.object(mipsel_https_module, "LOCK", fake):
                with self.assertRaises(ValueError):
                    validated_kas_mipsel_https_lock()

    def test_lock_rejects_every_drift_direction(self):
        cases = {
            "schema drift": lambda d: d.__setitem__("schema", "other-v1"),
            "kas ref drift": lambda d: d.__setitem__("for_kas_ref", "b" * 40),
            "guest arch drift": lambda d: d.__setitem__("guest_arch", "aarch64"),
            "extra top key": lambda d: d.__setitem__("extra", 1),
            "missing feed record": lambda d: d["feed_records"].pop(),
            "extra feed record": lambda d: d["feed_records"].append(
                dict(d["feed_records"][0])),
            "helper url drift": lambda d: d["helper_route"].__setitem__(
                "url", "https://bin.magitrickle.dev/packages/other.sh"),
            "helper digest drift": lambda d: d["helper_route"].__setitem__(
                "sha256", "0" * 64),
            "helper fetch origin drift": lambda d: d["helper_route"].__setitem__(
                "fetch_url", "https://evil.example/packages/add_repo.sh"),
            "feed digest drift": lambda d: d["feed_records"][0].__setitem__(
                "sha256", "1" * 64),
            "feed route scheme drift": lambda d: d["feed_records"][0].__setitem__(
                "url", "https://bin.magitrickle.dev/packages/entware/mipsel-3.4/Packages.gz"),
            "feed fetch scheme drift": lambda d: d["feed_records"][0].__setitem__(
                "fetch_url", "http://bin.magitrickle.dev/packages/entware/mipsel-3.4/Packages.gz"),
            "feed filename drift": lambda d: d["feed_records"][0].__setitem__(
                "filename", "other.gz"),
            "feed key drift": lambda d: d["feed_records"][0].__setitem__("extra", 1),
        }
        for label, change in cases.items():
            with self.subTest(label=label):
                self.assert_lock_rejected(self.mutate(change))


class MipselFeedIndexTests(unittest.TestCase):
    def test_synthetic_indexes_match_every_pinned_field(self):
        for arch in FEEDS:
            validated_feed_index(synthetic_index(arch), arch)

    def test_index_drift_is_denied(self):
        pins = FEEDS["mipsel-3.4_kn"]
        variants = {
            "version": ("Version: 0.8.2-1", "Version: 0.8.3-1"),
            "architecture": ("Architecture: mipsel-3.4_kn",
                             "Architecture: aarch64-3.10"),
            "md5": ("MD5Sum: " + pins["ipk_md5"], "MD5Sum: " + "0" * 32),
            "size": ("Size: " + str(pins["ipk_size"]), "Size: 1"),
            "filename": ("Filename: " + pins["ipk_filename"],
                         "Filename: other.ipk"),
            "depends": ("Depends: " + pins["depends"], "Depends: libc"),
            "package": ("Package: magitrickle", "Package: other"),
        }
        for label, (old, new) in variants.items():
            with self.subTest(label=label):
                fields = synthetic_index("mipsel-3.4_kn").decode("utf-8", "replace")
                broken = gzip.compress(fields.replace(old, new).encode())
                with self.assertRaises(ValueError):
                    validated_feed_index(broken, "mipsel-3.4_kn")

    def test_index_for_wrong_arch_is_denied(self):
        with self.assertRaises(ValueError):
            validated_feed_index(synthetic_index("mipsel-3.4"), "mipsel-3.4_kn")

    def test_not_gzip_is_denied(self):
        with self.assertRaises(Exception):
            validated_feed_index(b"not gzip", "mipsel-3.4")


class MipselValidatedMirrorTests(unittest.TestCase):
    """Offline end-to-end regression for CI run 44160249375.

    The validated mirror looked its plain-HTTP feed records up with
    scheme-ful 3-tuples while KASMirror.http_routes re-keys the route table
    to (host, path) pairs, so every lookup returned None and the run died
    before any guest started. Synthetic pins (fixture-backed assets, a
    throwaway KAS git repo, a disposable CA) exercise the real production
    function without network access.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.kas_dir = self.root / "kas"
        self.kas_dir.mkdir()
        (self.kas_dir / "install.sh").write_bytes(b"#!/bin/sh\nsynthetic\n")
        for command in (
            ["git", "init", "-q"],
            ["git", "add", "install.sh"],
            ["git", "-c", "user.email=lab@test", "-c", "user.name=lab",
             "commit", "-q", "-m", "synthetic KAS"],
        ):
            subprocess.run(command, cwd=self.kas_dir, check=True,
                           capture_output=True)
        self.kas_ref = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=self.kas_dir, text=True).strip()
        (self.root / "private").mkdir()

    def test_validated_mirror_serves_pinned_records_by_host_path(self):
        helper_bytes = b"#!/bin/sh\necho packages/entware/ > magitrickle.conf\n"
        fixture = self.root / "add_repo.sh"
        fixture.write_bytes(helper_bytes)
        records = [{
            "url": HELPER_URL,
            "fetch_url": HELPER_URL,
            "filename": "add_repo.sh",
            "sha256": hashlib.sha256(helper_bytes).hexdigest(),
            "fixture": str(fixture),
        }]
        feeds = {}
        for arch, pins in FEEDS.items():
            index_bytes = synthetic_index(arch)
            ipk_bytes = b"synthetic-ipk-" + arch.encode()
            index_path = self.root / (arch + "-Packages.gz")
            index_path.write_bytes(index_bytes)
            ipk_path = self.root / (arch + "-ipk")
            ipk_path.write_bytes(ipk_bytes)
            feeds[arch] = dict(pins,
                               index_sha256=hashlib.sha256(index_bytes).hexdigest(),
                               ipk_sha256=hashlib.sha256(ipk_bytes).hexdigest())
            records.append({
                "url": "http://" + pins["index_route"][1] + pins["index_route"][2],
                "fetch_url": "https://" + pins["index_route"][1] + pins["index_route"][2],
                "filename": "Packages.gz",
                "sha256": hashlib.sha256(index_bytes).hexdigest(),
                "fixture": str(index_path),
            })
            records.append({
                "url": "http://" + pins["index_route"][1] + "/packages/entware/"
                       + arch + "/" + pins["ipk_filename"],
                "fetch_url": "https://" + pins["index_route"][1] + "/packages/entware/"
                             + arch + "/" + pins["ipk_filename"],
                "filename": pins["ipk_filename"],
                "sha256": hashlib.sha256(ipk_bytes).hexdigest(),
                "fixture": str(ipk_path),
            })
        with patch.object(mipsel_https_module, "HELPER_SHA256",
                          hashlib.sha256(helper_bytes).hexdigest()), \
                patch.object(mipsel_https_module, "FEEDS", feeds), \
                patch.object(mipsel_https_module,
                             "validated_kas_mipsel_https_lock",
                             lambda: records):
            mirror = mipsel_https_module.validated_kas_mipsel_https_mirror(
                self.kas_dir, self.kas_ref, self.root / "private",
                self.root / "cache")
        for arch, pins in feeds.items():
            # The serving contract the validation must look up against:
            # (host, path) pairs, never scheme-ful tuples.
            self.assertIn(pins["index_route"][1:], mirror.http_routes)
            self.assertIn((pins["index_route"][1],
                           "/packages/entware/" + arch + "/"
                           + pins["ipk_filename"]), mirror.http_routes)

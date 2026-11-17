"""Host-only negative tests for pinned socat+MagiTrickle MIPSel IPKs."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from lab.mt7621.kas_magitrickle_deps import (
    MAGITRICKLE_LOCK, MAGITRICKLE_SIZE, MAGITRICKLE_VERSION,
    SOCAT_SIZE, SOCAT_VERSION, validated_kas_magitrickle_overlay,
)


class MT7621MagiTrickleOverlayTests(unittest.TestCase):
    def setUp(self):
        self.model = json.loads(MAGITRICKLE_LOCK.read_text(encoding="utf8"))

    def changed(self, change):
        obj = copy.deepcopy(self.model)
        change(obj)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "magitrickle.json"
            path.write_text(json.dumps(obj), encoding="utf8")
            return validated_kas_magitrickle_overlay(magitrickle_path=path)

    def test_exact_two_ipk_and_38_package_closure(self):
        valid = validated_kas_magitrickle_overlay()
        self.assertEqual(len(valid["packages"]), 2)
        self.assertEqual({row["Package"] for row in valid["packages"]},
                         {"socat", "magitrickle"})
        sizes = {row["Package"]: int(row["Size"]) for row in valid["packages"]}
        self.assertEqual(sizes, {"socat": SOCAT_SIZE,
                                 "magitrickle": MAGITRICKLE_SIZE})
        versions = {row["Package"]: row["Version"] for row in valid["packages"]}
        self.assertEqual(versions, {"socat": SOCAT_VERSION,
                                    "magitrickle": MAGITRICKLE_VERSION})

    def test_unknown_ref_denied(self):
        with self.assertRaises(ValueError):
            validated_kas_magitrickle_overlay(kas_ref="f" * 40)

    def test_wrong_arch_and_digest_denied(self):
        for package, field, value in (
                ("magitrickle", "Architecture", "aarch64-3.10"),
                ("magitrickle", "Architecture", "mipsel-3.4"),
                ("socat", "Architecture", "mipsel-3.4_kn"),
                ("magitrickle", "SHA256sum", "f" * 64),
                ("socat", "SHA256sum", "f" * 64),
                ("magitrickle", "Version", "0.0.1-1"),
                ("socat", "Version", "1.8.0-1"),
                ("magitrickle", "Size", str(MAGITRICKLE_SIZE - 1)),
                ("socat", "Size", str(SOCAT_SIZE - 1)),
                ("magitrickle", "Depends", "libc, iptables"),
                ("magitrickle", "Depends", "libc, curl, socat")):
            with self.subTest(package=package, field=field):
                with self.assertRaises(ValueError):
                    self.changed(lambda x, p=package, f=field, v=value:
                                 next(row for row in x["packages"]
                                      if row["Package"] == p).__setitem__(f, v))

    def test_unapproved_origin_and_filename_denied(self):
        for package, field, value in (
                ("magitrickle", "url", "http://bin.magitrickle.dev/"
                 "packages/entware/mipsel-3.4_kn/magitrickle.ipk"),
                ("magitrickle", "url", "https://example.invalid/magitrickle.ipk"),
                ("magitrickle", "Filename", "magitrickle.ipk"),
                ("socat", "url", "https://bin.entware.net/mipselsf-k3.4/"
                 "socat_9.9.9_mipsel-3.4.ipk"),
                ("socat", "Filename", "socat.ipk")):
            with self.subTest(package=package, field=field):
                with self.assertRaises(ValueError):
                    self.changed(lambda x, p=package, f=field, v=value:
                                 next(row for row in x["packages"]
                                      if row["Package"] == p).__setitem__(f, v))

    def test_unsafe_non_https_magitrickle_origin_denied(self):
        with self.assertRaises(ValueError):
            self.changed(lambda x: x.__setitem__(
                "magitrickle_origin",
                "http://bin.magitrickle.dev/packages/entware/mipsel-3.4_kn/"))

    def test_extra_missing_or_renamed_packages_denied(self):
        for arr in ([], self.model["packages"] * 2,
                    [row for row in self.model["packages"]
                     if row["Package"] != "socat"],
                    self.model["packages"] + [dict(self.model["packages"][0],
                                                   Package="extra")]):
            with self.subTest(count=len(arr)), self.assertRaises(ValueError):
                self.changed(lambda x, a=arr: x.__setitem__("packages", a))

    def test_unexpected_keys_or_drift_denied(self):
        for change in (
                lambda x: x["packages"][0].update(debug=True),
                lambda x: x["packages"][0].update(feed="keenetic"),
                lambda x: x.__setitem__("origin", "https://host.invalid/"),
                lambda x: x.__setitem__("schema", "version2"),
                lambda x: x.__setitem__("expected_magitrickle_version", "9.9"),
                lambda x: x.__setitem__("expected_socat_version", "9.9"),
                lambda x: x.__setitem__("for_kas_ref", "0" * 40)):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.changed(change)

    def test_dependency_closure_rejects_missing_socat_dependency(self):
        # A socat row whose Depends lists a package absent from the whole
        # 38-IPK closure must fail closed before any download.
        def break_closure(model):
            obj = copy.deepcopy(model)
            socat = next(row for row in obj["packages"]
                         if row["Package"] == "socat")
            socat["Depends"] = "libc, not-in-closure-lib"
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "broken.json"
                path.write_text(json.dumps(obj), encoding="utf8")
                with self.assertRaises(ValueError):
                    validated_kas_magitrickle_overlay(magitrickle_path=path)

        break_closure(self.model)


if __name__ == "__main__":
    unittest.main()

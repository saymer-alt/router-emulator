"""Cheap host-only contracts for the optional MT7621 KAS base IPK feed."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from lab.mt7621.kas_base_deps import (
    BOOTSTRAP_LOCK, OVERLAY_LOCK, REQUIRED_BASE, KAS_REF,
    validated_kas_base_overlay,
)
from lab.build_entware import validate_mipsel_lock


class KASBaseMipselLockTests(unittest.TestCase):
    def setUp(self):
        self.bootstrap = json.loads(BOOTSTRAP_LOCK.read_text(encoding="utf8"))
        self.delta = json.loads(OVERLAY_LOCK.read_text(encoding="utf8"))

    def validate_changed(self, edit):
        delta = copy.deepcopy(self.delta)
        edit(delta)
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "kas.json"
            path.write_text(json.dumps(delta), encoding="utf8")
            return validated_kas_base_overlay(overlay_path=path)

    def test_verified_25_plus_11_and_base_requirements(self):
        good = validated_kas_base_overlay()
        original_names = {x["Package"] for x in self.bootstrap["packages"]}
        extra_names = {x["Package"] for x in good["packages"]}
        self.assertEqual(len(self.bootstrap["packages"]), 25)
        self.assertEqual(len(good["packages"]), 11)
        self.assertFalse(extra_names & original_names)
        self.assertTrue(set(REQUIRED_BASE) <= extra_names)
        self.assertEqual(validate_mipsel_lock({
            "installer": self.bootstrap["installer"],
            "packages": self.bootstrap["packages"] + good["packages"]}), 36)

    def test_wrong_kas_sha_is_refused(self):
        with self.assertRaises(ValueError):
            validated_kas_base_overlay(kas_ref="a" * 40)
        with self.assertRaises(ValueError):
            self.validate_changed(lambda d: d.update(for_kas_ref="b" * 40))

    def test_missing_transitive_library_fails_closed(self):
        with self.assertRaises(ValueError):
            self.validate_changed(
                lambda d: d["packages"].__setitem__(slice(None),
                    [r for r in d["packages"] if r["Package"] != "libcurl"]))

    def test_wrong_abi_and_wrong_origin_are_rejected(self):
        for change in ("arch", "url"):
            def mutate(d):
                row = d["packages"][0]
                if change == "arch":
                    row["Architecture"] = "aarch64-3.10"
                else:
                    row["url"] = "https://example.invalid/" + row["Filename"]
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.validate_changed(mutate)

    def test_broken_digest_is_rejected(self):
        with self.assertRaises(ValueError):
            self.validate_changed(lambda d: d["packages"][0].update(
                SHA256sum="not-a-valid-digest"))

    def test_unrelated_package_is_rejected_even_with_valid_metadata(self):
        def mutate(d):
            row = copy.deepcopy(d["packages"][0])
            row["Package"] = "unrelated-package"
            row["Filename"] = "unrelated-package_" + row["Version"] + "_all.ipk"
            row["url"] = d["origin"] + row["Filename"]
            d["packages"].append(row)
        with self.assertRaises(ValueError):
            self.validate_changed(mutate)

    def test_missing_base_requirement_is_rejected(self):
        with self.assertRaises(ValueError):
            self.validate_changed(lambda d: d["packages"].__setitem__(slice(None),
                [r for r in d["packages"] if r["Package"] != "jq"]))

    def test_rejects_dependency_syntax_drift(self):
        def mutate(d):
            next(r for r in d["packages"] if r["Package"] == "curl")[
                "Depends"] = "libc, libcurl | libfake"
        with self.assertRaises(ValueError):
            self.validate_changed(mutate)

    def test_all_filenames_sizes_and_pins_are_literal(self):
        good = validated_kas_base_overlay()
        for row in good["packages"]:
            self.assertTrue(row["url"].endswith("/" + row["Filename"]))
            self.assertEqual(len(row["SHA256sum"]), 64)
            self.assertLessEqual(int(row["Size"]), 5_000_000)
            self.assertEqual(row["feed"], "entware")


if __name__ == "__main__":
    unittest.main()

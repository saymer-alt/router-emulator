"""Host-only negative tests for one SHA256-pinned MIPSel Mihomo IPK."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from lab.mt7621.kas_mihomo_deps import (
    MIHOMO_LOCK, MIHOMO_VERSION, MIHOMO_SIZE,
    validated_kas_mihomo_overlay,
)


class MT7621MihomoOverlayTests(unittest.TestCase):
    def setUp(self):
        self.model = json.loads(MIHOMO_LOCK.read_text(encoding="utf8"))

    def changed(self, change):
        obj = copy.deepcopy(self.model)
        change(obj)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mihomo.json"
            path.write_text(json.dumps(obj), encoding="utf8")
            return validated_kas_mihomo_overlay(mihomo_path=path)

    def test_exact_single_ipk_and_37_package_closure(self):
        valid = validated_kas_mihomo_overlay()
        self.assertEqual(len(valid["packages"]), 1)
        self.assertEqual(valid["packages"][0]["Package"], "mihomo")
        self.assertEqual(valid["packages"][0]["Size"], str(MIHOMO_SIZE))
        self.assertEqual(valid["packages"][0]["Version"], MIHOMO_VERSION)

    def test_unknown_ref_denied(self):
        with self.assertRaises(ValueError):
            validated_kas_mihomo_overlay(kas_ref="f" * 40)

    def test_bad_digest_and_wrong_arch_denied(self):
        for field, value in (("SHA256sum", "f" * 64),
                             ("Architecture", "aarch64-3.10"),
                             ("Size", str(MIHOMO_SIZE - 1)),
                             ("Depends", "libc"),
                             ("Version", "0.0.0-1")):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.changed(lambda x, f=field, v=value: x["packages"][0].__setitem__(f, v))

    def test_url_and_filename_are_pinned(self):
        for field, value in (("url", "https://example.invalid/mihomo.ipk"),
                             ("Filename", "another.ipk")):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.changed(lambda x, f=field, v=value: x["packages"][0].__setitem__(f, v))

    def test_extra_or_missing_packages_denied(self):
        for arr in ([], self.model["packages"] * 2):
            with self.subTest(count=len(arr)), self.assertRaises(ValueError):
                self.changed(lambda x, a=arr: x.__setitem__("packages", a))

    def test_unexpected_keys_or_feed_denied(self):
        with self.assertRaises(ValueError):
            self.changed(lambda x: x["packages"][0].update(debug=True))
        with self.assertRaises(ValueError):
            self.changed(lambda x: x["packages"][0].update(feed="keenetic"))

    def test_drift_in_origin_schema_version_denied(self):
        for key, value in (("origin", "https://host.invalid/"),
                           ("schema", "version2"),
                           ("expected_package_version", "3.0"),
                           ("for_kas_ref", "0" * 40)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.changed(lambda x, k=key, v=value: x.__setitem__(k, v))


if __name__ == "__main__":
    unittest.main()

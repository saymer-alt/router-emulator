"""Cheap, offline regression tests for Entware mirror dependency completeness."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lab import build_entware as entware


class MirrorClosureTests(unittest.TestCase):
    def test_both_committed_architecture_locks_are_closed(self):
        for filename in ("entware.lock.json", "entware-mipselsf.lock.json"):
            with self.subTest(lock=filename):
                lock = json.loads(Path(entware.__file__).with_name(filename).read_text())
                entware.validate_offline_dependency_closure(lock["packages"])

    def test_missing_libstdcpp_refused_before_download_or_image(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            lock_path = root / "broken.json"
            lock_path.write_text(json.dumps({
                "installer": {"url": "https://invalid.example/test", "sha256": "0" * 64},
                "packages": [
                    {"Package": "opt-ndmsv2", "Depends": "libc, libstdcpp"},
                    {"Package": "libc"},
                ],
            }))
            image, mirror, cache = root / "disk.img", root / "mirror", root / "cache"
            with patch.object(entware, "fetch", side_effect=AssertionError("network fetch attempted")):
                with self.assertRaisesRegex(ValueError, "opt-ndmsv2 missing libstdcpp"):
                    entware.build(image, mirror, cache, root / "no-credentials", arch="mipsel", lock_path=lock_path)
            self.assertFalse(image.exists())
            self.assertFalse(mirror.exists())
            self.assertFalse(cache.exists())

    def test_duplicate_package_refused(self):
        with self.assertRaisesRegex(ValueError, "duplicate package"):
            entware.validate_offline_dependency_closure([
                {"Package": "libc"}, {"Package": "libc"},
            ])

    def test_alternative_or_version_dependency_does_not_silently_pass(self):
        for dep in ("libc (>= 2.27)", "libc | musl"):
            with self.subTest(dep=dep):
                with self.assertRaisesRegex(ValueError, "unsupported offline dependency"):
                    entware.validate_offline_dependency_closure([
                        {"Package": "tool", "Depends": dep},
                        {"Package": "libc"},
                    ])

    def test_valid_plain_dependencies_pass(self):
        entware.validate_offline_dependency_closure([
            {"Package": "tool", "Depends": "libc, libstdcpp"},
            {"Package": "libc"},
            {"Package": "libstdcpp"},
        ])


if __name__ == "__main__":
    unittest.main()

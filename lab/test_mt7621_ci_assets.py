"""Offline, fail-closed tests of MT7621 cloud asset selection."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from lab.mt7621 import ci_assets


class Mt7621CloudAssetsTests(unittest.TestCase):
    def test_select_by_sha256_not_filename(self):
        data = {"uconfig": b"A" * 11, "ustate": b"B" * 11,
                "eeprom": b"C" * 12}
        expected = {k: (hashlib.sha256(v).hexdigest(), len(v))
                    for k, v in data.items()}
        with tempfile.TemporaryDirectory() as tmp, patch.dict(ci_assets.EXPECTED, expected):
            root = Path(tmp)
            for n, (key, payload) in enumerate(data.items()):
                path = root / "extract" / str(n) / "unrelated.dat"
                path.parent.mkdir(parents=True)
                path.write_bytes(payload)
            result = ci_assets.stage(root / "extract", root / "parts")
            self.assertEqual({key: (result / (key + ".bin")).read_bytes()
                              for key in data}, data)

    def test_tampered_archive_part_is_rejected(self):
        data = {"uconfig": b"A" * 11, "ustate": b"B" * 11,
                "eeprom": b"C" * 12}
        expected = {k: (hashlib.sha256(v).hexdigest(), len(v))
                    for k, v in data.items()}
        with tempfile.TemporaryDirectory() as tmp, patch.dict(ci_assets.EXPECTED, expected):
            root = Path(tmp) / "extract"
            root.mkdir()
            for key, payload in data.items():
                (root / key).write_bytes(payload)
            (root / "ustate").write_bytes(b"X" * 11)
            with self.assertRaisesRegex(ValueError, "ustate"):
                ci_assets.locate(root)

    def test_existing_output_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "input").mkdir()
            (root / "output").mkdir()
            with self.assertRaisesRegex(ValueError, "already exists"):
                ci_assets.stage(root / "input", root / "output")

    def test_archive_symlink_is_refused(self):
        data = {"uconfig": b"A" * 11, "ustate": b"B" * 11,
                "eeprom": b"C" * 12}
        expected = {k: (hashlib.sha256(v).hexdigest(), len(v))
                    for k, v in data.items()}
        with tempfile.TemporaryDirectory() as tmp, patch.dict(ci_assets.EXPECTED, expected):
            root = Path(tmp)
            (root / "original").write_bytes(data["uconfig"])
            src = root / "extract"
            src.mkdir()
            (src / "uconfig").symlink_to(root / "original")
            (src / "ustate").write_bytes(data["ustate"])
            (src / "eeprom").write_bytes(data["eeprom"])
            with self.assertRaisesRegex(ValueError, "uconfig"):
                ci_assets.locate(src)

import hashlib
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock

from lab.mt7621 import prepare_ported_mir3g as prep


def sha256_of(data):
    return hashlib.sha256(data).hexdigest()


class BreedEnvBlobTests(unittest.TestCase):
    def test_blob_layout_and_crc(self):
        blob = prep.breed_env_blob()
        self.assertEqual(len(blob), prep.ENV_SIZE)
        crc, body = struct.unpack("<I", blob[:4])[0], blob[4:]
        self.assertEqual(crc, zlib.crc32(body) & 0xFFFFFFFF)
        self.assertTrue(body.startswith(b"autoboot.command=boot flash 0x180000\x00\x00"))
        self.assertTrue(body.endswith(b"\x00" * 16))

    def test_empty_environment_crc_matches_breed_emission(self):
        # Breed's own flash write of an empty env emitted CRC32(4092 zeros)
        # on the UART write path; our CRC formula must reproduce it.
        self.assertEqual(zlib.crc32(bytes(4092)) & 0xFFFFFFFF, 0x603b0489)


class AssembleValidationTests(unittest.TestCase):
    """Validation refusals run on tiny synthetic fixtures with a shrunken
    layout, so no proprietary component is needed."""

    BREED_SIZE = 137250
    FW_SIZE = 0x40000

    @staticmethod
    def tiny_layout():
        # 9 parts, totals 0x240000; env offset shrunk accordingly
        return [(0x00000, 0x30000), (0x30000, 0x30000), (0x60000, 0x30000),
                (0x90000, 0x40000), (0xd0000, 0x30000), (0x100000, 0x30000),
                (0x130000, 0x30000), (0x160000, 0x40000), (0x1a0000, 0x40000)]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.src = root / "src"
        self.src.mkdir()
        blobs = {
            "breed": bytes.fromhex("27051956") + b"\x00" * (self.BREED_SIZE - 4),
            "firmware": bytes.fromhex("27051956") + b"\x00" * 28 + b"KN" +
                        b"\x00" * (self.FW_SIZE - 34),
            "uconfig": b"\x00" * 512,
            "ustate": b"\x00" * 512,
            "eeprom": b"\x00" * 512,
        }
        self.paths = {}
        for name, data in blobs.items():
            p = self.src / (name + ".bin")
            p.write_bytes(data)
            self.paths[name] = str(p)
        self.expected = {
            name: (sha256_of(data), len(data)) for name, data in blobs.items()
        }
        self.patches = [
            mock.patch.object(prep, "EXPECTED", self.expected),
            mock.patch.object(prep, "PARTS", self.tiny_layout()),
            mock.patch.object(prep, "TOTAL", sum(s for _, s in self.tiny_layout())),
            mock.patch.object(prep, "ENV_OFFSET", 0x30000),
            mock.patch.object(prep, "ENV_SIZE", 0x1000),
            mock.patch.object(prep, "FIRMWARE_SLOT_OFFSET", 0x90000),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def assemble(self, out="out"):
        return prep.assemble(self.paths["breed"], self.paths["firmware"],
                             self.paths["uconfig"], self.paths["ustate"],
                             self.paths["eeprom"], Path(self.tmp.name) / out)

    def test_happy_path_assembles_and_persists_env_blob(self):
        out = self.assemble()
        files = sorted(out.iterdir())
        self.assertEqual(len(files), 9)
        local = prep.ENV_OFFSET - prep.PARTS[1][0]
        env = (out / "mt7621.mtd1.U-Config.bin").read_bytes()[local:local + prep.ENV_SIZE]
        crc = struct.unpack("<I", env[:4])[0]
        self.assertEqual(crc, zlib.crc32(env[4:]) & 0xFFFFFFFF)
        fw1 = (out / "mt7621.mtd3.Firmware1.bin").read_bytes()[:self.FW_SIZE]
        fw2 = (out / "mt7621.mtd7.Firmware2.bin").read_bytes()[:self.FW_SIZE]
        self.assertEqual(fw1, fw2)
        bl = (out / "mt7621.mtd0.Bootloader.bin").read_bytes()[:self.BREED_SIZE]
        self.assertEqual(bl[:4], bytes.fromhex("27051956"))

    def test_wrong_size_refused(self):
        p = Path(self.paths["firmware"])
        data = bytearray(p.read_bytes())
        p.write_bytes(bytes(data[:-1]))
        with self.assertRaises(ValueError):
            self.assemble()

    def test_wrong_hash_refused(self):
        p = Path(self.paths["firmware"])
        data = bytearray(p.read_bytes())
        data[40] ^= 0xFF
        p.write_bytes(bytes(data))
        with self.assertRaises(ValueError):
            self.assemble()

    def test_missing_file_refused(self):
        Path(self.paths["eeprom"]).unlink()
        with self.assertRaises(ValueError):
            self.assemble()

    def test_wrong_firmware_magic_refused(self):
        expected = dict(self.expected)
        bad = bytes.fromhex("deadbeef") + bytes(self.FW_SIZE - 4)
        p = self.src / "fake.bin"
        p.write_bytes(bad)
        expected["firmware"] = (sha256_of(bad), self.FW_SIZE)
        with mock.patch.object(prep, "EXPECTED", expected):
            with self.assertRaises(ValueError):
                prep.assemble(self.paths["breed"], str(p), self.paths["uconfig"],
                              self.paths["ustate"], self.paths["eeprom"],
                              Path(self.tmp.name) / "out2")

    def test_existing_output_refused(self):
        out = self.assemble()
        with self.assertRaises(ValueError):
            prep.assemble(self.paths["breed"], self.paths["firmware"],
                          self.paths["uconfig"], self.paths["ustate"],
                          self.paths["eeprom"], out)


if __name__ == "__main__":
    unittest.main()

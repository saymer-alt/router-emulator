"""Synthetic NAND regression: no guest firmware or network required."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lab import prepare_ported_wr3000p as builder
from lab import ported_evidence


class PortedBuilderTests(unittest.TestCase):
    def test_uart_output_is_allowlisted_not_redacted_raw_text(self):
        raw = ('secret password token serial: sensitive\n'
               '*** Image verification failed *** password=sensitive\n'
               'Core::Main: *** Network Device Manager 5.01.C.8.0-2. secret\n')
        self.assertEqual(ported_evidence.evidence(raw), [
            'Network Device Manager 5.01.C.8.0-2',
            'ERROR: *** Image verification failed ***'])

    def test_physical_layout_pins_and_source_isolation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            stock = root / "stock"
            stock.mkdir()
            sizes = list(builder.PARTS.values())
            sizes.append(builder.TOTAL - sum(sizes))
            sources = []
            for n, size in enumerate(sizes):
                p = stock / ("mt7981.mtd%d.test.bin" % n)
                with p.open("wb") as fh:
                    fh.truncate(size)  # zero-filled poison, must not be copied
                sources.append(p)
            bl2 = b"synthetic-BL2" + b"\xff" * (sizes[0] - 13)
            sources[0].write_bytes(bl2)
            boot = bytes.fromhex("010064aa") + b"synthetic-FIP" * 200
            image = bytes.fromhex("d00dfeed") + b"FIT-and-rootfs" * 100000
            (root / "boot").write_bytes(boot)
            (root / "image").write_bytes(image)
            expected = {key: (hashlib.sha256(data).hexdigest(), len(data))
                        for key, data in (("ported", image), ("keenboot", boot))}
            out = root / "out"
            with patch.object(builder, "EXPECTED", expected):
                builder.prepare(stock, root / "image", root / "boot", out)
                with self.assertRaisesRegex(ValueError, "no overwrite"):
                    builder.prepare(stock, root / "image", root / "boot", out)
                (root / "boot").write_bytes(boot[:-1] + b"x")
                with self.assertRaisesRegex(ValueError, "pinned KeenBOOT KB-41"):
                    builder.prepare(stock, root / "image", root / "boot", root / "bad")
            # Rejoin only in the test: firmware crosses the 0x5c0000 backing
            # file boundary and MUST start at 0x500000, not backing mtd5.
            data = b"".join((out / p.name).read_bytes() for p in sources)
            self.assertEqual(len(data), builder.TOTAL)
            for offset, payload in ((0, bl2[:builder.FIP_OFFSET]),
                                    (builder.FIP_OFFSET, boot),
                                    (builder.BL2_FIP_OFFSET, boot),
                                    (builder.FIRMWARE_OFFSET, image)):
                self.assertEqual(data[offset:offset + len(payload)], payload)
            self.assertEqual(data[0x280000:0x300000], b"\xff" * 0x80000)
            self.assertEqual(data[builder.NMBM_START:], b"\xff" * 0x800000)
            self.assertEqual(sources[0].read_bytes(), bl2)
            self.assertEqual(sources[2].read_bytes()[:100], b"\x00" * 100)
            self.assertFalse((root / "bad").exists())

    def test_write_outside_flash_rejected(self):
        with self.assertRaises(ValueError):
            builder.write_at([], builder.TOTAL, b"x")


if __name__ == "__main__":
    unittest.main()

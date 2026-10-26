import tempfile
import unittest
from pathlib import Path
from lab.run import storage_args


class KNVRStorageTests(unittest.TestCase):
    def test_separate_disposable_archive_and_opt(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / 'source.img'
            source.write_bytes(b'synthetic image')
            work = root / 'work'
            work.mkdir()
            opt = storage_args(source, work, 'labopt')
            archive = storage_args(source, work, 'labarchive')
            self.assertNotEqual(opt, archive)
            self.assertEqual((work / 'labarchive.img').read_bytes(), b'synthetic image')
            (work / 'labarchive.img').write_bytes(b'changed by guest')
            self.assertEqual(source.read_bytes(), b'synthetic image')

    def test_archive_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'real.img').write_bytes(b'x')
            (root / 'link.img').symlink_to(root / 'real.img')
            with self.assertRaises(ValueError):
                storage_args(root / 'link.img', root, 'labarchive')

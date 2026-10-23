from pathlib import Path
import tempfile
import unittest
import urllib.error
import urllib.request
from lab.mirror import Mirror


class MirrorTests(unittest.TestCase):
    def test_loopback_only_and_no_directory_or_parent_access(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for feed in ('entware', 'keenetic'):
                (root / feed).mkdir()
                (root / feed / 'Packages.gz').write_bytes(b'pinned-index')
            (root / 'private-secret').write_bytes(b'must-not-be-served')
            with Mirror(root) as port:
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with opener.open('http://127.0.0.1:%d/entware/Packages.gz' % port) as response:
                    self.assertEqual(response.read(), b'pinned-index')
                for path in ('/', '/private-secret', '/entware/../private-secret', '/entware/Packages.gz?extra=1'):
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        opener.open('http://127.0.0.1:%d%s' % (port, path))
                    self.assertEqual(error.exception.code, 404)
                    error.exception.close()

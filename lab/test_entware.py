import io
import pathlib
import tarfile
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from lab import build_entware as entware


class EntwareTests(unittest.TestCase):
    def test_bootstrap_drops_reused_keys_and_sets_private_password_before_ssh(self):
        with tempfile.TemporaryDirectory() as td:
            source, target = pathlib.Path(td) / 'source.tar.gz', pathlib.Path(td) / 'target.tar.gz'
            members = {'etc/dropbear/dropbear_rsa_host_key': b'public-reused-private-key',
                       'etc/config/dropbear.conf': b'PORT=222\nPIDFILE="/opt/var/run/dropbear.pid"\n',
                       'etc/opkg.conf': b'upstream-feed',
                       'bin/install': b'/opt/etc/init.d/S51dropbear start\nif grep -q "222"; then\necho default-secret\nfi\n'}
            with tarfile.open(source, 'w:gz') as archive:
                for name, data in members.items():
                    item = tarfile.TarInfo(name)
                    item.size = len(data)
                    archive.addfile(item, io.BytesIO(data))
            with patch.object(entware.subprocess, 'run', return_value=SimpleNamespace(stdout=b'$6$synthetic-hash\n')) as command:
                entware.bootstrap_archive(source, target, 'PrivateSyntheticToken0123456789')
            self.assertNotIn('PrivateSyntheticToken0123456789', repr(command.call_args.args))
            with tarfile.open(target) as archive:
                self.assertNotIn('etc/dropbear/dropbear_rsa_host_key', archive.getnames())
                passwd = archive.extractfile('etc/.lab-passwd').read()
                self.assertIn(b'root:$6$synthetic-hash:', passwd)
                self.assertEqual(archive.getmember('etc/.lab-passwd').mode, 0o600)
                script = archive.extractfile('bin/install').read()
                self.assertLess(script.index(b'cat /opt/etc/.lab-passwd'), script.index(b'S51dropbear start'))
                self.assertNotIn(b'default-secret', script)
                config = archive.extractfile('etc/opkg.conf').read()
                self.assertIn(b'http://192.168.1.100/entware', config)
                self.assertNotIn(b'check_signature 0', config)
                ssh = archive.extractfile('etc/config/dropbear.conf').read()
                self.assertIn(b'PIDFILE="/var/run/lab-dropbear.pid"', ssh)
                self.assertNotIn(b'/opt/var/run', ssh)

    def test_cached_asset_digest_and_origin_are_enforced(self):
        with tempfile.TemporaryDirectory() as td:
            asset = pathlib.Path(td) / 'asset'
            asset.write_bytes(b'wrong')
            for origin in ('https://example.com/payload', 'https://bin.entware.net/aarch64-k3.10/payload'):
                with self.assertRaises(ValueError):
                    entware.fetch(origin, '0' * 64, asset)

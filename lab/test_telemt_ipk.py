"""Host-only tests for QEMU TeleMT IPK fixtures; no network, QEMU or opkg."""
import io
import json
import tarfile
import unittest

from lab.telemt_ipk_e2e import (
    CURL_FIXTURE, PKGS, binary_digest, metadata, release_json, sha256,
    synthetic_upgrade, tar_member_bytes,
)


def tgz(entries):
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode='w:gz') as tf:
        for name, data in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            tf.addfile(info, io.BytesIO(data))
    return out.getvalue()


def ipk(version='3.5.17-1', package='telemt', architecture='aarch64-3.10'):
    ctl = tgz({'./control': (
        'Package: ' + package + '\nVersion: ' + version +
        '\nArchitecture: ' + architecture + '\n').encode()})
    data = tgz({'./opt/bin/telemt': b'fixture ELF bytes'})
    return tgz({'./debian-binary': b'2.0\n',
                './control.tar.gz': ctl, './data.tar.gz': data})


class TelemtIPKFixtures(unittest.TestCase):
    def test_original_control_and_payload(self):
        src = ipk()
        self.assertEqual(metadata(src), {
            'Package': 'telemt', 'Version': '3.5.17-1',
            'Architecture': 'aarch64-3.10'
        })
        self.assertEqual(binary_digest(src, 'telemt'), sha256(b'fixture ELF bytes'))

    def test_synthetic_upgrade_changes_only_package_version(self):
        src = ipk()
        nxt = synthetic_upgrade(src, '3.5.17-1', '3.5.18-1')
        self.assertEqual(metadata(nxt)['Version'], '3.5.18-1')
        self.assertEqual(tar_member_bytes(src, 'data.tar.gz'),
                         tar_member_bytes(nxt, 'data.tar.gz'))
        self.assertEqual(binary_digest(src, 'telemt'),
                         binary_digest(nxt, 'telemt'))

    def test_reject_unexpected_original_version(self):
        with self.assertRaises(ValueError):
            synthetic_upgrade(ipk(), '3.5.16-1', '3.5.18-1')

    def test_reject_ambiguous_control(self):
        src = tgz({'./control.tar.gz': tgz({
            './control': b'Package: telemt\nVersion: 3.5.17-1\n'
                         b'Version: 3.5.17-1\nArchitecture: aarch64-3.10\n'
        }), './data.tar.gz': tgz({'./opt/bin/telemt': b'bytes'})})
        with self.assertRaises(ValueError):
            metadata(src)

    def test_release_json_is_public_and_immutable_filename(self):
        pkg, old, new, name, synthetic, digest, binary = PKGS['core']
        manifest = release_json({'core': {
            'name': name, 'digest': digest, 'version': old,
        }})
        obj = json.loads(manifest)
        self.assertEqual(obj['tag_name'], 'latest')
        self.assertEqual(obj['assets'][0]['digest'], 'sha256:' + digest)
        self.assertTrue(obj['assets'][0]['browser_download_url'].endswith(name))
        self.assertNotIn('127.0.0.1', manifest.decode())
        self.assertNotIn('192.168.', manifest.decode())

    def test_mock_curl_has_explicit_host_allowlist(self):
        self.assertIn('https://api.github.com/repos/saymer-alt/entware-go/releases/tags/latest', CURL_FIXTURE)
        self.assertIn('*) exit 72', CURL_FIXTURE)


if __name__ == '__main__':
    unittest.main()

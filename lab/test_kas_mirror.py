from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from lab.kas_mirror import KASMirror, load_sources


class KASDeliveryTests(unittest.TestCase):
    def test_committed_sources_remain_data_and_tls_checks_hostname(self):
        if not shutil.which('openssl'):
            self.skipTest('OpenSSL required for isolated TLS contract')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            source.mkdir()
            payload = b'#!/bin/sh\nprintf source-only\n'
            (source / 'install.sh').write_bytes(payload)
            subprocess.run(['git', 'init', '-q', str(source)], check=True, capture_output=True)
            subprocess.run(['git', '-C', str(source), 'add', 'install.sh'], check=True)
            subprocess.run(['git', '-C', str(source), '-c', 'user.name=fixture', '-c', 'user.email=fixture@invalid',
                            'commit', '-qm', 'synthetic source fixture'], check=True, capture_output=True)
            revision = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD']).decode().strip()
            self.assertEqual(load_sources(source, revision)['install.sh'], payload)
            with self.assertRaises(ValueError):
                load_sources(source, '0' * 40)
            with patch('lab.kas_mirror.assets', return_value={}):
                mirror = KASMirror(source, revision, root, root)
            with mirror as port:
                context = ssl.create_default_context(cafile=str(mirror.private / 'ca.pem'))
                with socket.create_connection(('127.0.0.1', port), timeout=5) as raw:
                    with context.wrap_socket(raw, server_hostname='raw.githubusercontent.com') as connection:
                        connection.sendall(('GET /saymer-alt/keenetic-auto-setup/' + revision +
                                            '/install.sh HTTP/1.0\r\nHost: raw.githubusercontent.com\r\n\r\n').encode())
                        response = b''
                        while True:
                            data = connection.recv(4096)
                            if not data:
                                break
                            response += data
                        self.assertIn(b'200 OK', response)
                        self.assertTrue(response.endswith(payload))
                with socket.create_connection(('127.0.0.1', port), timeout=5) as raw:
                    with self.assertRaises(ssl.SSLCertVerificationError):
                        context.wrap_socket(raw, server_hostname='unexpected.invalid')

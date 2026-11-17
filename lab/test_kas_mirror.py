import hashlib
import http.server
import json
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import unittest
import urllib.request
from unittest.mock import patch

import lab.kas_mirror as kas_mirror_module
from lab.kas_mirror import KASMirror, load_sources


class KASDependencyFetchTests(unittest.TestCase):
    def test_terminal_http_error_fails_closed_and_names_the_url(self):
        """A 404 from a pinned origin cannot escape anonymously (run 43270179009
        died on an unnamed 404 that required a manual bisect to attribute).

        Drives the real fetch loop against a scripted loopback origin; only the
        approved-host allowlist is repointed, mirroring the test_entware
        TRUSTED_ORIGIN pattern. The opener is bound to a fixed unverified
        context instead of the ssl default switch: urlopen caches its global
        opener on first use, so a sibling test may have built the real one."""
        if not shutil.which('openssl'):
            self.skipTest('OpenSSL required for the loopback TLS origin')

        class Missing(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_error(404)

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Missing)
        with tempfile.TemporaryDirectory() as certdir:
            subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                            '-keyout', str(Path(certdir) / 'key.pem'),
                            '-out', str(Path(certdir) / 'cert.pem'), '-days', '2',
                            '-subj', '/CN=127.0.0.1'], check=True, capture_output=True)
            server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            server_context.load_cert_chain(Path(certdir) / 'cert.pem', Path(certdir) / 'key.pem')
            server.socket = server_context.wrap_socket(server.socket, server_side=True)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f'https://127.0.0.1:{server.server_address[1]}/missing.ipk'
                record = {'fetch_url': url, 'filename': 'missing.ipk',
                          'sha256': hashlib.sha256(b'x').hexdigest()}
                opener = urllib.request.build_opener(urllib.request.HTTPSHandler(
                    context=ssl._create_unverified_context()))
                with tempfile.TemporaryDirectory() as td:
                    cache = Path(td) / 'cache'
                    lock_path = Path(td) / 'kas-assets.lock.json'
                    lock_path.write_text(json.dumps({
                        'schema': 1, 'kas_sha': '0' * 40,
                        'uninstall_no_remove_sha256': '0' * 64,
                        'uninstall_inventory_s28_sha256': '0' * 64,
                        'mihomo_version': '0', 'magitrickle_version': '0',
                        'assets': [record]}))
                    real_path = kas_mirror_module.Path

                    def fake_path(arg):
                        target = real_path(arg)
                        lock_reader = real_path(lock_path)

                        class LockReplacer:
                            def __truediv__(self, other):
                                return target / other

                            def with_name(self, name):
                                if name == 'kas-assets.lock.json':
                                    return lock_reader
                                return target.with_name(name)

                            def __getattr__(self, item):
                                return getattr(target, item)

                        return LockReplacer()

                    def scripted_urlopen(request, timeout=None):
                        return opener.open(request, timeout=timeout) if timeout else opener.open(request)

                    with patch.object(kas_mirror_module, 'Path', fake_path), \
                            patch.object(kas_mirror_module, 'APPROVED_ASSET_HOSTS', ('127.0.0.1',)), \
                            patch('urllib.request.urlopen', scripted_urlopen), \
                            patch.object(kas_mirror_module.time, 'sleep'):
                        with self.assertRaises(ValueError) as raised:
                            kas_mirror_module.assets(cache)
                self.assertIn(url, str(raised.exception))
                self.assertIn('404', str(raised.exception))
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)


class KASMirrorAssetRecordsTests(unittest.TestCase):
    """Explicit record lists replace the shared aarch64 lock (MIPSel flow)."""

    def _fixture_repo(self, root):
        source = root / 'source'
        source.mkdir()
        (source / 'install.sh').write_bytes(b'#!/bin/sh\nprintf source-only\n')
        subprocess.run(['git', 'init', '-q', str(source)], check=True, capture_output=True)
        subprocess.run(['git', '-C', str(source), 'add', 'install.sh'], check=True)
        subprocess.run(['git', '-C', str(source), '-c', 'user.name=fixture',
                        '-c', 'user.email=fixture@invalid', 'commit', '-qm', 'fixture'],
                       check=True, capture_output=True)
        return source, subprocess.check_output(
            ['git', '-C', str(source), 'rev-parse', 'HEAD']).decode().strip()

    def test_explicit_records_serve_pinned_bytes_and_reject_tamper(self):
        if not shutil.which('openssl'):
            self.skipTest('OpenSSL required for isolated TLS contract')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, revision = self._fixture_repo(root)
            payload = b'pinned-helper-bytes'
            record = {'url': 'https://bin.magitrickle.dev/packages/add_repo.sh',
                      'fetch_url': 'https://bin.magitrickle.dev/packages/add_repo.sh',
                      'filename': 'helper.sh',
                      'sha256': hashlib.sha256(payload).hexdigest()}
            cache = root / 'cache'
            cache.mkdir()
            (cache / 'helper.sh').write_bytes(payload)
            mirror = KASMirror(source, revision, root, cache,
                               asset_records=[record])
            self.assertEqual(mirror.routes[
                ('https', 'bin.magitrickle.dev', '/packages/add_repo.sh')], payload)
            self.assertEqual(mirror.http_routes, {})
            with mirror as port:
                context = ssl.create_default_context(cafile=str(mirror.private / 'ca.pem'))
                with socket.create_connection(('127.0.0.1', port), timeout=5) as raw:
                    with context.wrap_socket(raw, server_hostname='bin.magitrickle.dev') as conn:
                        conn.sendall(b'GET /packages/add_repo.sh HTTP/1.0\r\n'
                                     b'Host: bin.magitrickle.dev\r\n\r\n')
                        response = b''
                        while True:
                            chunk = conn.recv(4096)
                            if not chunk:
                                break
                            response += chunk
                        self.assertTrue(response.endswith(payload))
            (cache / 'helper.sh').write_bytes(b'tampered')
            with self.assertRaises(ValueError):
                KASMirror(source, revision, root, cache, asset_records=[record])

    def test_fixture_records_route_without_network(self):
        fixture = Path(__file__).with_name('fixtures') / 'entware-go-release.json'
        data = fixture.read_bytes()
        record = {'url': 'http://bin.magitrickle.dev/packages/entware/'
                         'mipsel-3.4_kn/Packages.gz',
                  'fixture': 'fixtures/entware-go-release.json',
                  'filename': 'index.gz',
                  'sha256': hashlib.sha256(data).hexdigest()}
        routes = kas_mirror_module.assets_from_records([record], 'cache-ignored')
        self.assertEqual(routes[('http', 'bin.magitrickle.dev',
                                 '/packages/entware/mipsel-3.4_kn/Packages.gz')], data)


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

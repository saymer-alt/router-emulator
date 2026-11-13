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
                       'etc/opkg.conf': b'upstream-feed',
                       # Mirror the real pinned installer tail: the saving RCI
                       # POST, the dropbear-port hint block and the completion
                       # marker must all be present for the lab reorder patch.
                       'bin/install': b'opkg install dropbear\n/opt/etc/init.d/S51dropbear start\n'
                                      b"wget -qO - --post-data='[{\"system\":{\"configuration\":{\"save\":true}}}]' localhost:79/rci/ >/dev/null 2>&1\n"
                                      b'if grep -q "222"; then\necho default-secret\nfi\n'
                                      b"echo '[5/5] \"Entware\" installed!'\nrm /opt/etc/init.d/doinstall\nrm \"$0\"\n"}
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
                # Match the real pinned archive: the package, not bootstrap
                # tar members, supplies this file. The override must follow it.
                self.assertNotIn('etc/config/dropbear.conf', archive.getnames())
                self.assertIn(b'PIDFILE="/var/run/lab-dropbear.pid"', script)
                self.assertLess(script.index(b'opkg install dropbear'), script.index(b'PIDFILE='))
                self.assertLess(script.index(b'PIDFILE='), script.index(b'S51dropbear start'))

    def test_cached_asset_digest_and_origin_are_enforced(self):
        with tempfile.TemporaryDirectory() as td:
            asset = pathlib.Path(td) / 'asset'
            asset.write_bytes(b'wrong')
            for origin in ('https://example.com/payload', 'https://bin.entware.net/aarch64-k3.10/payload'):
                with self.assertRaises(ValueError):
                    entware.fetch(origin, '0' * 64, asset)

    def _fetch_from_script(self, script):
        """Drive fetch() against a local scripted origin; offline and bounded.

        The trusted-origin allowlist is repointed to the loopback server, so
        the real retry/digest/fail-closed code path runs unchanged.
        """
        import http.server
        import threading

        class Scripted(http.server.BaseHTTPRequestHandler):
            served = []

            def do_GET(self):
                index = len(Scripted.served)
                Scripted.served.append(self.path)
                status, body = script[index] if index < len(script) else script[-1]
                self.send_response(status)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Scripted)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            origin = f'http://127.0.0.1:{server.server_address[1]}/aarch64-k3.10'
            with tempfile.TemporaryDirectory() as td:
                destination = pathlib.Path(td) / 'payload'
                with patch.object(entware, 'TRUSTED_ORIGIN', origin), \
                        patch.object(entware.time, 'sleep'):
                    payload = entware.fetch(f'{origin}/payload.bin', self.digest, destination)
                    return payload.read_bytes(), destination.read_bytes()
        finally:
            server.shutdown()
            server.server_close()

    def setUp(self):
        import hashlib
        self.payload = b'pinned-payload'
        self.digest = hashlib.sha256(self.payload).hexdigest()

    def test_transient_404_recovers_within_two_bounded_retries(self):
        payload, destination = self._fetch_from_script(
            [(404, b'gone'), (404, b'gone'), (200, self.payload)])
        self.assertEqual(payload, self.payload)
        self.assertEqual(destination, self.payload)

    def test_persistent_404_fails_closed_after_two_retries(self):
        with self.assertRaises(ValueError) as raised:
            self._fetch_from_script([(404, b'gone')] * 5)
        self.assertIn('404', str(raised.exception))
        self.assertIn('/payload.bin', str(raised.exception))

    def test_other_client_errors_fail_fast_and_name_the_url(self):
        with self.assertRaises(ValueError) as raised:
            self._fetch_from_script([(403, b'no')] * 2)
        self.assertIn('HTTP 403', str(raised.exception))
        self.assertIn('/payload.bin', str(raised.exception))

    def test_server_errors_keep_the_existing_bounded_retry_budget(self):
        with self.assertRaises(ValueError) as raised:
            self._fetch_from_script([(503, b'busy')] * 6)
        self.assertIn('HTTP 503', str(raised.exception))

    def test_digest_mismatch_still_fails_closed_and_names_the_url(self):
        with self.assertRaises(ValueError) as raised:
            self._fetch_from_script([(200, b'tampered')])
        self.assertIn('download digest mismatch', str(raised.exception))
        self.assertIn('/payload.bin', str(raised.exception))

    def test_completion_markers_precede_the_saving_rci_post(self):
        """The 2026-11-09 installer's final RCI POST carries
        system.configuration.save=true; ndm's doinstaller console bridge stops
        relaying stdout while that save runs, so post-POST echoes never reach
        the UART even though the script completes (local reproduction
        2026-11-12 and run 43283357131: opkg database populated, both rm's
        executed, no [5/5] on console, 600s gate wait timed out). The lab must
        print the completion markers and drop the auto-install hook before the
        POST, keep the POST itself verbatim, and keep self-deletion last."""
        with tempfile.TemporaryDirectory() as td:
            source, target = pathlib.Path(td) / 'source.tar.gz', pathlib.Path(td) / 'target.tar.gz'
            install = (b'#!/bin/sh\n'
                       b'/opt/etc/init.d/S51dropbear start\n'
                       b'\n'
                       b"wget -qO - --post-data='[{\"opkg\":{\"initrc\":{\"path\":\"/opt/etc/init.d/rc.unslung\"}},{\"system\":{\"configuration\":{\"save\":true}}}]' localhost:79/rci/ >/dev/null 2>&1\n"
                       b'\n'
                       b'if grep -q "222" /opt/etc/config/dropbear.conf; then\n'
                       b"\techo 'Log on to start an SSH session using login - root, password - keenetic, port - 222.'\n"
                       b'  else\n'
                       b"\techo 'Log on to start an SSH session using login - root, password - keenetic.'\n"
                       b'fi\n'
                       b'\n'
                       b"echo '[5/5] \"Entware\" installed!'\n"
                       b'\n'
                       b'rm /opt/etc/init.d/doinstall\n'
                       b'\n'
                       b'rm "$0"\n')
            with tarfile.open(source, 'w:gz') as archive:
                item = tarfile.TarInfo('bin/install')
                item.size = len(install)
                archive.addfile(item, io.BytesIO(install))
            with patch.object(entware.subprocess, 'run', return_value=SimpleNamespace(stdout=b'$6$synthetic-hash\n')):
                entware.bootstrap_archive(source, target, 'PrivateSyntheticToken0123456789')
            with tarfile.open(target) as archive:
                script = archive.extractfile('bin/install').read()
            post = script.index(b'wget -qO - --post-data=')
            self.assertLess(script.index(b"echo '[5/5]"), post)
            self.assertLess(script.index(b'rm /opt/etc/init.d/doinstall'), post)
            self.assertIn(b"save\":true}}}]' localhost:79/rci/ >/dev/null 2>&1", script)
            self.assertGreater(script.index(b'rm "$0"'), post)
            self.assertNotIn(b'if grep -q "222"', script)
            # The relay bridge swallows stdout in the tail section, so the
            # gate marker must also land on the console device itself.
            self.assertIn(b"echo '[5/5] \"Entware\" installed!' > /dev/console", script)

"""Pinned local delivery of real KAS sources and upstream package snapshots.

No KAS code executes on the host. A short-lived lab CA preserves guest TLS
verification; its private key never enters the guest or public reports.
"""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import re
import ssl
import subprocess
import tarfile
import threading
import urllib.parse
import urllib.request

DOMAINS = ('raw.githubusercontent.com', 'api.github.com', 'github.com', 'bin.magitrickle.dev')


def load_sources(repository, revision):
    if not re.fullmatch('[a-f0-9]{40}', revision):
        raise ValueError('KAS source requires a full commit SHA')
    head = subprocess.check_output(['git', '-C', str(repository), 'rev-parse', 'HEAD']).decode().strip()
    if head != revision:
        raise ValueError('KAS checkout does not match the requested commit')
    archive = subprocess.check_output(['git', '-C', str(repository), 'archive', '--format=tar', revision], timeout=60)
    sources = {}
    # Read committed blobs in memory; never follow checkout symlinks or extract
    # untrusted PR files onto a host filesystem, and never source their scripts.
    with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
        for item in bundle:
            if item.isfile() and not item.name.startswith('/') and '..' not in item.name.split('/'):
                sources[item.name] = bundle.extractfile(item).read()
    if not sources.get('install.sh', b'').startswith(b'#!/bin/sh'):
        raise ValueError('actual KAS installer unavailable')
    return sources


def assets(cache):
    lock = json.loads(Path(__file__).with_name('kas-assets.lock.json').read_text())
    routes = {}
    for record in lock['assets']:
        if 'fixture' in record:
            data = Path(__file__).parent.joinpath(record['fixture']).read_bytes()
        else:
            url = urllib.parse.urlsplit(record['fetch_url'])
            if url.scheme != 'https' or url.hostname not in ('github.com', 'bin.magitrickle.dev'):
                raise ValueError('unapproved pinned dependency origin')
            path = Path(cache) / record['filename']
            if path.is_symlink():
                raise ValueError('dependency cache symlink rejected')
            if not path.exists():
                with urllib.request.urlopen(record['fetch_url'], timeout=120) as response:
                    path.write_bytes(response.read())
            data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != record['sha256']:
            raise ValueError('pinned KAS dependency digest mismatch')
        url = urllib.parse.urlsplit(record['url'])
        routes[(url.scheme, url.hostname, url.path)] = data
    return routes


class KASMirror:
    def __init__(self, repository, revision, private, cache):
        self.sources = load_sources(repository, revision)
        self.routes = assets(cache)
        for path, data in self.sources.items():
            self.routes[('https', DOMAINS[0], '/saymer-alt/keenetic-auto-setup/' + revision + '/' + path)] = data
            self.routes[('https', DOMAINS[1], '/repos/saymer-alt/keenetic-auto-setup/contents/' + path + '?ref=' + revision)] = data
        self.private = Path(private) / 'tls'
        self.private.mkdir(mode=0o700)
        def openssl(*args):
            subprocess.run(['openssl', *args], cwd=self.private, check=True, capture_output=True)
        openssl('req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', 'ca.key',
                '-out', 'ca.pem', '-days', '2', '-subj', '/CN=Disposable QEMU laboratory CA',
                '-addext', 'basicConstraints=critical,CA:TRUE',
                '-addext', 'keyUsage=critical,keyCertSign,cRLSign',
                '-addext', 'subjectKeyIdentifier=hash')
        openssl('req', '-newkey', 'rsa:2048', '-nodes', '-keyout', 'server.key',
                '-out', 'server.csr', '-subj', '/CN=raw.githubusercontent.com')
        (self.private / 'extensions').write_text('subjectAltName=' + ','.join('DNS:' + host for host in DOMAINS) +
            '\nbasicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\n'
            'extendedKeyUsage=serverAuth\nauthorityKeyIdentifier=keyid,issuer\n')
        openssl('x509', '-req', '-in', 'server.csr', '-CA', 'ca.pem', '-CAkey', 'ca.key',
                '-CAcreateserial', '-out', 'server.pem', '-days', '2', '-extfile', 'extensions')
        routes = self.routes

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                host = self.headers.get('Host', '').split(':', 1)[0].lower()
                data = routes.get(('https', host, self.path))
                if data is None:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Content-Type', 'application/octet-stream')
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(self.private / 'server.pem', self.private / 'server.key')
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def http_routes(self):
        return {(host, path): data for (scheme, host, path), data in self.routes.items() if scheme == 'http'}

    def __enter__(self):
        self.thread.start()
        return self.server.server_port

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

"""Authenticated SSH probes against the controller's isolated loopback VM."""
import json
import logging
import os
from pathlib import Path
import secrets
import socket
import time

import paramiko

logging.getLogger('paramiko').addHandler(logging.NullHandler())
logging.getLogger('paramiko').propagate = False


class Guest:
    def __init__(self, credentials, private, port=8022, username='root', deadline=None):
        self.password = json.loads(Path(credentials).read_text())['password']
        self.private = Path(private)
        self.port = port
        self.username = username
        self.deadline = deadline
        self.key = None
        self.client = None

    def connect(self, timeout=180):
        until = time.monotonic() + timeout
        if self.deadline is not None:
            until = min(until, self.deadline)
        while time.monotonic() < until:
            client = None
            try:
                # First key comes from this disposable, loopback-only guest.
                # Pin it before authentication and reject changes after reset.
                if self.key is None:
                    with socket.create_connection(('127.0.0.1', self.port), timeout=5) as sock:
                        transport = paramiko.Transport(sock)
                        try:
                            transport.start_client(timeout=10)
                            key = transport.get_remote_server_key()
                        finally:
                            transport.close()
                        self.key = key
                client = paramiko.SSHClient()
                client.set_missing_host_key_policy(paramiko.RejectPolicy())
                client.get_host_keys().add('[127.0.0.1]:%d' % self.port, self.key.get_name(), self.key)
                client.connect('127.0.0.1', port=self.port, username=self.username, password=self.password,
                               allow_agent=False, look_for_keys=False, timeout=5,
                               banner_timeout=10, auth_timeout=10)
                self.client = client
                return
            except paramiko.AuthenticationException:
                if client:
                    client.close()
                raise RuntimeError('lab SSH authentication rejected') from None
            except (OSError, EOFError, paramiko.SSHException):
                if client:
                    client.close()
                time.sleep(2)
        raise TimeoutError('authenticated guest SSH unavailable')

    def close(self):
        if self.client:
            self.client.close()
            self.client = None

    def execute(self, text, timeout=30, input_data=None, on_stdout=None):
        until = time.monotonic() + timeout
        if self.deadline is not None:
            until = min(until, self.deadline)
        if until <= time.monotonic():
            raise TimeoutError('guest controller time budget exceeded')
        stdin, stdout, stderr = self.client.exec_command(text, timeout=timeout)
        if input_data is not None:
            stdin.write(input_data)
            stdin.flush()
            stdin.channel.shutdown_write()
        else:
            stdin.close()
        channel = stdout.channel
        data, errors = b'', b''
        try:
            while True:
                if time.monotonic() >= until:
                    raise TimeoutError('guest command time budget exceeded')
                if channel.recv_ready():
                    chunk = channel.recv(65536)
                    data = (data + chunk)[-262144:]
                    if on_stdout:
                        on_stdout(chunk)
                if channel.recv_stderr_ready():
                    errors = (errors + channel.recv_stderr(65536))[-65536:]
                if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                    code = channel.recv_exit_status()
                    break
                time.sleep(0.025)
        finally:
            channel.close()
        # Keep diagnostics private; even error output can contain router data.
        with (self.private / 'ssh-private.log').open('ab') as log:
            os.chmod(self.private / 'ssh-private.log', 0o600)
            log.write(data + errors + b'\n')
        return code, data.decode('utf8', errors='replace').strip(), errors.decode('utf8', errors='replace').strip()

    def command(self, text, timeout=30, input_data=None):
        code, data, errors = self.execute(text, timeout, input_data)
        if code != 0:
            raise RuntimeError('guest probe returned nonzero')
        return data

    def entware(self, marker=None):
        if self.command('uname -m') != 'aarch64':
            raise RuntimeError('guest ABI mismatch')
        if self.command('/bin/sh -c "id -u"') != '0':
            raise RuntimeError('native shell privilege mismatch')
        mounts = self.command('cat /proc/mounts')
        if not any(len(row.split()) >= 3 and row.split()[1:3] == ['/opt', 'ext4'] for row in mounts.splitlines()):
            raise RuntimeError('opt is not a mounted EXT4 filesystem')
        if 'opkg version' not in self.command('/opt/bin/opkg --version').lower():
            raise RuntimeError('native opkg execution failed')
        architectures = self.command('/opt/bin/opkg print-architecture')
        if 'aarch64-3.10' not in architectures:
            raise RuntimeError('package ABI not configured')
        installed = self.command('/opt/bin/opkg list-installed')
        observed = dict(line.split(' - ', 1) for line in installed.splitlines() if ' - ' in line)
        lock = json.loads(Path(__file__).with_name('entware.lock.json').read_text())
        if any(observed.get(p['Package']) != p['Version'] for p in lock['packages']):
            raise RuntimeError('real opkg database differs from pinned bootstrap')
        if marker is None:
            marker = secrets.token_hex(16)
            self.command("printf '%s' '" + marker + "' > /opt/.lab-persistence; sync")
        elif self.command('cat /opt/.lab-persistence') != marker:
            raise RuntimeError('EXT4 marker did not survive reboot')
        return marker

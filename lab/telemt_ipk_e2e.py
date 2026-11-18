"""Disposable QEMU/Entware TeleMT IPK lifecycle using REAL opkg, no live router.

The network update endpoint is a strict local fixture. Published IPKs are
SHA256 pinned and unchanged; synthetic "next" packages modify ONLY the
control Version and are NEVER published. They test opkg transaction semantics,
not upstream compatibility or running Telegram/Panel API.
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
from pathlib import Path
import re
import tarfile

BASE = '/opt/lab/telemt'
ASSETS = BASE + '/assets'
TESTBIN = BASE + '/testbin'
MANIFEST = BASE + '/manifest.json'
PKGS = {
    'core': ('telemt', '3.5.17-1', '3.5.18-1',
             'telemt_3.5.17-1_aarch64-3.10.ipk',
             'telemt_3.5.18-1_aarch64-3.10.ipk',
             'ccab455710761f236f5da6f2f2a852de575e5db164f59b8157c30e270110e509',
             '/opt/bin/telemt'),
    'panel': ('telemt-panel-lite', '1.0.0~rc.4-1', '1.0.0~rc.5-1',
              'telemt-panel-lite_1.0.0.rc.4-1_aarch64-3.10.ipk',
              'telemt-panel-lite_1.0.0.rc.5-1_aarch64-3.10.ipk',
              '39053c1e747685183390f0d2f683b2ef72b7d3d9afe97c8724c4358620b64424',
              '/opt/bin/telemt-panel'),
}
CHECKS = ('TELEMT_IPK_INSTALL', 'TELEMT_IPK_REFUSALS', 'TELEMT_IPK_STAGE',
          'TELEMT_IPK_UPGRADE', 'TELEMT_IPK_RESTORE', 'TELEMT_IPK_REMOVE_REINSTALL')
ROUTER_ONLY = ('TELEMT_IPK_SERVICE_HEALTH', 'TELEMT_IPK_PANEL_BUTTONS')


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def tar_member_bytes(archive: bytes, member_name: str) -> bytes:
    with tarfile.open(fileobj=io.BytesIO(archive), mode='r:gz') as tf:
        matches = [m for m in tf if m.name.lstrip('./') == member_name and m.isfile()]
        if len(matches) != 1:
            raise ValueError('unexpected package tar membership: ' + member_name)
        return tf.extractfile(matches[0]).read()


def metadata(ipk: bytes) -> dict:
    control = tar_member_bytes(tar_member_bytes(ipk, 'control.tar.gz'), 'control').decode('utf-8')
    result = {}
    for field in ('Package', 'Version', 'Architecture'):
        match = re.findall(r'^' + field + r': (.+)$', control, re.M)
        if len(match) != 1:
            raise ValueError('invalid control field: ' + field)
        result[field] = match[0]
    return result


def binary_digest(ipk: bytes, package: str) -> str:
    data_tar = tar_member_bytes(ipk, 'data.tar.gz')
    binary = 'opt/bin/telemt' if package == 'telemt' else 'opt/bin/telemt-panel'
    return sha256(tar_member_bytes(data_tar, binary))


def synthetic_upgrade(ipk: bytes, from_version: str, to_version: str) -> bytes:
    """Repackage ONLY the control Version in a disposable, synthetic IPK."""
    old_control_tar = tar_member_bytes(ipk, 'control.tar.gz')
    old_control = tar_member_bytes(old_control_tar, 'control')
    marker = ('Version: ' + from_version + '\n').encode()
    if old_control.count(marker) != 1:
        raise ValueError('cannot uniquely locate original version')
    with tarfile.open(fileobj=io.BytesIO(old_control_tar), mode='r:gz') as source:
        content = io.BytesIO()
        with tarfile.open(fileobj=content, mode='w:gz') as target:
            for member in source:
                item = source.extractfile(member).read() if member.isfile() else None
                if member.name.lstrip('./') == 'control':
                    item = item.replace(marker, ('Version: ' + to_version + '\n').encode())
                entry = copy.copy(member)
                if item is not None:
                    entry.size = len(item)
                target.addfile(entry, io.BytesIO(item) if item is not None else None)
        new_control_tar = content.getvalue()

    output = io.BytesIO()
    with tarfile.open(fileobj=io.BytesIO(ipk), mode='r:gz') as source:
        with tarfile.open(fileobj=output, mode='w:gz') as target:
            for member in source:
                item = source.extractfile(member).read() if member.isfile() else None
                if member.name.lstrip('./') == 'control.tar.gz':
                    item = new_control_tar
                entry = copy.copy(member)
                if item is not None:
                    entry.size = len(item)
                target.addfile(entry, io.BytesIO(item) if item is not None else None)
    result = output.getvalue()
    original = metadata(ipk)
    changed = metadata(result)
    if original['Version'] != from_version or changed != {
        'Package': original['Package'], 'Version': to_version,
        'Architecture': original['Architecture']
    }:
        raise ValueError('synthetic package metadata mismatch')
    if tar_member_bytes(result, 'data.tar.gz') != tar_member_bytes(ipk, 'data.tar.gz'):
        raise ValueError('synthetic change altered package payload')
    return result


def prepared_fixtures(directory: Path) -> tuple[dict, dict]:
    """Verify published package digests BEFORE building disposable upgrades."""
    current, next_versions = {}, {}
    for family, (pkg, oldver, newver, name, newname, expected, _) in PKGS.items():
        source = directory / name
        if source.is_symlink() or not source.is_file():
            raise ValueError('missing verified upstream IPK: ' + family)
        data = source.read_bytes()
        if sha256(data) != expected:
            raise ValueError('verified IPK SHA-256 mismatch: ' + family)
        if metadata(data) != {'Package': pkg, 'Version': oldver, 'Architecture': 'aarch64-3.10'}:
            raise ValueError('published IPK control mismatch: ' + family)
        generated = synthetic_upgrade(data, oldver, newver)
        current[family] = {'name': name, 'data': data, 'digest': expected,
                           'binary': binary_digest(data, pkg), 'version': oldver}
        next_versions[family] = {'name': newname, 'data': generated,
                                 'digest': sha256(generated),
                                 'binary': binary_digest(generated, pkg),
                                 'version': newver}
    return current, next_versions


def release_json(packages: dict) -> bytes:
    repo = 'https://github.com/saymer-alt/entware-go/releases/download/latest/'
    return json.dumps({
        'tag_name': 'latest',
        'assets': [
            {'name': item['name'], 'digest': 'sha256:' + item['digest'],
             'browser_download_url': repo + item['name']}
            for item in packages.values()
        ],
    }, separators=(',', ':')).encode()


# Mimics ONLY two immutable GitHub URL patterns. It never allows WAN fallback.
# Production scripts remain byte-identical to their pinned PR source.
CURL_FIXTURE = """#!/bin/sh
set -eu
out=''
url=''
while [ "$#" -gt 0 ]; do
  case "$1" in
    -o) out="$2"; shift 2 ;;
    -H|--connect-timeout|--max-time|--retry|--proto|--proto-redir|--max-filesize)
      shift 2 ;;
    -*) shift ;;
    *) url="$1"; shift ;;
  esac
done
[ -n "$out" ] || exit 71
case "$url" in
  https://api.github.com/repos/saymer-alt/entware-go/releases/tags/latest)
    source=/opt/lab/telemt/manifest.json ;;
  https://github.com/saymer-alt/entware-go/releases/download/latest/telemt_*.ipk|https://github.com/saymer-alt/entware-go/releases/download/latest/telemt-panel-lite_*.ipk)
    source="/opt/lab/telemt/assets/${url##*/}" ;;
  *) exit 72 ;;
esac
[ -f "$source" ] && [ ! -L "$source" ] || exit 73
cp "$source" "$out"
"""


def upload(guest, source: bytes, path: str):
    """Stream bytes over the existing authenticated SSH *exec* channel.

    Entware Dropbear in this guest does not provide an SFTP subsystem.
    Never negotiate SFTP or require sftp-server: Guest.execute already accepts
    input_data and writes it to the command's stdin (binary-safe).
    """
    if not isinstance(source, bytes) or not source or len(source) > 32 * 1024 * 1024:
        raise ValueError('invalid fixture transfer size')
    if not (path.startswith(BASE + '/assets/') or
            path.startswith(BASE + '/scripts/') or path in (TESTBIN + '/curl', MANIFEST)):
        raise ValueError('invalid fixture transfer path')
    if not re.fullmatch(r'/opt/lab/telemt/[A-Za-z0-9/_.-]+', path) or '..' in path:
        raise ValueError('invalid fixture transfer path')
    # The file is inside disposable /opt/lab; write to a private temporary name
    # then rename only after the guest reports the matching digest.
    temp = path + '.part'
    code, _, _ = guest.execute(
        'umask 077; cat > ' + temp, timeout=180, input_data=source)
    if code != 0:
        raise RuntimeError('guest SSH stdin transfer failed')
    expected = sha256(source)
    code, output, _ = guest.execute('sha256sum ' + temp, timeout=30)
    if code != 0 or output.split()[0] != expected:
        raise RuntimeError('guest fixture transfer SHA-256 mismatch')
    code, _, _ = guest.execute('mv -f ' + temp + ' ' + path + ' && chmod 0600 ' + path,
                               timeout=30)
    if code != 0:
        raise RuntimeError('guest fixture transfer commit failed')


def cmd(guest, shell: str, timeout=120):
    code, out, error = guest.execute(shell, timeout=timeout)
    if code:
        raise RuntimeError('native command failed')
    return out


def assert_command(guest, shell: str, timeout=120) -> bool:
    return guest.execute(shell, timeout=timeout)[0] == 0


def status(guest, package: str):
    out = guest.execute('opkg status ' + package, timeout=30)[1]
    versions = re.findall(r'^Version: (\S+)$', out, re.M)
    return versions[0] if len(versions) == 1 else None


def shell_cmd(command: str) -> str:
    return ('PATH=' + TESTBIN + ':/opt/bin:/opt/sbin:/bin:/usr/bin '
            'TELEMT_STAGE_DIR=' + BASE + '/stage '
            'TELEMT_CACHE_DIR=' + BASE + '/cache '
            'sh ' + BASE + '/scripts/' + command)


def check_inert(guest, baseline: dict):
    for key, path in baseline.items():
        observed = cmd(guest, 'sha256sum ' + path).split()[0]
        if observed != key:
            raise RuntimeError('synthetic private configuration changed')


def lifecycle(guest, evidence, source: Path, ipk_dir: Path) -> bool:
    for label in CHECKS:
        evidence.check(label, 'NOT_TESTED', 'unattempted')
    for label in ROUTER_ONLY:
        evidence.check(label, 'NOT_TESTED', 'unattempted')

    completed = []
    phase = 'PREPARE'
    try:
        current, future = prepared_fixtures(ipk_dir)
        if not (source / 'entware-manager.sh').is_file():
            raise ValueError('pinned installer source missing')
        phase = 'GUEST_DEPENDENCIES'
        print('TELEMT_IPK_E2E: PHASE=' + phase, flush=True)
        cmd(guest, 'opkg update && opkg install ca-bundle curl jq', timeout=240)
        cmd(guest, 'mkdir -p ' + ASSETS + ' ' + TESTBIN + ' ' + BASE + '/scripts '
            + BASE + '/stage ' + BASE + '/cache')
        phase = 'TRANSFER'
        print('TELEMT_IPK_E2E: PHASE=' + phase, flush=True)
        for path in ('entware-manager.sh', 'install.sh', 'upgrade.sh'):
            upload(guest, (source / path).read_bytes(), BASE + '/scripts/' + path)
        upload(guest, CURL_FIXTURE.encode(), TESTBIN + '/curl')
        cmd(guest, 'chmod 0700 ' + TESTBIN + '/curl')
        for packages in (current, future):
            for item in packages.values():
                upload(guest, item['data'], ASSETS + '/' + item['name'])
        upload(guest, release_json(current), MANIFEST)
        cmd(guest, 'mkdir -p /opt/etc/telemt /opt/etc/telemt-panel /opt/var/lib/telemt-panel')
        cmd(guest,
            "printf 'LAB_PRIVATE_TELEMT_ONLY\\n' > /opt/etc/telemt/config.toml; "
            "printf 'LAB_PRIVATE_PANEL_ONLY\\n' > /opt/etc/telemt-panel/config.toml; "
            "printf 'LAB_PRIVATE_STATE_ONLY\\n' > /opt/var/lib/telemt-panel/panel-state.json")
        safe_paths = ('/opt/etc/telemt/config.toml',
                      '/opt/etc/telemt-panel/config.toml',
                      '/opt/var/lib/telemt-panel/panel-state.json')
        inert = {
            cmd(guest, 'sha256sum ' + path).split()[0]: path for path in safe_paths
        }
        if len(inert) != len(safe_paths):
            raise RuntimeError('lab state fixtures unexpectedly identical')

        phase = 'CLEAN_INSTALL'
        print('TELEMT_IPK_E2E: PHASE=' + phase, flush=True)
        # The genuine installed version MUST be absent prior to a clean install.
        if any(status(guest, data[0]) for data in PKGS.values()):
            raise RuntimeError('lab starts with an already installed package')
        for fam, (pkg, old, _, _, _, _, binary) in PKGS.items():
            phase = 'CLEAN_DRY_' + fam.upper()
            print('TELEMT_IPK_E2E: PHASE=' + phase, flush=True)
            cmd(guest, shell_cmd('install.sh ' + fam), timeout=120)
            phase = 'CLEAN_DRY_STATUS_' + fam.upper()
            if status(guest, pkg) is not None:
                raise RuntimeError('dry run mutated opkg')
            phase = 'CLEAN_APPLY_' + fam.upper()
            print('TELEMT_IPK_E2E: PHASE=' + phase, flush=True)
            cmd(guest, shell_cmd('install.sh ' + fam + ' --apply'), timeout=180)
            phase = 'CLEAN_STATUS_' + fam.upper()
            if status(guest, pkg) != old:
                raise RuntimeError('opkg metadata differs after install')
            phase = 'CLEAN_BINARY_' + fam.upper()
            if cmd(guest, 'sha256sum ' + binary).split()[0] != current[fam]['binary']:
                raise RuntimeError('installed binary identity mismatch')
        check_inert(guest, inert)
        completed.append('TELEMT_IPK_INSTALL')

        phase = 'NEGATIVE_GATES'
        print('TELEMT_IPK_E2E: PHASE=' + phase, flush=True)
        # Reinstall via the clean installer and wrong SHA must be rejected before opkg.
        for fam, data in PKGS.items():
            if assert_command(guest, shell_cmd('install.sh ' + fam + ' --apply')):
                raise RuntimeError('clean installer accepted already installed package')
            if status(guest, data[0]) != data[1]:
                raise RuntimeError('installer refusal modified opkg status')
        wrong = json.loads(release_json(current).decode())
        wrong['assets'][0]['digest'] = 'sha256:' + '0' * 64
        upload(guest, json.dumps(wrong).encode(), MANIFEST)
        if assert_command(guest, shell_cmd('entware-manager.sh inspect core')):
            raise RuntimeError('checksum mismatch was accepted')
        upload(guest, release_json(current), MANIFEST)
        check_inert(guest, inert)
        completed.append('TELEMT_IPK_REFUSALS')

        phase = 'CACHE_STAGE'
        print('TELEMT_IPK_E2E: PHASE=' + phase, flush=True)
        # Preserve old signed IPKs for package-aware rollback BEFORE changing index.
        for fam in PKGS:
            cmd(guest, shell_cmd('entware-manager.sh cache ' + fam), timeout=120)
        upload(guest, release_json(future), MANIFEST)
        for fam in PKGS:
            cmd(guest, shell_cmd('upgrade.sh ' + fam), timeout=120)
        for fam, data in PKGS.items():
            if status(guest, data[0]) != data[1]:
                raise RuntimeError('upgrade staging unexpectedly mutated opkg')
        check_inert(guest, inert)
        completed.append('TELEMT_IPK_STAGE')

        phase = 'SYNTHETIC_OPKG_UPGRADE'
        print('TELEMT_IPK_E2E: PHASE=' + phase, flush=True)
        # Real opkg against synthetic (version-only) successors, no clients/API.
        for fam, (pkg, _, newver, _, newname, _, binary) in PKGS.items():
            cmd(guest, 'opkg install ' + ASSETS + '/' + newname, timeout=180)
            if status(guest, pkg) != newver:
                raise RuntimeError('real opkg package upgrade failed')
            if cmd(guest, 'sha256sum ' + binary).split()[0] != current[fam]['binary']:
                raise RuntimeError('synthetic version-only package changed binary')
        check_inert(guest, inert)
        completed.append('TELEMT_IPK_UPGRADE')

        phase = 'MANUAL_PACKAGE_RESTORE'
        print('TELEMT_IPK_E2E: PHASE=' + phase, flush=True)
        # Manual package-aware restoration; NOT claiming an automatic rollback.
        for fam, (pkg, oldver, _, oldname, _, _, binary) in PKGS.items():
            cmd(guest, 'opkg remove ' + pkg, timeout=120)
            cmd(guest, 'opkg install ' + ASSETS + '/' + oldname, timeout=180)
            if status(guest, pkg) != oldver:
                raise RuntimeError('package-aware manual restore failed')
            if cmd(guest, 'sha256sum ' + binary).split()[0] != current[fam]['binary']:
                raise RuntimeError('restored binary mismatch')
        check_inert(guest, inert)
        completed.append('TELEMT_IPK_RESTORE')

        phase = 'REMOVE_REINSTALL'
        print('TELEMT_IPK_E2E: PHASE=' + phase, flush=True)
        # Switch fixture catalog BACK to original published versions. Otherwise
        # reinstall after remove would accidentally install synthetic rc.4/3.5.18.
        upload(guest, release_json(current), MANIFEST)
        # Removal must not delete user-created config or panel state.
        for fam, (pkg, oldver, _, oldname, _, _, binary) in PKGS.items():
            cmd(guest, 'opkg remove ' + pkg, timeout=120)
            if status(guest, pkg) is not None or assert_command(guest, 'test -e ' + binary):
                raise RuntimeError('real opkg removal left managed binary/database')
            check_inert(guest, inert)
            cmd(guest, shell_cmd('install.sh ' + fam + ' --apply'), timeout=180)
            if status(guest, pkg) != oldver:
                raise RuntimeError('clean reinstall after removal failed')
        check_inert(guest, inert)
        completed.append('TELEMT_IPK_REMOVE_REINSTALL')

    except Exception as error:
        # Mark the right gate on SSH/transport exceptions as well as command
        # failures; never leak raw exception, guest output, secrets or paths.
        failed = CHECKS[len(completed)] if len(completed) < len(CHECKS) else CHECKS[-1]
        evidence.check(failed, 'FAIL', 'telemt_ipk_failed')
        for label in completed:
            evidence.check(label, 'PASS', 'telemt_ipk_passed')
        kind = type(error).__name__
        if kind not in ('RuntimeError', 'ValueError', 'OSError', 'TimeoutError', 'SSHException', 'EOFError'):
            kind = 'OTHER'
        print('TELEMT_IPK_E2E: FAIL phase=' + phase + ' kind=' + kind
              + ' gate=' + failed, flush=True)
        return False

    for label in completed:
        evidence.check(label, 'PASS', 'telemt_ipk_passed')
    print('TELEMT_IPK_E2E: PASS real opkg install, staging, synthetic upgrade, manual restore, removal and reinstall', flush=True)
    print('TELEMT_IPK_E2E: NOT_TESTED automatic rollback, panel button integration, live service health', flush=True)
    return True

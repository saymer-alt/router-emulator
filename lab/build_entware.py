#!/usr/bin/env python3
"""Prepare pinned Entware bootstrap, loopback mirror and a real EXT4 USB image.

Only regular disposable files are created. Guest opkg performs the installation;
the host neither fakes its package database nor substitutes a demonstration OS.
"""
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tarfile
import tempfile
import urllib.request

LOCK = Path(__file__).with_name("entware.lock.json")
MIRROR = "http://192.168.1.100"
FS_OFFSET = 1024 * 1024
FS_SIZE = 256 * 1024 * 1024
FS_UUID = "11111111-2222-4333-8444-555555555555"  # synthetic, never a real drive


def fetch(url, digest, destination):
    destination = Path(destination)
    if not url.startswith("https://bin.entware.net/aarch64-k3.10/"):
        raise ValueError("unexpected bootstrap origin")
    if not destination.exists():
        with urllib.request.urlopen(url, timeout=90) as response:
            data = response.read()
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError("download digest mismatch")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    if destination.is_symlink() or hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
        raise ValueError("cached asset digest mismatch")
    return destination


def bootstrap_archive(source, destination, password):
    root_hash = subprocess.run(["openssl", "passwd", "-6", "-stdin"],
                               input=(password + "\n").encode(), capture_output=True, check=True).stdout.strip()
    passwd = b"root:" + root_hash + b":0:0:lab:/opt/root:/opt/bin/sh\n"
    passwd += b"nobody:*:65534:65534:nobody::/opt/bin/nologin\n"
    with tarfile.open(source, "r:gz") as original, tarfile.open(destination, "w:gz") as target:
        for member in original:
            name = member.name.lstrip("./")
            if name.startswith("etc/dropbear/dropbear_") and name.endswith("_host_key"):
                continue  # never reuse keys from the public installer archive
            data = original.extractfile(member).read() if member.isfile() else None
            if name == "etc/opkg.conf":
                data = ("src/gz entware " + MIRROR + "/entware\n"
                        "src/gz keendev " + MIRROR + "/keenetic\n"
                        "dest root /\nlists_dir ext /opt/var/opkg-lists\n"
                        "arch all 100\narch aarch64-3.10 150\narch aarch64-3.10_kn 200\n").encode()
            if name == "bin/install":
                text = data.decode("utf8")
                needle = "/opt/etc/init.d/S51dropbear start"
                if text.count(needle) != 1:
                    raise ValueError("unexpected native Entware bootstrap")
                text = text.replace(needle, "cat /opt/etc/.lab-passwd > /opt/etc/passwd\n"
                                    "chmod 600 /opt/etc/passwd\nrm -f /opt/etc/.lab-passwd\n" + needle)
                start = text.index('if grep -q "222"')
                end = text.index("\nfi", start) + 3
                text = text[:start] + "echo 'Lab SSH uses private per-run credentials.'" + text[end:]
                data = text.encode()
            if data is not None:
                member.size = len(data)
            target.addfile(member, io.BytesIO(data) if data is not None else None)
        member = tarfile.TarInfo("etc/.lab-passwd")
        member.mode, member.size = 0o600, len(passwd)
        target.addfile(member, io.BytesIO(passwd))


def add_packages(mirror, cache, lock):
    """Extend a prepared local feed with a separately pinned dependency set."""
    packages = json.loads(Path(lock).read_text())['packages']
    for feed in ('entware', 'keenetic'):
        directory = Path(mirror) / feed
        index = directory / 'Packages.gz'
        blocks = {}
        for block in gzip.decompress(index.read_bytes()).decode().split('\n\n'):
            if block.startswith('Package: '):
                blocks[block.splitlines()[0][9:]] = block
        for package in packages:
            if package['feed'] != feed:
                continue
            payload = fetch(package['url'], package['SHA256sum'], Path(cache) / package['Filename'])
            shutil.copyfile(payload, directory / package['Filename'])
            blocks[package['Package']] = '\n'.join(k + ': ' + v for k, v in package.items() if k not in ('feed', 'url'))
        index.write_bytes(gzip.compress(('\n\n'.join(blocks.values()) + '\n\n').encode(), mtime=0))


def build(image, mirror, cache, credentials):
    image, mirror, cache = (Path(p).resolve() for p in (image, mirror, cache))
    if image.exists() or mirror.exists():
        raise ValueError("output image and mirror must be new")
    lock = json.loads(LOCK.read_text(encoding="utf8"))
    cache.mkdir(parents=True, exist_ok=True)
    source = fetch(lock["installer"]["url"], lock["installer"]["sha256"], cache / "installer.tar.gz")
    mirror.mkdir(parents=True, mode=0o700)
    for feed in ("entware", "keenetic"):
        directory = mirror / feed
        directory.mkdir()
        blocks = []
        for package in lock["packages"]:
            if package["feed"] != feed:
                continue
            payload = fetch(package["url"], package["SHA256sum"], cache / package["Filename"])
            shutil.copyfile(payload, directory / package["Filename"])
            # Preserve real package version/dependencies/architecture and SHA256.
            blocks.append("\n".join(k + ": " + v for k, v in package.items() if k not in ("feed", "url")))
        (directory / "Packages.gz").write_bytes(gzip.compress(("\n\n".join(blocks) + "\n\n").encode(), mtime=0))
    with tempfile.TemporaryDirectory(prefix="entware-stage-", dir=cache) as td:
        stage = Path(td)
        (stage / "install").mkdir()
        private = json.loads(Path(credentials).read_text())
        bootstrap_archive(source, stage / "install" / "aarch64-installer.tar.gz", private["password"])
        image.parent.mkdir(parents=True, exist_ok=True)
        with image.open("xb") as fh:
            os.chmod(image, 0o600)
            fh.truncate(FS_OFFSET + FS_SIZE)
            mbr = bytearray(512)
            mbr[446:462] = struct.pack("<B3sB3sII", 0, b"\xfe\xff\xff", 0x83,
                                       b"\xfe\xff\xff", FS_OFFSET // 512, FS_SIZE // 512)
            mbr[510:512] = b"\x55\xaa"
            fh.write(mbr)
        subprocess.run(["mkfs.ext4", "-q", "-F", "-b", "4096", "-L", "KASOPT", "-U", FS_UUID,
                        "-O", "^orphan_file,^metadata_csum_seed", "-E",
                        "offset=1048576,lazy_itable_init=0,lazy_journal_init=0",
                        "-d", str(stage), str(image), str(FS_SIZE // 4096)], check=True, capture_output=True)
    return image

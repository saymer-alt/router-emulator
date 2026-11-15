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
import time
import urllib.request

LOCK = Path(__file__).with_name("entware.lock.json")
TRUSTED_ORIGIN = "https://bin.entware.net/aarch64-k3.10/"
# KeeneticOS mipsel devices (KN-1913 Ported on MT7621) install Entware from
# the soft-float distribution; the repo directory is mipselsf-k3.4 while its
# packages carry the mipsel-3.4 ABI name.
MIPSLSF_ORIGIN = "https://bin.entware.net/mipselsf-k3.4/"
MIRROR = "http://192.168.1.100"
FS_OFFSET = 1024 * 1024
FS_SIZE = 256 * 1024 * 1024
FS_UUID = "11111111-2222-4333-8444-555555555555"  # synthetic, never a real drive

# Per-architecture Entware profiles: the opkg architecture lines the installer
# ships and the file name(s) NDMS looks for on the OPKG volume (the aarch64
# name is proven by the ARM64 lab; the mipsel name follows the platform arch
# plus the upstream distribution's own file name).
ARCH_LINES = {
    "aarch64": "arch all 100\narch aarch64-3.10 150\narch aarch64-3.10_kn 200\n",
    "mipsel": "arch all 100\narch mipsel-3.4 150\narch mipsel-3.4_kn 200\n",
}
INSTALLER_NAMES = {
    "aarch64": ("aarch64-installer.tar.gz",),
    "mipsel": ("mipsel-installer.tar.gz", "mips-installer.tar.gz"),
}


def trusted_origins():
    # Evaluated per call so tests can patch TRUSTED_ORIGIN.
    return (TRUSTED_ORIGIN, MIPSLSF_ORIGIN)


def fetch(url, digest, destination):
    destination = Path(destination)
    if not any(url.startswith(origin) for origin in trusted_origins()):
        raise ValueError("unexpected bootstrap origin")
    if not destination.exists():
        # bin.entware.net intermittently answers 503 to runner IPs; three
        # bounded attempts for 502/503/504 only. Mid-wave feed rotation also
        # briefly answers 404 for files that verify live minutes before and
        # after the failure (run 43250106154): at most two bounded 404
        # retries. Failures name the URL; every downloaded byte is still
        # pinned by the digest check below.
        missing = 0
        for attempt in (1, 2, 3, 4, 5):
            try:
                with urllib.request.urlopen(url, timeout=90) as response:
                    data = response.read()
                break
            except urllib.error.HTTPError as error:
                if error.code == 404:
                    missing += 1
                    if missing > 2:
                        raise ValueError(f"trusted origin answered 404 after two retries: {url}") from error
                elif attempt == 5 or error.code not in (502, 503, 504):
                    raise ValueError(f"trusted origin fetch failed: HTTP {error.code}: {url}") from error
                time.sleep(min(15, 3 * attempt))
        else:
            raise ValueError(f"trusted origin fetch failed after bounded attempts: {url}")
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError(f"download digest mismatch: {url}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    if destination.is_symlink() or hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
        raise ValueError("cached asset digest mismatch")
    return destination


def bootstrap_archive(source, destination, password, arch="aarch64"):
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
                        + ARCH_LINES[arch]).encode()
            if name == "bin/install":
                text = data.decode("utf8")
                needle = "/opt/etc/init.d/S51dropbear start"
                if text.count(needle) != 1:
                    raise ValueError("unexpected native Entware bootstrap")
                text = text.replace(needle, "cat /opt/etc/.lab-passwd > /opt/etc/passwd\n"
                                    "chmod 600 /opt/etc/passwd\nrm -f /opt/etc/.lab-passwd\n"
                                    # The source archive has no Dropbear config:
                                    # opkg creates it earlier in bin/install.
                                    # Set the lab transport PID only afterwards.
                                    "printf '%s\\n' 'PORT=222' 'PIDFILE=\"/var/run/lab-dropbear.pid\"' > /opt/etc/config/dropbear.conf\n"
                                    # The [4/5] wedge (MT7621 clean run 2026-11-15:
                                    # no script output for 600 s after the [4/5]
                                    # echo) is bisected from here on the next run:
                                    # these markers bypass the doinstaller stdout
                                    # bridge via /dev/console, so the UART shows
                                    # how far the script actually got.
                                    "echo 'lab-dropbear-begin' > /dev/console 2>/dev/null\n" + needle)
                start = text.index('if grep -q "222"')
                end = text.index("\nfi", start) + 3
                text = text[:start] + text[end:]
                # ndm's doinstaller console bridge stops relaying the script's
                # stdout during the tail section (observed from the timezone
                # RCI GET onward: even a pre-POST echo never reaches the UART,
                # local reproduction 2026-11-12), so the completion markers
                # would time out the 600s gate wait over a *successful*
                # install (both rm's execute, opkg database populated, no
                # wget alive). Move the markers and the auto-install hook
                # removal before the POST, and re-emit the gate marker
                # directly to the guest console device, bypassing the relay:
                # a UART write from the live guest lands in the same serial
                # log the wait reads (verified with a live /dev/console and
                # /dev/ttyS0 probe during a stalled gate). The SSH probes stay
                # the authoritative install-success gate; reject patterns are
                # unchanged.
                text = text.replace("\n\necho '[5/5] \"Entware\" installed!'\n", "\n", 1)
                text = text.replace("\nrm /opt/etc/init.d/doinstall\n", "\n", 1)
                text = text.replace(
                    "wget -qO - --post-data=",
                    "echo 'Lab SSH uses private per-run credentials.'\n\n"
                    "echo '[5/5] \"Entware\" installed!'\n"
                    "echo '[5/5] \"Entware\" installed!' > /dev/console 2>/dev/null\n\n"
                    "rm /opt/etc/init.d/doinstall\n\n"
                    "echo 'lab-post-begin' > /dev/console 2>/dev/null\n\n"
                    "wget -qO - --post-data=", 1)
                # The optional TZ RCI GET is unsafe only during the MIPSel
                # lab's doinstall; preserve the bounded probe on aarch64.
                # Stage-C and production code are not changed.
                tz_needle = ('timezone="$(echo $(wget -qO - localhost:79/rci/'
                             'show/clock/date/tz | grep locality | cut -d\':\''
                             ' -f2 | sed \'s#\\("\\|,\\)##g\'))"')
                tz_bounded = tz_needle.replace("wget -qO - localhost:79",
                                               "wget -qT 3 -O - localhost:79")
                if tz_needle in text:
                    if arch == "mipsel":
                        # The optional RCI show GET stalls while doinstall
                        # is active. BusyBox 1.38 MIPSel SIGSEGVs on the
                        # bounded wget timeout (Issue #82). In this LAB
                        # bootstrap alone, explicitly omit the optional TZ
                        # probe; the prior failure left TZ empty anyway.
                        tz_stmt = ('timezone=""\n'
                                   "echo 'lab-tz-skipped' > /dev/console 2>/dev/null")
                    else:
                        tz_stmt = tz_bounded
                    text = text.replace(
                        tz_needle,
                        "echo 'lab-tz-begin' > /dev/console 2>/dev/null\n"
                        + tz_stmt +
                        "\necho 'lab-tz-end' > /dev/console 2>/dev/null", 1)
                elif "rci/show/clock/date/tz" in text:
                    raise ValueError("unexpected native Entware timezone probe")
                # A silent no-op replace would reintroduce the swallowed [5/5]
                # after the next upstream installer rotation; fail loudly
                # instead so the lab refuses to build a mis-patched bootstrap.
                if text.index("echo '[5/5] \"Entware\" installed!'") > text.index('wget -qO - --post-data=') \
                        or text.index('rm /opt/etc/init.d/doinstall') > text.index('wget -qO - --post-data='):
                    raise ValueError('unexpected native Entware bootstrap tail')
                if text.index("lab-dropbear-begin") > text.index('wget -qO - --post-data=') \
                        or text.index("lab-post-begin") > text.index('wget -qO - --post-data='):
                    raise ValueError('unexpected native Entware bootstrap tail')
                if "lab-tz-begin" in text and (
                        text.index("lab-tz-begin") > text.index("lab-tz-end")
                        or text.index("lab-tz-end") > text.index("lab-dropbear-begin")
                        or "wget -qO - localhost:79" in text
                        or (arch == "mipsel" and "rci/show/clock/date/tz" in text)):
                    raise ValueError('unexpected native Entware bootstrap tail')
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


def validate_mipsel_lock(lock):
    """Fail closed on incomplete MIPSel local feed before any downloads.

    Package names and verified Provides aliases are used for dependency
    resolution; no assumption that a missing library exists in the guest.
    This intentionally does not alter the existing ARM64 lab contract.
    """
    import re

    packages = lock.get("packages")
    if not isinstance(packages, list) or not packages:
        raise ValueError("mipsel lock has no packages")
    names = set()
    provided = set()
    for package in packages:
        name = package.get("Package", "")
        if not name or name in names:
            raise ValueError("duplicate or invalid mipsel lock package: " + str(name))
        names.add(name)
        if package.get("Architecture") not in ("mipsel-3.4", "mipsel-3.4_kn", "all"):
            raise ValueError("invalid MIPSel package architecture: " + name)
        feed = package.get("feed")
        if feed not in ("entware", "keenetic"):
            raise ValueError("invalid MIPSel package feed: " + name)
        prefix = MIPSLSF_ORIGIN + ("keenetic/" if feed == "keenetic" else "")
        filename = package.get("Filename", "")
        if not filename or "/" in filename or package.get("url") != prefix + filename:
            raise ValueError("unexpected MIPSel package URL: " + name)
        if not re.fullmatch(r"[0-9a-f]{64}", package.get("SHA256sum", "")):
            raise ValueError("invalid MIPSel package SHA256: " + name)
        for alias in package.get("Provides", "").split(","):
            alias = alias.strip().split(" ", 1)[0]
            if alias:
                provided.add(alias)
    missing = set()
    for package in packages:
        for group in package.get("Depends", "").split(","):
            if not group.strip():
                continue
            alternatives = []
            for alternative in group.split("|"):
                match = re.fullmatch(
                    r"\s*([A-Za-z0-9_.+-]+)(?:\s*\([^)]+\))?\s*",
                    alternative)
                if match is None:
                    raise ValueError("unrecognized dependency syntax: " + group)
                alternatives.append(match.group(1))
            if not any(alt in names or alt in provided for alt in alternatives):
                missing.add(package["Package"] + " -> " + " | ".join(alternatives))
    if missing:
        raise ValueError("incomplete MIPSel dependency closure: " + "; ".join(sorted(missing)))
    return len(packages)


def build(image, mirror, cache, credentials, arch="aarch64", lock_path=None):
    image, mirror, cache = (Path(p).resolve() for p in (image, mirror, cache))
    if image.exists() or mirror.exists():
        raise ValueError("output image and mirror must be new")
    lock = json.loads(Path(lock_path).read_text(encoding="utf8") if lock_path
                      else LOCK.read_text(encoding="utf8"))
    if arch == "mipsel":
        validate_mipsel_lock(lock)
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
        for name in INSTALLER_NAMES[arch]:
            bootstrap_archive(source, stage / "install" / name, private["password"], arch=arch)
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

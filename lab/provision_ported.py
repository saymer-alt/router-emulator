#!/usr/bin/env python3
"""Synthetic NDM startup configuration for disposable WR3000P lab NAND only.

No firmware modification or authentication bypass. Each run receives a random
password, provisioned by NDM's native password command. The initial private
NAND contains this temporary password and must be destroyed after the run.
Credentials and generated configuration must never enter logs or artifacts.
"""
import json
import os
from pathlib import Path
import re
import secrets
import struct

from lab.prepare_ported_wr3000p import TOTAL, write_at

CONFIG_OFFSET = 0x2700000
CONFIG_SIZE = 0x80000
DATA_OFFSET = 0x6200
REALM = "Keenetic WR3000P"


def entware_config():
    """Isolated synthetic switch/bridge and native removable-volume selection."""
    text = 'interface GigabitEthernet0\n    up\n!\n'
    for port in range(4):
        text += ('interface GigabitEthernet0/%d\n    switchport mode access\n'
                 '    switchport access vlan 1\n    up\n!\n') % port
    return text + ('interface GigabitEthernet0/Vlan1\n    up\n!\n'
                   'interface Bridge0\n    include GigabitEthernet0/Vlan1\n'
                   '    mac address 02:00:00:00:01:01\n    security-level private\n'
                   '    ip address 192.168.1.1 255.255.255.0\n    up\n!\n'
                   'opkg disk KASOPT:/\n')


def config_image(text):
    data = text.encode("utf8")
    if not data or b"\0" in data or len(data) > CONFIG_SIZE - DATA_OFFSET:
        raise ValueError("invalid synthetic startup configuration")
    # Native ConfigImage zero-entry: magic, physical offset within Config_1,
    # stored length, flags (0 = uncompressed), original length, reserved.
    # No signature/checksum check is patched or disabled by this encoding.
    image = bytearray(b"\xff" * CONFIG_SIZE)
    image[:32] = b"ndm3" + struct.pack("<4I", DATA_OFFSET, len(data), 0, len(data)) + b"\0" * 12
    image[DATA_OFFSET:DATA_OFFSET + len(data)] = data
    return bytes(image)


def synthetic_config(username, password, extra=""):
    if not re.fullmatch(r"[a-z][a-z0-9_-]{2,30}", username):
        raise ValueError("invalid lab username")
    if not re.fullmatch(r"[A-Za-z0-9_-]{24,64}", password):
        raise ValueError("lab password must be a random token of at least 24 characters")
    # NDM computes its own hashes using the running device's actual realm.
    # Keep this text exclusively in the disposable private NAND, never reports.
    return ("! $$$ Model: " + REALM + "\n! $$$ Username: " + username + "\n"
            "system\n    hostname keenetic-lab\n    zram\n!\n"
            "user " + username + "\n    password plain " + password +
            "\n    tag cli\n    tag http\n    tag opt\n!\n" + extra)


def provision(nand, private_dir, extra=""):
    nand, private = Path(nand).resolve(), Path(private_dir).resolve()
    if private.exists():
        raise ValueError("credential directory must be new")
    files = []
    for n in range(6):
        found = list(nand.glob("mt7981.mtd%d.*.bin" % n))
        if len(found) != 1 or found[0].is_symlink():
            raise ValueError("requires disposable builder output")
        files.append(found[0])
    if sum(p.stat().st_size for p in files) != TOTAL:
        raise ValueError("invalid disposable NAND size")
    os.chmod(nand, 0o700)
    for path in files:
        os.chmod(path, 0o600)
    private.mkdir(parents=True, mode=0o700)
    # Ported's console first-setup policy keys off the administrator account.
    # An arbitrary cli-tagged account without admin leaves first-setup active.
    username, password = "admin", secrets.token_urlsafe(24)
    config = synthetic_config(username, password, extra)
    credentials = private / "credentials.json"
    with credentials.open("x", encoding="utf8") as fh:
        os.chmod(credentials, 0o600)
        json.dump({"username": username, "password": password}, fh)
    write_at(files, CONFIG_OFFSET, config_image(config))
    return credentials

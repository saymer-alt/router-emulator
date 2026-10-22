# WR3000P Ported laboratory

This branch starts at `e38cf0ce0c15ba0ccdf9976e5b16e268956e6d0a`.
It uses published QEMU 3.8, compatible KeenBOOT KB-41 and the unchanged
Ported 5.1.8 FIT, all checked by SHA256. QEMU is not rebuilt.

The virtual board is Cudy WR3000P/MT7981 with Ported. The firmware's own
`show version` reports release `5.01.C.8.0-2`, `arch: aarch64` and model
KN-3811; its authentication realm is `Keenetic KN-3811`. These software
identities do not establish equivalence to a physical KN-3811 or KN-1012.

## Authentication and privacy

`provision_ported.py` writes only a disposable synthetic Config_1. The native
32-byte `ndm3` zero entry points to uncompressed text at `0x6200` inside the
configuration partition at physical NAND `0x2700000`. Firmware, signature
verification and native authentication remain intact.

Every run generates `admin` with a 192-bit random password. The native
`password plain` command computes the correct hashes for the running image.
This temporary cleartext exists only in the private initial NAND and credential
file; both must be destroyed. NDM's configuration-save command writes its native
configuration before the persistence test. An arbitrary cli-tagged username
without admin leaves Ported in first-setup mode, so an unauthenticated prompt
is deliberately rejected by the scenario.

UART logs, firmware, NAND, USB, generated host keys, password hashes and private
credentials are never uploaded. Only `gates.json`, `junit.xml`, and `summary.md`
from the fixed-label evidence writer are public artifacts. Do not upload the
private directory to troubleshoot a failure.

## Local Linux / WSL preparation

Install `python3`, `python3-paramiko`, `mtd-utils`, `socat`, `curl`, `wget`,
`unzip`, `e2fsprogs`, and `openssl`. Use a short private native Linux path for
UNIX sockets. Source and output paths must differ.

```sh
bash lab/fetch_lab_assets.sh /var/tmp/ported-assets
python3 -m lab.prepare_ported_wr3000p \
  --stock-nand /var/tmp/ported-assets/downloads/Router-Emulator/nand-wr3000p \
  --ported /var/tmp/ported-assets/downloads/ported.bin \
  --keenboot /var/tmp/ported-assets/downloads/keenboot.bin \
  --out /var/tmp/ported-nand
python3 -m lab.ported_e2e \
  --emulator-root /var/tmp/ported-assets/downloads/Router-Emulator \
  --nand-dir /var/tmp/ported-nand \
  --private-dir /var/tmp/ported-private \
  --report-dir /var/tmp/ported-public
```

`BOOT_READY` requires all eight ordered boot stages. `AUTH_READY` requires the
real Login/Password protocol, a version response, configuration save, reset and
another authenticated version response. E2's console is NDM CLI. The native
`/bin/sh` dispatcher has no standalone ash in this image; a root shell is
checked separately after genuine Entware bootstrap.

## Entware preparation

`entware.lock.json` pins the official AArch64 installer and every dependency.
`build_entware.py` constructs a regular RAW image with an MBR partition starting
at 1 MiB and a real EXT4 filesystem. `/install/aarch64-installer.tar.gz` uses
the official bootstrap, with local feed URLs, regenerated per-run SSH identity,
and a per-run root password installed before the SSH service starts. Native
guest `opkg` performs all package installs and creates its own package database.

The host verifies SHA256 before preparing the mirror; opkg retains the package
checksums in real feed metadata. No integrity option is disabled. The existing
virtual xHCI/USB-storage mechanism attaches a copy of this disk. `opkg disk
KASOPT:/` selects it through NDM. WAN is `restrict=on`; one explicit slirp TCP
mapping sends `192.168.1.100:80` only to a mirror bound to `127.0.0.1`.
Inherited `WAN_EXTRA` and `LAN_EXTRA` cannot silently open other mappings.

The real guest probe checks `/bin/sh` UID 0, `uname -m`, mounted EXT4 `/opt`,
executed opkg version/architectures, all pinned installed packages and a writable
persistence marker. Reboot must preserve the marker, package versions and SSH
host key. Merely copying a binary or constructing this disk is not E3 PASS.

## Reports and limits

Nine independent gates use PASS, FAIL, BLOCKED or NOT_TESTED. JUnit encodes
BLOCKED/NOT_TESTED as skipped, never as successful execution. A successful boot
cannot advance authentication, Entware or KAS gates. E4 must test actual kernel
operations; a component name in `show version` alone is insufficient.

No schedule is enabled. No changes to real routers, networks, VPS, main/stable,
tags, releases or automatic merges are part of this laboratory.

References: [native password command](https://storage.googleapis.com/docs.help.keenetic.com/cli/4.3/en/cli_manual_kn-3610.pdf),
[official Entware USB installation](https://support.keenetic.com/titan/kn-1812/en/20980-installing-the-entware-repository-on-a-usb-drive.html).

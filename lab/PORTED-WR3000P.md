# WR3000P Ported 5.1.8 boot investigation

This is isolated QEMU evidence, not physical-router acceptance. No KAS install.

## Findings

The original candidate combined OpenWrt backing-file boundaries with the
KeenBOOT guest partition map. They are different. Read-only `mtd list` from
the actual KB-51 console reported these NMBM addresses:

| Guest partition | Start | End |
| --- | --- | --- |
| bl2 | 0x000000 | 0x080000 |
| fip | 0x080000 | 0x280000 |
| u-boot-env | 0x280000 | 0x300000 |
| factory | 0x300000 | 0x500000 |
| firmware | 0x500000 | 0x3c00000 |
| ustate | 0x3c00000 | 0x3c80000 |
| reserved | 0x3c80000 | 0x3e80000 |
| u-boot-env-1 | 0x3e80000 | 0x3f00000 |
| factory-1 | 0x3f00000 | 0x4100000 |
| firmware-1 | 0x4100000 | 0x7800000 |

The last 8 MiB belong to NMBM management. No UBI volume is needed: the kernel
NDM partition parser finds `Kernel_1` at 0x500000 and `RootFS_1` at 0x700000.
Read-only `printenv bootcmd` and `printenv mtdparts` both report undefined;
the boot menu dispatches the board's compiled boot path.

Two independently reproduced problems:

1. FIT was written at 0x5c0000 instead of 0x500000. Moving it makes `mtd read
   firmware` see FIT magic and `iminfo` verify the kernel and FDT CRC32/SHA1.
2. With that corrected address, KB-51 still prints exactly
   `*** Image verification failed ***`, tries image 2, and resets. Public
   KB-41 boots the identical, unmodified firmware normally, including FIT
   hash verification. This isolates a KB-51 pre-FIT compatibility problem;
   it is not an OpenWrt BL2 handoff failure or missing UBI. The image TLV
   contains a 72-byte DER signature; KB-51's inspected verifier constrains
   the signature length to 256..512 bytes. This explains the rejection,
   but we do not patch firmware, disable hash checks, or re-sign images.

## Fix

Pin the compatible, separately published KB-41 FIP (306784 bytes):
`8b8615b345d912187853fea6bef6b1868760fd52ed92ba7c3a380ad5533cbf08`.
Source: https://osvault.project-athens.com/files/keenboot/aarch64/KB-41/KeenBOOT_WR3000P_KB-41.bin

Ported remains pinned to the original 31588412-byte image:
`710e4a37b1aa9f3a65a5122edd0650f5c5ad3e6eca5df53b11ac28aab2664eb7`.

The builder starts with erased media and copies only the OpenWrt preloader,
then places FIP at 0x80000 and FIT/SquashFS at 0x500000. It retains a second
FIP at 0x3c0000 for the unmodified OpenWrt BL2's compiled search address.
That mirror occupies part of the **synthetic** factory partition. This is
an emulator bootstrap arrangement, never a physical flash image or usable
Wi-Fi calibration. Backing files retain OpenWrt names/sizes only as QEMU
transport containers; guest MTD numbering is independent.

The gate requires separate, ordered BL2, BL31, actual KeenBOOT, firmware
read at 0x500000, `Starting kernel`, `Linux version 4.9-ndm-5`, mounted
SquashFS and the exact NDMS 5.01.C.8.0-2 startup banner. Neither a board
model string nor the word `ndm` can substitute for userspace startup.

## Diagnostics and scope

UART `send_serial` supports `"enter": false` for menu keys. KB-41/51 accept
the `0` menu shortcut; Escape exits the menu toward default boot. The harness
connects early and drains socket output without echoing it. Raw UART, NAND,
FIP, EEPROM and firmware never become artifacts. CI emits fixed allowlisted
evidence labels and removes guest assets on the ephemeral runner.

KB-51 can reach Linux and mount root through an explicit diagnostic
`mtd read`/`bootm`, but that is not a stock-autoboot success. The regression
uses KB-41's normal autoboot, without injected boot commands.

Before KAS testing, separately prove a usable console, `ndmc`, `/opt`,
Entware/`opkg`, service readiness and reboot recovery. NDMS startup alone
does not establish these gates. Keep WAN offline and LAN on localhost.

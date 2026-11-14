# MT7621 / MIPSel stand (Xiaomi Mi Router 3G)

A reproducible MT7621 (MIPS 1004Kc, little-endian) QEMU stand built on the
existing upstream `mt7621-router` machine (`qemu-system-mipsel`, patches
`qemu-patches/0028`–`0063`).  It is the MIPSel counterpart of the ARM64
`lab/` flow: same scenario runner, same JSON/JUnit contract, same isolation
rules.  No new emulator was written; this directory only adds a build script,
a Ported NAND assembler and gate aggregation for the MT7621 preset family.

## What is proven (2026-11-14, see the gate table below)

| Gate | Status | Evidence |
|---|---|---|
| MT7621_QEMU_BUILD | PASS | `lab/mt7621/build_qemu_mipsel.sh`, QEMU v10.1.0 (`f8b2f64e`) + 65 patches |
| MT7621_OPENWRT_BOOT | PASS | `lab/scenarios/openwrt-mt7621-native-elf.json` via `lab/run.py` |
| MT7621_REBOOT | PASS | same scenario, second boot after `reset` |
| MIPSEL_NATIVE_ELF | PASS | `uname -m` → `mips`, `\x7fELF` of `/bin/busybox`, executed in the guest |
| PORTED_IMAGE_VALIDATION | PASS | `lab/mt7621/prepare_ported_mir3g.py` pinning + negative tests |
| NEGATIVE_IMAGE_REJECT | PASS | unit tests: wrong size/hash/magic/output refused |
| PORTED_KERNEL_BOOT | **BLOCKED** | KeeneticOS 4.9-ndm-5 kernel hangs at SMP bring-up (see below) |
| PORTED_NDMC_READY, MIPSEL_ENTWARE_READY, MIPSEL_SERVICE_RESTART, MIPSEL_PERSISTENCE | NOT_TESTED | prerequisite blocked |

**NDMS/Entware on Keenetic Ported remain BLOCKED.**  Native MIPSel ELF
execution was proven in the **OpenWrt** guest; that is *not* an Entware
check on KeeneticOS and must not be counted as one.

## Quick start (Linux x86_64, incl. WSL2)

```bash
# 1. build the patched mipsel QEMU (mipsel-softmmu only, ~10 min)
lab/mt7621/build_qemu_mipsel.sh /tmp/qemu-mipsel

# 2. OpenWrt baseline: needs a dump of the board bootloader (see below)
mkdir -p assets/stock-3g-breed && cp Bootloader.bin assets/stock-3g-breed/
tools/prepare-nand.sh --soc mt7621 --stock assets/stock-3g-breed \
  xiaomi_mi-router-3g 25.12.5 nand-mi-router-3g-breed

# 3. run the automated boot + native-ELF + reboot gate
QEMU_BIN=/tmp/qemu-mipsel/qemu-system-mipsel python3 lab/run.py \
  --emulator-root . \
  --scenario lab/scenarios/openwrt-mt7621-native-elf.json \
  --preset xiaomi-mi-router-3g-breed \
  --nand-dir nand-mi-router-3g-breed \
  --output-dir work/reports/openwrt-mt7621 \
  --max-seconds 2400

# 4. interactive console (Ctrl-A X exits)
QEMU_BIN=/tmp/qemu-mipsel/qemu-system-mipsel ./emulator.sh \
  -P xiaomi-mi-router-3g-breed -n nand-mi-router-3g-breed \
  -w offline -l user -u none
```

The scenario runner copies the flash folder into a disposable workspace
(source never modified), runs the guest with `-w offline -l user`
(`restrict=on`, loopback forwards only) and deletes the copy afterwards.

### Bootloader dump (local component, not committed)

OpenWrt ships no bootloader for the Mi Router 3G.  `tools/prepare-nand.sh
--stock DIR` needs a dump of the board `Bootloader` partition.  The stand
was verified with the public **Breed 1.2 (r1416)** build:

```
https://breed.hackpascal.net/breed-mt7621-xiaomi-r3g.bin
sha256 05ebb31fd889b148022346179346d91c60a1b4518bdc4d8b9717a62aec638bed
```

Name the file `Bootloader.bin` inside the `--stock` directory.  The stock
Xiaomi U-Boot also works (`xiaomi-mi-router-3g-uboot` preset) but was not
exercised in this cycle.

## Keenetic Ported 5.1.8 (BLOCKED — status and how to reproduce)

Ported for the Mi Router 3G is a Keenetic-style **dual-slot** NAND layout
(official install: Breed telnet, `flash write 0x180000 … 0x1AB3F00`,
`env set autoboot.command …`).  `lab/mt7621/prepare_ported_mir3g.py`
assembles it from **local** components with SHA-256 pinning and constructs
the Breed environment block (`CRC32(zlib)` over `[4..0x1000)` + `name=value`
pairs + zero padding — format proven against Breed's own empty-env flash
write: `CRC32(4092 zero bytes) == 0x603b0489`).  Result: **unattended boot**
of the Ported kernel via `boot flash 0x180000`.

```bash
python3 lab/mt7621/prepare_ported_mir3g.py \
  --breed   Bootloader.bin \
  --firmware Xiaomi-R3G_5.1.8.bin \
  --uconfig u-config.bin --ustate U-State.bin \
  --eeprom reserved_eeprom.bin \
  --out nand-ported-r3g
QEMU_BIN=... ./emulator.sh -P xiaomi-mi-router-3g-breed -n nand-ported-r3g \
  -w offline -l user -u none
```

Pinned local components (never committed):

| Component | Origin | SHA-256 |
|---|---|---|
| `Xiaomi-R3G_5.1.8.bin` | osvault.project-athens.com (md5 `88e60a64a7529e1e6583fa9fe35c202d` matches the site listing) | `05958b9786621ee014bb72551f6458d0672c93370cbce9aec3573c6bba038cb2` |
| `u-config.bin`, `U-State.bin`, `reserved_eeprom.bin` | `Xiaomi-R3G.7z` installer archive (same project, `Установка KeenBOOT`/`Keenetic` folders) | pinned in `prepare_ported_mir3g.py` |
| `breed-mt7621-xiaomi-r3g.bin` | breed.hackpascal.net | `05ebb31fd889b148022346179346d91c60a1b4518bdc4d8b9717a62aec638bed` |
| `KeenBOOT-Xiaomi_3G_1.8.1.bin` | same archive | `1573af518d5ac72bb20eab1310eec6a74c3ed23fd72e153915e41d8233edc16e` |

`reserved_eeprom.bin` is the public default EEPROM shipped by the Ported
project — not a personal device dump; Wi-Fi calibrations in the emulator
come from the cards' synthetic eFuse anyway (radio silent).

### The exact blocker

The Ported kernel boots (unattended through Breed):

```
Booting image from flash bank 0 at 0x180000 ...
Starting kernel at 0x813e3e44...
Linux version 4.9-ndm-5 (t.me/project_athens)
```

… and hangs in SMP bring-up, before any NDM output:

* 4 VPE (MT7621A profile): `Synchronize counters for CPU 2: done` →
  `random: crng init done` → silence.  `qemu.log` at the stall:
  `Wrong GIC offset at 0x80` (GIC_SH_CONFIG region), then GCR
  `0x4030 ← 0x40000000` (CPC base) and `GCR 0x20 read/write` → stop.
* `-smp 2` (one core / 2 VPE): gets further — `Brought up 2 CPUs` — then
  hangs in `sock_init → __kmem_cache_create → kick_all_cpus_sync →
  smp_call_function_single`: the IPI to CPU1 is never handled; RCU stall
  dump confirms CPU0 spinning in the IPI wait loop.  Same `Wrong GIC
  offset at 0x80` symptom.

OpenWrt 6.12 (same machine, same flash layout) brings up all 4 VPEs and
runs — the gap is specific to the KeeneticOS 4.9 kernel's SMP/IPI/GIC
interaction with the emulated GIC.  Two diagnosis-only experiments (NFI
0x64 FIFO-counter values, cumulative counter) gave no new information and
were **reverted**; the final binary is the pristine patched build.  GIC
work is out of scope for this cycle: the next step would be tracing which
GIC VPE-local/IPI register the 4.9 kernel polls while delivering
`call_func` IPIs, then extending `hw/intc/mips_gic.c` accordingly — only
with a concrete hypothesis.

A separate earlier observation: Breed's own flash writes fail in the
emulated NFI (`mt7621-nand: NFI core FIFO write operation timed out`
×4 during `env save`; erase lands, data never commits).  The static env
blob bypasses this.  Reads and `boot flash` are unaffected.

## Gate aggregation

```bash
python3 lab/mt7621/mt7621_gates.py --report-dir work/reports/mt7621-gates \
  --openwrt-report work/reports/openwrt-mt7621/report.json \
  --set MT7621_QEMU_BUILD PASS --reason MT7621_QEMU_BUILD qemu_mipsel_built_from_pinned_source \
  --set PORTED_IMAGE_VALIDATION PASS --reason PORTED_IMAGE_VALIDATION pinned_sha256_and_header_validated \
  --set PORTED_KERNEL_BOOT BLOCKED --reason PORTED_KERNEL_BOOT smp_ipi_hang_4_9_ndm_kernel \
  --set PORTED_NDMC_READY NOT_TESTED --reason PORTED_NDMC_READY prerequisite_blocked \
  --set MIPSEL_ENTWARE_READY NOT_TESTED --reason MIPSEL_ENTWARE_READY prerequisite_blocked \
  --set MIPSEL_SERVICE_RESTART NOT_TESTED --reason MIPSEL_SERVICE_RESTART prerequisite_blocked \
  --set MIPSEL_PERSISTENCE NOT_TESTED --reason MIPSEL_PERSISTENCE prerequisite_blocked \
  --set NEGATIVE_IMAGE_REJECT PASS --reason NEGATIVE_IMAGE_REJECT unit_tests_refuse_bad_inputs \
  --set HOST_ISOLATION PASS --reason HOST_ISOLATION offline_wan_loopback_lan_only \
  --set CLEANUP PASS --reason CLEANUP disposable_workspace_removed_after_run
```

Statuses are never upgraded by this script; BLOCKED stays BLOCKED.

## Limits

* No Wi-Fi airtime (cards are "radio silent" by design), no real EEPROM,
  no physical reboot/power-cut/USB-wear behaviour.
* Breed cannot persist its environment through emulated flash writes —
  the stand pre-constructs the env block instead.
* Keenetic Ported components stay local; nothing proprietary is committed.

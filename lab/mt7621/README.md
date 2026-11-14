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
| MT7621_REBOOT | PASS | same scenario: `reset` step + second kernel boot (`Linux version` after reset) |
| MIPSEL_NATIVE_ELF | PASS | explicit `/bin/busybox uname -m` executed in the guest, answer `mips` verified (echo-safe marker) |
| PORTED_IMAGE_VALIDATION | PASS | `lab/mt7621/prepare_ported_mir3g.py` pinning + negative tests |
| PORTED_KERNEL_BOOT | PASS | `lab/scenarios/ported-mt7621-ndms.json`: KeeneticOS kernel boots (derived from the report) |
| PORTED_KERNEL_SMP_4VPE | PASS | same scenario: `Brought up 4 CPUs` observed (full MT7621A 4-VPE profile) |
| PORTED_USERSPACE_READY | PASS | same scenario: `init process was started` |
| PORTED_NDMS_READY | PASS | same scenario: ndm `system ready` + the NDMS HTTP answer on the loopback LAN forward |
| PORTED_LAN_READY | PASS | same scenario: `probe_http 127.0.0.1:8080` answered by the NDMS web server |
| MIPSEL_NATIVE_ELF_ON_NDMS | PASS | same scenario: native MIPSel NDMS daemons started (telnetd, dropbear) |
| PORTED_REBOOT | PASS | same scenario: `reset` step + a second full boot to `system ready` |
| NEGATIVE_IMAGE_REJECT | PASS | unit tests: wrong size/hash/magic/output refused |
| HOST_ISOLATION | PASS | guest runs with `-w offline -l user`; forwards bound to 127.0.0.1 only |
| CLEANUP | PASS | the runner's disposable workspace copy is removed after the run |
| PORTED_NDMC_READY | NOT_TESTED | the Ported serial console is log-only; the ndm CLI over telnet (port 23) is not scripted yet |
| MIPSEL_ENTWARE_READY | NOT_TESTED | Entware installation is the next cycle's work, after NDMS |
| MIPSEL_SERVICE_RESTART, MIPSEL_PERSISTENCE | NOT_TESTED | service state across reboots is not scripted yet |

Totals: **15 PASS / 4 NOT_TESTED** of 19 gates (aggregate outcome BLOCKED while any
gate is not PASS).  Hardware acceptance (physical MT7621AT, full 4-VPE
profile) is a separate acceptance criterion and is not covered by
virtual evidence.  Native gates (`MT7621_OPENWRT_BOOT`, `MT7621_REBOOT`,
`PORTED_KERNEL_BOOT`, `PORTED_KERNEL_SMP_4VPE`, `PORTED_USERSPACE_READY`,
`PORTED_NDMS_READY`, `PORTED_LAN_READY`, `PORTED_REBOOT`,
`PORTED_NDMC_READY`, `MIPSEL_ENTWARE_READY`, `MIPSEL_NATIVE_ELF`,
`MIPSEL_NATIVE_ELF_ON_NDMS`, `MIPSEL_SERVICE_RESTART`,
`MIPSEL_PERSISTENCE`) can never be set to PASS from the controller —
`mt7621_gates.py` refuses `--set GATE PASS` for them; their PASS is
derived only from a scenario report validated against the committed
scenario (row order, actual `reset` + second boot, explicit ELF
execution with an echo-safe answer marker, the 4-VPE marker and the
NDMS HTTP answer).

**Native MIPSel ELF execution inside KeeneticOS is evidenced by the NDMS
daemons (ndm, telnetd, dropbear, Deadwood) starting and answering; it is
*not* an Entware check and must not be counted as one.**

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

## Keenetic Ported 5.1.8 (4-VPE boot to NDMS — status and how to reproduce)

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

### The SMP blocker and its fix (2026-11-14)

KeeneticOS 4.9-ndm-5 used to hang in SMP bring-up on the 4-VPE profile
(`Synchronize counters for CPU 2: done` → silence).  The root cause was
in the emulated GIC, not in the firmware:

* `hw/intc/mips_gic.c` filled `GIC_SH_CONFIG.NUMVPES` with the raw VPE
  count, while the MIPS GIC convention (and the adjacent `NUMINTRS`
  field) is **count − 1**.  The 4.9 kernel computes
  `gic_vpes = field + 1` and therefore believed in **five** VPEs.
* Its per-VPE GIC setup then wrote `GIC_VPE_OTHER_ADDR = 4` — a guest
  error (`Wrong GIC offset at 0x80`) — and the vendor counter-sync
  handshake (shared hwirqs `gic_shared_intrs − 2·gic_vpes … +3`, i.e.
  54–57 here) was never mapped to VPEs.  The sync request (hwirq 54,
  `map_vp = −1`) was silently dropped and the reply (hwirq 56) was
  routed to VPE0 — the boot CPU — which received and acknowledged its
  own IPI while CPU2 waited forever.  The instrumented QEMU trace shows
  the whole handshake.
* Fix (patch `qemu-patches/0065`): `NUMVPES = num_vps − 1`; `MAP_VP`
  stores any VPE bit like the real GIC (delivery to a nonexistent VPE is
  a natural no-op); `GCR_Cx_RESET_EXT_BASE` (UEB) is implemented because
  the kernel writes it before releasing a secondary core.  With the fix
  the kernel brings up **all four VPEs** (`Brought up 4 CPUs`), ndm
  reaches `system ready`, and the NDMS web server answers on the
  loopback LAN forward.

`-smp 1` booted NDMS even before the fix (it is the diagnostic baseline);
`-smp 2` remains a diagnostic-only mode: the firmware's device tree
describes the two-core MT7621A, so the 4.9 kernel still places its VPEs
by the two-core topology there.

A separate earlier observation: Breed's own flash writes fail in the
emulated NFI (`mt7621-nand: NFI core FIFO write operation timed out`
×4 during `env save`; erase lands, data never commits).  The static env
blob bypasses this.  Reads and `boot flash` are unaffected.

## Gate aggregation

```bash
python3 lab/mt7621/mt7621_gates.py --report-dir work/reports/mt7621-gates \
  --openwrt-scenario lab/scenarios/openwrt-mt7621-native-elf.json \
  --openwrt-report work/reports/openwrt-mt7621/report.json \
  --ported-scenario lab/scenarios/ported-mt7621-ndms.json \
  --ported-report work/reports/ported-mt7621-ndms/report.json \
  --set MT7621_QEMU_BUILD PASS --reason MT7621_QEMU_BUILD qemu_mipsel_built_from_pinned_source \
  --set PORTED_IMAGE_VALIDATION PASS --reason PORTED_IMAGE_VALIDATION pinned_sha256_and_header_validated \
  --set PORTED_NDMC_READY NOT_TESTED --reason PORTED_NDMC_READY serial_console_log_only_telnet_cli_deferred \
  --set MIPSEL_ENTWARE_READY NOT_TESTED --reason MIPSEL_ENTWARE_READY entware_is_next_cycle_after_ndms \
  --set MIPSEL_SERVICE_RESTART NOT_TESTED --reason MIPSEL_SERVICE_RESTART service_state_across_reboot_not_scripted \
  --set MIPSEL_PERSISTENCE NOT_TESTED --reason MIPSEL_PERSISTENCE config_persistence_not_scripted \
  --set NEGATIVE_IMAGE_REJECT PASS --reason NEGATIVE_IMAGE_REJECT unit_tests_refuse_bad_inputs \
  --set HOST_ISOLATION PASS --reason HOST_ISOLATION offline_wan_loopback_lan_only \
  --set CLEANUP PASS --reason CLEANUP disposable_workspace_removed_after_run
```

Statuses are never upgraded by this script; BLOCKED and NOT_TESTED stay
what they are, and `--set GATE PASS` is refused for native gates (see
the gate table above) — their PASS can only be derived from a scenario
report that matches the committed scenario.

## Limits

* No Wi-Fi airtime (cards are "radio silent" by design), no real EEPROM,
  no physical reboot/power-cut/USB-wear behaviour.
* Breed cannot persist its environment through emulated flash writes —
  the stand pre-constructs the env block instead.
* Keenetic Ported components stay local; nothing proprietary is committed.

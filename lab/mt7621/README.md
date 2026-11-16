# MT7621 / MIPSel stand (Xiaomi Mi Router 3G)

A reproducible MT7621 (MIPS 1004Kc, little-endian) QEMU stand built on the
existing upstream `mt7621-router` machine (`qemu-system-mipsel`, patches
`qemu-patches/0028`–`0065`).  It is the MIPSel counterpart of the ARM64
`lab/` flow: same scenario runner, same JSON/JUnit contract, same isolation
rules.  No new emulator was written; this directory only adds a build script,
a Ported NAND assembler and gate aggregation for the MT7621 preset family.

## What is proven (2026-11-15, see the gate table below)

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
| PORTED_NDMC_READY | PASS | `lab/scenarios/ported-mt7621-cli.json`: the ndm CLI over the loopback telnet forward answers (prompt greeting + verified `show version` release answer) |
| MIPSEL_ENTWARE_READY | PASS | `lab/scenarios/ported-mt7621-entware.json` via `lab/mt7621/entware_e2e.py`: full 32-step chain PASS (2026-11-15 diag4) — installer completion, real opkg answers, native `bash --version` (`mipsel-openwrt-linux-gnu`), `DOINSTALL_ABSENT`, `MARK42`, native `system reboot`, post-reboot `/opt ext4` + opkg + bash + marker; derived by `derive_entware_gates` from the report against the committed scenario; re-confirmed 33/33 by the SIGSEGV-free tz-skip cycle (see below) |
| MIPSEL_SERVICE_RESTART | PASS | `lab/scenarios/ported-mt7621-entware-service-restart.json` via `lab/mt7621/entware_e2e.py`: 40-step chain PASS (2026-11-15, see the service cycle below) — start/status generation 1 with recorded PID, saved pidfile, real restart, status generation 2, on-guest proof of different PID + `kill -0` liveness + counter 2 (`RESTART_VERIFIED`), then stop and cleanup (`LAB_CLEANED`); derived by `derive_service_restart_gate` |
| MIPSEL_PERSISTENCE | PASS | `lab/scenarios/ported-mt7621-entware-persistence.json` via `lab/mt7621/entware_e2e.py`: 47-step guest cycle PASS (2026-11-15, tested source SHA `4f831934`) — non-secret `/opt/etc/.lab-persist` written + `sync` before the native `system reboot`, re-read after it (`PERSIST_OK`, `/opt ext4`, opkg, native bash, `MARK42`), `sync` again, **QEMU monitor `system_reset`**, third boot with 4 VPE to `system ready`, and both `/opt/etc/.lab-persist` and `/opt/.lab-marker` unchanged after the reset; the native reboot and the QEMU reset are separate events in the guest log (only one `reboot: Restarting system` line, boot 3 follows the monitor reset without any init-shutdown lines); derived by `derive_persistence_gate` (`native_reboot_then_qemu_reset_opt_config_persisted`); evidence: `lab/mt7621/evidence/ported-mt7621-entware-persistence-20261115/` |
| MIPSEL_AUTOSTART (additional gate, **outside the 19-gate count**) | PASS | `lab/scenarios/ported-mt7621-entware-autostart.json` via `lab/mt7621/entware_e2e.py`: 59-step guest cycle PASS (2026-11-16, tested source SHA `abf9f25`, see the autostart cycle below) — the real `rc.unslung` execution measured as a live process on both a native reboot and a separate QEMU `system_reset`; derived by `derive_autostart_gate` (`native_reboot_then_qemu_reset_service_autostarted`); evidence: `lab/mt7621/evidence/ported-mt7621-entware-autostart-20261116/` |

Totals: **19 PASS / 0 NOT_TESTED** of 19 gates when the two independent
Stage C guest reports and all other required evidence are supplied to
the aggregator (aggregate outcome PASS). `MIPSEL_AUTOSTART` is a separate
additional gate: it is deliberately outside `GATES`/`NATIVE_GATES`, only
appears when its own scenario/report pair is supplied, cannot be labelled
by `--set` at all, and does not change the 19-gate count. Omitting any native evidence
still leaves that gate NOT_TESTED and the aggregate BLOCKED.  Hardware acceptance (physical MT7621AT, full 4-VPE
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

### The ndm CLI over telnet (2026-11-14)

The fresh Ported configuration exposes the ndm CLI on telnet:23 without
a login prompt ("administrative password is not configured"), and the
scenario runner drives it with the new `telnet` action (loopback forward
`8023:23`, IAC/ANSI filtering, one command per step, LF submits the
line — CR alone only echoes).  `lab/scenarios/ported-mt7621-cli.json`
proves a real CLI session, not just an open port: the `(config)>`
prompt greeting, `show version` with the verified pinned release answer
(`release: 5.01.C.8.0-2`) and `show system` (`hostname: Xiaomi_3G`);
`PORTED_NDMC_READY` derives PASS from that report.  A 15 s settle after
telnetd's first `waiting for connections` line is required: CLI sessions
opened in the first seconds can hang.  A companion scenario,
`ported-mt7621-cli-reboot.json`, drives the native `system reboot` from
the CLI (`Core::System::RebootManager: Activated system reboot.`) and
observes the second full 4-VPE boot on the UART — CLI-initiated reboot
evidence, separate from the QEMU-reset reboot.  Command sequences
(`send` as a list) on one session configure user contexts
(`user lab` → `password plain …` → tags), with `{password}`/`{username}`
placeholders resolved only from the private credentials file.

### Entware mipselsf on the virtual USB drive (2026-11-15, wedge bisected and resolved)

Proven with the `mipselsf` profile (pinned installer + 25 packages in
`lab/entware-mipselsf.lock.json`, mirror served on 127.0.0.1 and reached
by the guest through the `192.168.1.100:80` guestfwd).  The clean
bootstrap (pristine NAND assembled by `prepare_ported_mir3g.py`, fresh
USB ext4 image, complete 25-package mirror, strong builtin admin
password set and config saved via the ndm CLI before `opkg disk
KASOPT:/`) now reaches much further than the 2026-11-14 partial run:

* USB detected, ext4 mounted to `/opt`
  (`Opkg::Manager: /tmp/mnt/1111… mounted to /opt/.`);
* `npkg` inflates both seeded installer archives; doinstall runs;
* **all 16 base packages install natively from the pinned mirror**,
  including `findutils` (the 2026-11-14 failure point) and `opt-ndmsv2`
  (which ships `rc.unslung`); SSH keys generated ([3/5]);
* `[4/5]` echoes (Setting timezone, script initrc and starting
  "dropbear") — and then the script produces **no further output for
  600 s**: the first statement after that echo is the unbounded TZ RCI
  GET (`wget -qO - localhost:79/rci/show/clock/date/tz`), and the RCI
  POST (`opkg initrc` + configuration save) never executes (no
  `configured init script`, no `saving (http/rci)`).

Discrimination result (Issue #76's narrow goal): the failure is a
**reproducible clean-bootstrap problem, not a dirty-recovery problem** —
2/2 clean runs wedge at the same point.  The repeating Deadwood
`Got signal 1` messages are **not** the wedge: they run at a steady
~15 s rate throughout (first seen during normal CLI-session config
updates, correlated with `Http::Manager: updated configuration` and
MT7621 Wi-Fi ACS/channel-switch events; the ARM64 reference stand shows
**zero** such reloads), continue unchanged after the wedge, and did not
stop ndm from installing 16 packages.  The section below records the
follow-up diagnostic cycle that bisected and resolved this wedge.

The lab patch now bounds the TZ probe (`wget -qT 3`) and brackets the
[4/5] tail with `/dev/console` markers (`lab-tz-begin/end`,
`lab-dropbear-begin`, `lab-post-begin`) — the write path the `[5/5]`
marker already proved lands on the UART even when the doinstaller stdout
bridge stalls — so one follow-up run bisects the wedge to the exact
statement.  Drive the whole cycle with `lab/mt7621/entware_e2e.py`
(fresh workspace; pristine NAND; committed scenario
`lab/scenarios/ported-mt7621-entware.json`).

### The follow-up diagnostic cycle (2026-11-15, Issue #76): the wedge is the doinstall TZ RCI GET window

One clean diagnostic run on merged main (`entware_e2e.py`, freshly
assembled pristine NAND, fresh USB image, mirror rebuilt from the pinned
25-package lock) fired **all five markers in order** (`lab-tz-begin` →
`lab-tz-end` → `lab-dropbear-begin` → `[5/5]` → `lab-post-begin`) and
reached `configured init script` in 87 s — the [4/5] wedge is the
unbounded TZ RCI GET, exactly as bisected.  The run then failed only on
the committed `bash --version` answer marker, which offline `strings`
proof showed can never match: the Entware mipselsf bash 5.3.20(1)
carries the triplet `mipsel-openwrt-linux-gnu` and no
`mipsel-unknown-linux` string at all (the only "unknown" strings are
bash error messages).  The scenario marker was corrected and the next
full cycle **passed 32/32 in 545 s**: real `opkg print-architecture`
(`mipsel-3.4_kn 200`), `opkg list-installed`, `opkg install bash`,
native `bash --version`, `DOINSTALL_ABSENT`, `MARK42`, native
`system reboot`, second 4-VPE boot to `system ready`, and post-reboot
persistence (`/opt ext4` mount, opkg, bash, marker).  `MIPSEL_ENTWARE_READY`
derives PASS from that report (`derive_entware_gates`).

Why the RCI GET was never served (verified, not inferred):

* Outside the doinstall window the same endpoint answers immediately
  (`GET /rci/show/clock/date/tz` → `200 OK`, JSON with
  `"locality": "Europe/Moscow"` — live probe on a disposable boot of the
  same NAND image, plus `GET /rci/show/clock` → JSON).  The request
  format is valid for this firmware.
* Inside the doinstall window ndm never answers it: with the unbounded
  wget the UART went silent for 600 s (2/2 runs); with `-qT 3` the
  timeout fires at 3 s — and busybox wget 1.38 (MIPSel) then segfaults
  in its own timeout path (`do_page_fault(): sending SIGSEGV to wget`,
  `epc = 00485adc in busybox`), visible on the UART.  The script
  survives the segfault (empty command substitution) and continues.
* The POST to the same `localhost:79` one statement later **is** served
  during the same window (`configured init script` + `saving (http/rci)`
  2–3 s after the segfault, no second page fault in the UART).

So the blocking operator is the doinstall TZ RCI GET statement, and the
non-serving is specific to the synchronous doinstaller execution window
— most plausibly the RCI show handler waits on state held by the
running Opkg::Manager script (a circular wait: the GET waits for
doinstall to finish, doinstall waits for the GET answer), while the
POST path is exempt.  That lock mechanism is a hypothesis about the
proprietary ndm binary and is not provable from outside; what is proven
is the window asymmetry itself (healthy endpoint, unbounded GET hang,
bounded-timeout SEGV, served POST seconds apart).  The bounded probe is
a workaround with verified end-to-end effect, not a claim that the ndm
side was fixed; the timezone value is simply left empty by the installer
in that case, which does not block the deployment.

### The SIGSEGV-free fix cycle (2026-11-15, Issue #82): the optional TZ GET is skipped on mipsel

The bounded `-qT 3` workaround removed the wedge but still made BusyBox
wget 1.38 (MIPSel) segfault in its own timeout path on every run —
harmless to the install, yet a crash in every UART log.  The fix cycle
replaces the optional TZ GET with an explicit skip **in the mipsel lab
bootstrap only** (`lab/build_entware.py`): `timezone=""` plus a
`lab-tz-skipped` console marker, bracketed by `lab-tz-begin`/`lab-tz-end`
exactly where the GET used to sit; aarch64 keeps the bounded probe, and
the patch refuses a rotated MIPSel installer without the known TZ line
(fail-closed, unit-tested).  The new committed scenario
`lab/scenarios/ported-mt7621-entware-tz-skip.json` (33 steps) inserts a
UART wait on `lab-tz-skipped` with `reject_pattern` for
`do_page_fault()/Segmentation fault/SIGSEGV`, and that rejection covers
the post-TZ stage too.

One clean guest cycle on a fresh NAND copy and a newly built ext4 image
(`entware_e2e.py`, pinned 25-package mirror) passed **33/33 in 596 s**:
the doinstall window closed in ~2 s (`[4/5]` → `lab-tz-skipped` →
`lab-dropbear-begin` → `[5/5]` → `configured init script` + `saving
(http/rci)`), real `opkg`, native bash, `DOINSTALL_ABSENT`, native
`system reboot`, post-reboot `/opt ext4` + opkg + bash + `MARK42`.  A
scan of the **entire** UART log found zero `wget` output lines, zero
busybox errors and zero SIGSEGV/page-fault/oops events — the only four
pattern hits are the kernel module name `mtdoops_proc.ko` (2 boots).
Sanitized evidence: `lab/mt7621/evidence/ported-mt7621-entware-tz-skip-20261115/`.

Honest scope: the TZ value is still empty after install, the ndm-side
GET/POST window asymmetry stands exactly as described above, and the
skip is confined to the lab bootstrap — production images and the
aarch64 path are untouched.

### The Stage C service-restart guest cycle (2026-11-15, Issue #81 / PR #85): disposable service start/restart/PID proof

The committed scenario
`lab/scenarios/ported-mt7621-entware-service-restart.json` (40 steps)
keeps the whole 33-step TZ-safe clean/bootstrap/reboot baseline and then
installs a **disposable** `/bin/sh` probe service `S98labprobe` from an
echo-safe base64 blob (busybox-only, `/opt/var/run` pidfile + generation
counter; never shipped by any real image).  The appended seven SSH steps
prove, in one run: decode + install (`LAB_SERVICE_READY`), start + status
reporting `RUNNING 1 <pid>` with a live process, the pidfile copied aside
(`FIRST_SAVED`), a real restart with status `RUNNING 2 <pid>`, and the
on-guest contract step that exits 0 and echoes `RESTART_VERIFIED` only if
the old PID differs from the new one, `kill -0` confirms the new process
is alive and the counter reads 2 — then `LAB_CLEANED` (stop, pidfile
removed, service and scratch files deleted).

One clean guest cycle on a fresh NAND copy and a newly built ext4 image
(`entware_e2e.py`, pinned 25-package mirror) passed **40/40 in 562 s**
on PR #85 HEAD `b32f803`: the TZ-safe doinstall window closed in ~2 s
again, the native `system reboot` + second boot + Entware persistence
checks all held, and the whole service chain took under 5 s total.
A scan of the **entire** UART log again found zero `wget` output lines,
zero busybox errors, zero `Critical error` and zero real SIGSEGV/
page-fault/oops events — the only four pattern hits are the kernel
module name `mtdoops_proc.ko` (2 boots).  Numeric PID values are
deliberately not persisted (the runner keeps guest output out of
reports; step 40 deletes the pidfiles), which is why the proof is the
committed on-guest contract validated by `verify_ssh_response`.
Sanitized evidence:
`lab/mt7621/evidence/ported-mt7621-entware-service-restart-20261115/`.

Honest scope: this cycle validates `MIPSEL_SERVICE_RESTART` only.  It
does **not** independently prove post-boot `rc.unslung` autostart
(`MIPSEL_PERSISTENCE` separately proved by the 47-step PR #84 guest cycle), the
probe service itself is not installed across the reboot (start happens
post-boot, stop happens before the run ends), and the restart was of the
probe service only — Dropbear itself was never restarted.

### The Stage C persistence guest cycle (2026-11-15, Issue #81 / PR #84): native reboot + QEMU reset

After the validated MIPSel-only optional TZ GET skip (merged PR #83),
the Stage C persistence scenario is `lab/scenarios/ported-mt7621-entware-persistence.json`
(**47 steps**). One isolated guest cycle on 2026-11-15 (tested source SHA
`4f831934`, CI 4/4 green) ran it to **PASS 47/47, 797.19 s**: a non-secret
`PERSIST_OK` file under `/opt/etc` written + `sync`ed before the native
`system reboot`, re-read after that reboot together with `/opt ext4`, native
`opkg` and native `bash`, then `sync`, a **distinct** QEMU monitor
`system_reset` (runner action `reset`; console EOF is marked before the
monitor command), a third boot with 4 VPE to `system ready`, and both
`/opt/etc/.lab-persist` and `/opt/.lab-marker` unchanged after the reset.
The two reset events are separate in the guest log: exactly one
`reboot: Restarting system` line (between boots 1 and 2), and boot 3 follows
the monitor reset with no init-shutdown lines at all. The full UART scan
found zero crash-pattern hits (no SIGSEGV/do_page_fault/oops/panic and no
`mtdoops_proc` false positives in this log, 0 wget/busybox errors).
`MIPSEL_PERSISTENCE` is derived `PASS` (`native_reboot_then_qemu_reset_opt_config_persisted`)
by `derive_persistence_gate` from the real report — no manual gate override.

Sanitized evidence: `lab/mt7621/evidence/ported-mt7621-entware-persistence-20261115/`.

Honest scope: **actual `rc.unslung` service autostart is still NOT_TESTED.**
The log shows ndm applying the setting once per boot (`configured init
script: /opt/etc/init.d/rc.unslung` ×3), but no 47-step probe observes a
live Entware service after boot 2 or boot 3 — the bash/opkg checks prove
package and data persistence, not autostart. Issue #81 needs a separate
cycle with a persistent init script writing a boot marker (or a live
service probe) right after `system ready` to measure it directly.
Keep NAND, firmware, root credentials and the full UART private. Do not
substitute standard GitHub CI (which tests scenario contracts but does
not execute this private guest) for Stage C acceptance.

### The Stage C autostart guest cycle (2026-11-16, Issue #81 / PR #86): real rc.unslung execution, generation 1 then 2

This closes the remaining Stage C item: PR #85 proved a manually started
service, PR #84 proved config persistence — neither proved that KeeneticOS
**executes** the configured Entware init script at boot.  The pinned
`rc.unslung` was first read verbatim from the committed lock package
(`opt-ndmsv2_1.0-17_mipsel-3.4_kn.ipk`): it takes `{start|stop|...}`, then
runs `find /opt/etc/init.d/ -perm -u+x -name 'S*' | sort` and **sources**
every match in one shell (`. $i $ACTION $CALLER`) — so a boot probe must
wrap all logic in functions and must not use `set -e/-u` or `exit`.  The
disposable `lab/mt7621/fixtures/S99labboot` probe starts a `sleep 3600`
worker, records `/opt/var/run/labboot.pid`, increments a persistent
`/opt/var/run/labboot.gen` boot-generation marker, echoes
`labboot-autostart gen=…` to `/dev/console`, and identifies any
pre-recorded PID via `/proc/<pid>/cmdline` (stale-pidfile/PID-reuse safe).

`lab/scenarios/ported-mt7621-entware-autostart.json` (**59 steps** = the
tz-skip baseline through `MARK42`, then the autostart chain): the probe is
seeded on boot 1 with no manual start, a negative step proves no
pidfile/generation file/worker exists yet (`LABBOOT_OFF`), the baseline
native `system reboot` is the autostart trigger, each post-reboot boot
first waits for the probe's **UART execution marker**
(`labboot-autostart gen=1` / `gen=2`) and only then proves `/opt ext4`,
`LABBOOT gen=N pid=…`, the generation file exactly 1 then exactly 2, a
live worker via `/proc/<pid>/cmdline`, exactly one worker, and records
both PID numerals (`PIDS old new` — never asserted: PID reuse across
reboots is legal).  One isolated guest cycle on 2026-11-16 (tested source
SHA `abf9f25`, CI green on that SHA) ran it to **PASS 59/59, 744.57 s**:
boot 2 console line 3139 `labboot-autostart gen=1 pid=701`, boot 3 console
line 4551 `labboot-autostart gen=2 pid=702`; ndm mounts the USB disk to
`/opt` ~26-29 s **after** `system ready` and runs `rc.unslung` right after
the mount (the marker waits make that asynchronous flow deterministic).
The two reset events are separate in the guest log (only one
`reboot: Restarting system` line at 1777; boot 3 follows the monitor reset
with no init-shutdown lines).  Full UART scan: 6 crash-regex hits, all the
benign `mtdoops_proc` module-name false positives, no true faults;
0 wget/critical hits.  `MIPSEL_AUTOSTART` is derived `PASS`
(`native_reboot_then_qemu_reset_service_autostarted`) by
`derive_autostart_gate` from the real report — the gate is deliberately
outside the 19-gate `GATES` tuple (the count is unchanged) and
`apply_labels` refuses any `--set` for it, so a PASS can only come from a
validated scenario report.

Two earlier cycles on the same stand are part of the record, both
precisely diagnosed host-side and fixed with one targeted change each:
the scanner self-match (an inline SSH command looping over `/proc/[0-9]*`
with `*sleep*3600*` matched its own command line — fixed by a
concatenation-built needle and a regression test), and the proof race
(the SSH probes ran ~1-2 s after the disk mount, ahead of the
asynchronous `rc.unslung` — fixed by the UART marker waits).

Sanitized evidence: `lab/mt7621/evidence/ported-mt7621-entware-autostart-20261116/`.

Honest scope: this proves the rc.unslung autostart mechanism for a
disposable probe service in the emulator (one boot-triggered live process
per boot, generation-tracked).  It does not cover other Entware init
scripts, service supervision/restart policies, or physical hardware.

### The TeleMT MIPSel pinned-IPK guest cycle (2026-11-16, Issue #91 / PR #93): opkg install PASS, Core local run BLOCKED

`lab/mt7621/telemt_lab.py` delivers the two frozen experimental TeleMT IPKs
of entware-go Draft PR #44 (Actions run 43742529964, SHA-pinned, never
rebuilt) through the loopback-only mirror, installs them with the guest's
own native opkg, runs the PR #45 negative CLI contracts on the real guest
ELF, then starts Core as a loopback-only daemon with a synthetic
configuration.  Steps 0-32 of `lab/scenarios/ported-mt7621-telemt.json`
are the committed tz-skip clean-Entware baseline verbatim (33/33), so every
cycle re-proves the whole Entware foundation first.

Three real guest cycles ran (2026-11-16).  Cycles 1-2 (first round) and
cycle 3 (the instrumented diagnostic round) all PASS through the opkg/CLI
stages: clean baseline, in-guest dual SHA verification of both IPKs and
both installed ELFs against the full pins (the round-2 two-printf manifest
erratum is fixed — ONE printf builds each checksum file, so the guest now
really verifies BOTH artifacts), native `opkg install` of telemt 3.5.17-1
and telemt-panel-lite 1.0.0~rc.4-1 with `opkg status` version checks,
`healthcheck --mode=lab-probe-invalid` -> EXIT 2 with the exact upstream
text, and the malformed-TOML `run` -> EXIT 1 + "unclosed table" with no
panic, and `telemt start ...` exiting 0.

Cycle 3 then FAILS at `DAEMON_PROC_ALIVE` — but this time the exact crash
was captured first: the new FOREGROUND diagnostic step (commit c53237e)
runs `telemt start --foreground` with stdio attached and dumps the capture
to the UART before the real daemon start.  The child dies because
**rand 0.10.2's ThreadRng cannot initialize — the Ported KeeneticOS kernel
(`Linux version 4.9-ndm-5`) does not expose getrandom(2) (ENOSYS, os error
89)**, while its `/dev/urandom` driver works ("random: crng init done").
Both a tokio worker and the main thread panic at
`rand-0.10.2/src/rngs/thread.rs:163` ~1.5 s after daemonize; stderr goes to
/dev/null in daemon mode, so the tracing file log ends at the
"Runtime generation channel closed before API bootstrap" warning and both
previous cycles' BLOCKED was log-less.  The qemu-user ENOSYS panic
documented below is the same failure class (there qemu-user does not
emulate the MIPS getrandom syscall; here the guest kernel itself lacks it).
The stage stays **BLOCKED**: the fix requires an upstream rand
getrandom-ENOSYS fallback or a getrandom-capable kernel — neither is
in scope for pinned IPKs and a pinned firmware; an LD_PRELOAD getrandom
shim was considered and rejected as a C-ABI fake that would misrepresent
the platform result.  Full evidence and analysis:
`lab/mt7621/evidence/ported-mt7621-telemt-20261116/telemt-blocked-diagnosis.md`
(report-diagnostic.json + junit-diagnostic.xml: 46 rows, 45 PASS, 1 FAIL,
598.6 s).

MIPSEL_TELEMT (`derive_telemt_gate`) stays outside GATES/NATIVE_GATES —
the 19 core gates are unchanged, `apply_labels` refuses any `--set` for
it, and it can only report from a validated scenario report (currently
FAIL/NOT_TESTED paths, never an invented PASS).  The cloud guest stage
(`mt7621-telemt-lab.yml`) activates as soon as the repository secret
`TELEMT_IPK_TOKEN` (actions:read on saymer-alt/entware-go) exists; the
acquisition job fails closed on any pin deviation and the guest job is
intentionally skipped without the token — no rebuild, no approximation.

Also documented for entware-go: under qemu-mipsel 10.2.1 the Core server
path panics at rand 0.10.2 ThreadRng init (`Function not implemented`,
os error 89) right after the orchestrator's buffer-budget line — the
RNG-seeding syscall is unavailable under qemu-user, so the full server
path cannot be probed host-side (PR #45 never started the server, so this
was previously invisible).  The diagnostic guest cycle upgraded this from
a qemu-user-only note to the real-platform root cause: the Ported 4.9-ndm-5
kernel itself does not expose getrandom(2), so rand 0.10.2-based builds
cannot start on KeeneticOS MT7621 at all.

Honest scope: real native opkg installation of the experimental pinned
IPKs on the Ported MT7621 guest is PROVEN (three cycles, dual in-guest SHA
verification now included); the Core daemon startup inside KeeneticOS
Ported is BLOCKED with a proven root cause (missing getrandom(2) on the
4.9-ndm-5 kernel, rand 0.10.2 ThreadRng panic) that only an upstream or
kernel-side fix can remove; the reboot/rc.unslung autostart stage of the
same configuration is NOT_TESTED; Panel start/HTTP and the real Telegram
upstream remain NOT_TESTED.

## Gate aggregation

```bash
python3 lab/mt7621/mt7621_gates.py --report-dir work/reports/mt7621-gates \
  --openwrt-scenario lab/scenarios/openwrt-mt7621-native-elf.json \
  --openwrt-report work/reports/openwrt-mt7621/report.json \
  --ported-scenario lab/scenarios/ported-mt7621-ndms.json \
  --ported-report work/reports/ported-mt7621-ndms/report.json \
  --cli-scenario lab/scenarios/ported-mt7621-cli.json \
  --cli-report work/reports/ported-mt7621-cli/report.json \
  --service-scenario lab/scenarios/ported-mt7621-entware-service-restart.json \
  --service-report work/reports/ported-mt7621-service/report.json \
  --persistence-scenario lab/scenarios/ported-mt7621-entware-persistence.json \
  --persistence-report work/reports/ported-mt7621-persistence/report.json \
  --autostart-scenario lab/scenarios/ported-mt7621-entware-autostart.json \
  --autostart-report work/reports/ported-mt7621-autostart/report.json \
  --set MT7621_QEMU_BUILD PASS --reason MT7621_QEMU_BUILD qemu_mipsel_built_from_pinned_source \
  --set PORTED_IMAGE_VALIDATION PASS --reason PORTED_IMAGE_VALIDATION pinned_sha256_and_header_validated \
  --entware-scenario lab/scenarios/ported-mt7621-entware-tz-skip.json \
  --entware-report work/reports/ported-mt7621-tz-skip/report.json \
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

# MIPSel stand results (MT7621 / Xiaomi Mi Router 3G, Ported 5.1.8, ABI `mipsel-3.4`) — 2026-11-18

Controller: `lab/mt7621/busybox_matrix.py` (this branch). Four scenarios,
each on a pristine NAND copy + freshly built EXT4 `/opt` + pinned 25-package
mirror (busybox **1.38.0-5**): `fresh`, `transition`, `feedfail`, `interrupt`.
Scenario part of every run: **17/17 steps PASS** (`RESULT PASS`,
`uart-report.json`). Verbatim evidence: `lab/mt7621/evidence/
ported-mt7621-busybox-matrix-20261118/<kind>/evidence.json` (+ `junit.xml`).

## fresh — clean bootstrap and no-op update paths (16 entries, all PASS except OBSERVED)

| Observation | Result |
|---|---|
| Fresh pinned bootstrap installs busybox **1.38.0-5** (`P01_FRESH_BOOTSTRAP_STATE`) | PASS |
| `opkg status busybox` right after bootstrap: **`install hold installed`** — the installer leaves busybox **held** | OBSERVED (baseline of all 4 runs) |
| `/opt/bin/sh` symlink valid; `sh -c` exec OK; chmod/rm/sleep/ls applets OK | PASS |
| `opkg update` against the pinned mirror (`P02`) | PASS ("Updated list") |
| `opkg install busybox` at current version = no-op (`P03`) | PASS (rc 0) |
| `opkg install --force-reinstall busybox` (`P10`) — real prerm/postinst cycle; `.busybox.opkg-keep` mechanism ran; `already exists`-class output present | PASS; shell OK after |
| KAS preflight on the healthy system: exit **2** = `WARN wget-capability` (busybox wget has no HTTPS, Entware #887) + `UNKNOWN opkg-view` (**the hold flag** makes opkg-view unable to see `install ok installed`) | OBSERVED |
| Native `system reboot` → second boot → `/opt ext4`, opkg, 1.38.0-5, shell OK (`P11`) | PASS |
| Second `opkg update && opkg upgrade busybox` — idempotent no-op (`P12`) | PASS |

## transition — real version transition + hold + conffile (24 entries)

Baseline: fresh 1.38.0-5 → real `opkg install --force-downgrade` of the pinned
1.38.0-3 IPK (test feed, sha-verified) → hold → `opkg update; opkg install
busybox; opkg upgrade` → unhold → reboot → idempotence.

| Observation | Result |
|---|---|
| `P04_DOWNGRADE_TO_OLD`: real opkg downgrade → **1.38.0-3**; `/opt/bin/.busybox.opkg-keep` present (the 1.38.0-5 `prerm` hard-link mechanism preserved the old binary and kept every applet link working through the swap) | PASS |
| KAS preflight after downgrade | rc 2 (same WARN+UNKNOWN class) |
| `P08_HOLD_SET`: hold while 1.38.0-3 installed | PASS (`install hold installed`) |
| `P07A opkg update`: feed index offers 1.38.0-5 | PASS |
| **`P07B opkg install busybox`: UPGRADED TO 1.38.0-5 DESPITE `hold`** — the advised field step ignores the hold flag; output carries the field-case `already exists` warning class | PASS (rc 0) — **hold is not a protection against explicit install** |
| `P07C mass opkg upgrade` (busybox still held) | PASS, clean |
| `P08_UNHOLD`: `install user installed` | PASS |
| Final state 1.38.0-5; `/opt/bin/sh` valid; applets OK; fresh SSH exec OK; `.busybox.opkg-keep` cleanup visible | PASS |
| Reboot → 1.38.0-5 + shell OK (`P11`); second `opkg upgrade busybox` idempotent (`P12`) | PASS |
| **`N14`**: modified `dropbear.conf` + `opkg install --force-reinstall dropbear` → **`/opt/etc/config/dropbear.conf-opkg` backup created** (field-observed mechanism reproduced; dropbear survived, SSH reconnected) | OBSERVED |

## feedfail — outage and TLS failure modes (14 entries)

| Observation | Result |
|---|---|
| `N07`: `/entware` source pointed to a closed port → `opkg update` **fails with nonzero rc** (the `/keenetic` feed on the same mirror still answered — partial failure recorded honestly); installed busybox unchanged, shell intact | OBSERVED + PASS (state unchanged) |
| `opkg install --force-reinstall bash` with broken feed: **refused cleanly (rc 255)**, no package modified | OBSERVED |
| Feed restored → `opkg update` OK | PASS |
| `N08`: fresh bootstrap has **no `wget-ssl`/`curl`** at all (rc 127 inventory) — TLS failure mode on a fresh install is "no TLS client exists"; busybox wget is HTTP-only (matches preflight `WARN wget-capability`, Entware #887) | OBSERVED |
| KAS preflight after both failure scenarios | rc 2 (unchanged WARN+UNKNOWN) |

## interrupt — maintainer-script window, kill, recovery (15 entries)

| Observation | Result |
|---|---|
| `N01_PRERM_WINDOW` (one persistent session): installed `busybox.prerm` remove-branch executed outside opkg → applet links deleted (`CHMOD_PRESENT_41` = chmod **gone**); **KAS preflight exit 1 = hard FAIL in the broken state** (detection works); package-owned `busybox.postinst` (pinned aside before, sha-checked) restored links (`RECOVERY_40`, `SH_RESTORED_40`) | OBSERVED — reproduction + recovery |
| `N01_NEW_SSH_SESSION` after in-channel recovery: fresh SSH exec, `rm`/`sleep` OK | PASS |
| KAS preflight after recovery: rc 2 (back to the WARN+UNKNOWN baseline, **not** rc 1) | OBSERVED |
| `N10`: `kill -9` opkg mid-reinstall → `wait` reported 137 (SIGKILL); database stayed consistent (`Version 1.38.0-5`, `hold`), shell intact | OBSERVED |
| `N15`: re-running `opkg install --force-reinstall busybox` completes cleanly (`already exists` class), state `install hold installed`, shell OK | PASS |
| KAS preflight final | rc 2 (baseline) |

## Cross-run invariants

- `/opt/bin/sh`, `chmod`, `rm`, `sleep` were functional after **every** completed
  operation; the only broken windows were the ones deliberately created
  (remove-branch `prerm`).
- The KAS preflight tri-state matched system health in every observation:
  rc 1 = broken (FAIL lines), rc 2 = alive-with-warnings (hold flag →
  `UNKNOWN opkg-view`, busybox-only wget → `WARN wget-capability`), and it
  never repaired anything.
- `opkg flag hold busybox` is already present on a fresh Entware install and
  does **not** protect against `opkg install busybox` with a version delta.

## Fixed inputs used (all fail-closed verified)

bootstrap mirror `lab/entware-mipselsf.lock.json` (busybox 1.38.0-5, sha
`0ba41200…`); baseline IPK `busybox_1.38.0-3_mipsel-3.4.ipk` sha
`29954f847e9442fc30cebf6bc7dd17a1fc1d391c2fd037c6df582a30c0e40645` from
`https://test.entware.net/mipselsf-k3.4/`; KAS preflight
`development@0bbe0cd` sha `defb91a0…` re-verified in-guest after transfer;
QEMU `v10.1.0-65-g955c37b` (source `f8b2f64e` + `qemu-patches/0028–0065`);
Ported firmware `05958b97…`, Breed `05ebb31f…`; controller commit recorded in
the branch history.

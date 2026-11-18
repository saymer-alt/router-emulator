# AArch64 stand (MT7981 / Cudy WR3000P Ported 5.1.8, ABI `aarch64-3.10`) — 2026-11-18

Night-window results. All runs are isolated QEMU guests; nothing outside the
disposable NAND/EXT4/credentials was touched.

## What ran in this window (evidence committed)

`lab/ported_e2e.py` default stage chain, fresh private workspace, patched QEMU
`v10.1.0-65-g955c37b` (source `f8b2f64e` + repo patch series), pinned assets:

- `ported.bin` WR3000P 5.1.8 `710e4a37…`, KeenBOOT KB-41 FIP `8b8615b3…`
- synthetic Config provision + throwaway `admin` credentials (192-bit random)

**41/41 scenario steps PASS** (`cli-persistence-report.json`):
provisioned firstboot → all eight boot stages → authenticated CLI
(`show version` answered `release 5.1.C.8.0`, model **KN-3811**, hw_id KN-3811)
→ configuration save → reset → second full boot → second authenticated
version check.

| Gate (from `gates.json`) | Status | Reason |
|---|---|---|
| BOOT_READY | PASS | boot_stages_observed |
| AUTH_READY | PASS | authenticated_version_and_reboot |
| ENTWARE_READY | FAIL | runner_failure — **infra artifact, not a guest failure** (see below) |
| KAS_* gates | BLOCKED | prerequisite_blocked (entirely out of scope tonight) |

**Why ENTWARE_READY is not claimed:** the Entware bootstrap phase of
`ported_e2e.py` builds its mirror inside the same process as the guest run.
To run both stands concurrently on one host, every run was wrapped in a
private Linux network namespace (loopback only, no external routes). The
in-guest work had already proven boot+auth, but the in-process mirror build
then had no DNS, so the aarch64 Entware bootstrap could not start inside the
namespace. This is recorded honestly as a controller-infrastructure
limitation of this night window, **not** as an Entware/OPKG failure.

## Entware/BusyBox lifecycle evidence for AArch64 (existing, reused)

The task forbids re-running an unchanged system just to reproduce known PASS.
The AArch64 Entware + BusyBox transition evidence already exists as public CI
of this repository and is reused per the token-economy rules:

- Run [`42244661660`](https://github.com/saymer-alt/router-emulator/actions/runs/42244661660)
  (2026-11-06, job "Disposable QEMU BusyBox 1.37 to 1.38 / ported-lab", conclusion
  success, guest controller head `ca63bad`):
  - `ENTWARE: native shell, real EXT4 and pinned opkg database verified`
  - `ENTWARE: authenticated SSH, filesystem and packages survived reset`
  - `native 1.37 prerm interruption + postinst recovery PASS`
  - `native 1.38 upgrade left /opt/bin/sh and applets missing; recovered
    in-guest with the pinned old postinst`
- PR #56 (`lab/issue356-native-busybox-transition-20261104`) documents the
  full contract: pinned 1.37.0-6 bootstrap (sha `881b06ce…`), local-IPK
  1.38.0-3 (sha `4d69a414…`), real guest `opkg install`, prerm-deletes-`sh`
  window observed via `/opt/bin/busybox sh` (system `/bin/sh` becomes unusable
  for script files: `Bad interpreter "/opt/bin/sh"` with exit 0), KAS preflight
  FAIL in the broken state and its recovery exit code, all on real packages.

## AArch64 scenario matrix status (this window)

| Scenario | Status | Evidence |
|---|---|---|
| Boot / AUTH / reset persistence (P11-class) | PASS (tonight) | `cli-persistence-report.json`, gates.json |
| Fresh Entware bootstrap (P01/P02) | NOT_TESTED tonight (netns DNS limitation); PASS 2026-11-06 CI run 42244661660 | run log + `docs/entware-busybox-transition-issue356.md` |
| `opkg install busybox` at current version (P03) | NOT_TESTED tonight | — |
| Real version transition 1.38.0-3 → 1.38.0-5 (P04/P05/P07) | NOT_TESTED tonight; 1.38.0-3 local-IPK transition PASS 2026-11-06 | run 42244661660 + PR #56 doc |
| Interrupted `prerm` window + package-owned recovery (N01-class) | NOT_TESTED tonight; PASS 2026-11-06 | run 42244661660 |
| Feed outage / TLS failure (N07/N08) | NOT_TESTED | — |
| `dropbear.conf-opkg` conffile backup (N14) | NOT_TESTED | — |

The `lab/mt7621/busybox_matrix.py` controller is written arch-agnostically
except for the preset/NAND/lock parameters; extending the AArch64 stand with
the same matrix is a mechanical follow-up (see UNTESTED-AND-NEXT-STEPS.md).

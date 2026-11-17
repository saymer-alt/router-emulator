# Entware/OPKG/BusyBox lifecycle laboratory — two emulators (2026-11-17/18)

Night run for `keenetic-auto-setup-dev` issue **#226 (section F)**: a comparative
Entware/BusyBox lifecycle matrix on the two isolated QEMU stands of this repo.

- **Stand A — MT7621 / MIPSel** (`xiaomi-mi-router-3g-breed`, Ported KeeneticOS 5.1.8,
  ABI `mipsel-3.4`): controller `lab/mt7621/busybox_matrix.py` (this branch),
  pristine NAND assembled by `lab/mt7621/prepare_ported_mir3g.py`, fresh EXT4 `/opt`
  + pinned local mirror per run (25 packages, `lab/entware-mipselsf.lock.json`).
- **Stand B — MT7981 / AArch64** (`cudy-wr3000p-v1`, Ported 5.1.8 reporting model
  KN-3811, ABI `aarch64-3.10`): controller `lab/ported_e2e.py --stage entware`
  (existing proven stage), assets per `lab/fetch_lab_assets.sh`, NAND per
  `lab/prepare_ported_wr3000p.py`, fresh EXT4 `/opt` + pinned mirror
  (`lab/entware.lock.json`, 21 packages).

Both stands are virtual. Nothing outside the disposable guests was modified:
no physical routers, no feeds, no releases, no KAS changes. WAN stays offline
(`-w offline`, loopback-only forwards; the runs in this report were additionally
executed each inside a private Linux network namespace, so the two stands shared
one host without port collisions and without any external reachability).

## Evidence conventions

- Every observation stores the verbatim guest output first and is only then graded
  against an explicit expectation (`status`: PASS / FAIL / OBSERVED). An unexpected
  emergency outcome is never relabelled as a successful update.
- `PASS` = the expectation held; `FAIL` = it did not; `OBSERVED` = exploratory fact
  without a pre-registered expectation; `BLOCKED` = prerequisite unavailable;
  `NOT_TESTED` = not attempted in this window.
- Machine-readable evidence per MIPSel scenario: `evidence.json` + `junit.xml`
  (committed under `lab/mt7621/evidence/ported-mt7621-busybox-matrix-20261118/`).

## Fixed inputs (SHA-256 pinned, fail-closed)

| Artifact | Origin | Digest / version |
|---|---|---|
| Bootstrap busybox (both stands, `entware*.lock.json`) | bin.entware.net | busybox **1.38.0-5** (`0ba41200…` mipsel-3.4, `01270533…` aarch64-3.10) |
| Pre-upgrade baseline IPK (real package, not invented) | test.entware.net | busybox **1.38.0-3** mipsel-3.4 `29954f847e9442fc30cebf6bc7dd17a1fc1d391c2fd037c6df582a30c0e40645` |
| Pre-upgrade baseline IPK (AArch64, PR #56 lineage) | test.entware.net | busybox 1.38.0-3 aarch64-3.10 `4d69a41404abd5df0aaf55633331d43e28b90b4446ca5b53ae987a9f04b5a381` |
| KAS read-only preflight | private KAS `development@0bbe0cd` | `kas-entware-preflight.sh` sha256 `defb91a0fa819f06d6dcc6651704b183d45a6bf638f831167da2cb5c3132378e` (verified in-guest after transfer) |
| Ported firmware Xiaomi R3G 5.1.8 | osvault.project-athens.com | `05958b97…` (26 345 532 B) |
| Breed 1.2 r1416 bootloader | breed.hackpascal.net | `05ebb31f…` |
| Ported firmware Cudy WR3000P 5.1.8 | osvault.project-athens.com | `710e4a37…` (31 588 412 B) |
| KeenBOOT KB-41 FIP | osvault.project-athens.com | `8b8615b3…` |
| Patched QEMU v10.1.0 @ `f8b2f64e` + `qemu-patches/0028–0065` | built locally (WSL2) | `mipsel-softmmu` and `aarch64-softmmu` targets, per `lab/build_qemu*.sh` |

## Historical 1.37 baseline — BLOCKED by feed reality

The plan's preferred baseline busybox **1.37.0-6 is no longer published**: both
`https://bin.entware.net/…/busybox_1.37.0-6_….ipk` and the same paths on
`test.entware.net` answer **HTTP 404** (verified 2026-11-17/18, both
`mipselsf-k3.4` and `aarch64-k3.10`). 1.37.0-5 is also 404. Per the issue #226
rule ("если достоверный старый пакет недоступен — BLOCKED и причина") no fake or
mixed-ABI substitute was installed. The real-version transition instead uses the
oldest verifiably published package — **1.38.0-3 from the official test feed** —
and upgrades to the current **1.38.0-5**, reproducing the field-case mechanics
(installed → newer feed version with real maintainer scripts). The aarch64
1.37.0-6 interruption evidence remains the PR #56 CI run
[`42244661660`](https://github.com/saymer-alt/router-emulator/actions/runs/42244661660)
(2026-11-06, guest head `ca63bad`): *"native 1.37 prerm interruption + postinst
recovery PASS"* and *"native 1.38 upgrade left /opt/bin/sh and applets missing;
recovered in-guest with the pinned old postinst"*.

## Maintainer-script generations (determines the whole matrix)

Extracted host-side from the real IPKs (full bytes, SHA-pinned):

- **1.38.0-5 (current bin feed)**: monolithic `prerm` (`#!/opt/bin/busybox sh`);
  on upgrade (`PKG_UPGRADE=1`) it **hard-links the running binary to
  `/opt/bin/.busybox.opkg-keep` and re-points every busybox applet symlink there
  before the swap**; the monolithic `postinst` then deletes links pointing at the
  keep-file and recreates them for the new binary (this recreation is what emits
  the field-observed `Warning: '…' is already exists, skipping` lines for
  foreign `/opt/bin/wget`, `egrep`, `fgrep`, `xargs`, `find`, `grep`, `/opt/sbin/ip`).
- **1.38.0-3 (test feed) and the 1.37 generation**: `prerm`/`postinst` are
  OpenWrt-style `functions.sh` wrappers (`[ -s /opt/lib/functions.sh ] || exit 0`);
  stock Entware has no `/opt/lib/functions.sh`, so on this generation **both
  scripts no-op** — links are never deleted, and the in-place replacement of
  `/opt/bin/busybox` keeps the surviving symlinks valid.

Consequence: the *fresh bootstrap → downgrade → upgrade* path exercises both
generations; the *broken-sh window* is reproduced by executing an installed
remove-branch `prerm` outside opkg (same technique and explicit limitation as
PR #56: this is **not** a truly interrupted opkg transaction).

## Result documents

- `MIPSEL-RESULTS.md` — MT7621 matrix evidence.
- `AARCH64-RESULTS.md` — MT7981 matrix evidence.
- `MATRIX.md` — the combined scenario table.
- `FAILURES-AND-RECOVERY.md` — interruption windows, failure modes, recovery.
- `RECOMMENDATIONS.md` — operator guidance supported by this evidence.
- `UNTESTED-AND-NEXT-STEPS.md` — honest gaps.

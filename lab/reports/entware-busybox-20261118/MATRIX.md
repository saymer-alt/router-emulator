# MATRIX — combined scenario status (2026-11-17/18 window)

Status vocabulary: PASS / FAIL / OBSERVED / BLOCKED / NOT_TESTED.
"MIPSel run" = disposable QEMU guest this window; "AArch64 CI" = public run
[`42244661660`](https://github.com/saymer-alt/router-emulator/actions/runs/42244661660)
(2026-11-06) or the 41/41 persistence run archived in this window.

| ID | Scenario | MIPSel | AArch64 | Key evidence |
|---|---|---|---|---|
| P01 | Fresh Entware bootstrap (no upgrade) | PASS | PASS (CI 2026-11-06) | fresh/evidence.json; run log |
| P02 | `opkg update` only | PASS | PASS (CI) | fresh P02_OPKG_UPDATE |
| P03 | `opkg install busybox` at current version | PASS | NOT_TESTED tonight | fresh P03_INSTALL_CURRENT (no-op) |
| P04 | Older version installed first (real downgrade to published 1.38.0-3) | PASS | PASS (CI, local-IPK 1.38.0-3) | transition P04_* |
| P05 | `opkg upgrade busybox` (real version delta) | see MIPSEL-RESULTS | NOT_TESTED tonight | transition P07B/P05_* |
| P06 | `opkg update && opkg upgrade` (mass) | see MIPSEL-RESULTS | NOT_TESTED tonight | transition P07C |
| P07 | `opkg update; opkg install busybox; opkg upgrade` (field advice) | see MIPSEL-RESULTS | NOT_TESTED tonight | transition P07A/B/C |
| P08 | `opkg flag hold busybox` + updates | see MIPSEL-RESULTS | NOT_TESTED tonight | transition P08_* |
| P09 | Selective per-package upgrade | covered by P05/P12 (no second version delta available in a single-version pinned feed) | NOT_TESTED | transition |
| P10 | Reinstall busybox (`--force-reinstall`, package scripts) | PASS | NOT_TESTED tonight | fresh P10_* |
| P11 | Reboot after successful update | see MIPSEL-RESULTS | PASS (CI + tonight's 41/41 persistence) | fresh/transition P11_* |
| P12 | Idempotent second update | PASS | NOT_TESTED tonight | fresh/transition P12_* |
| N01 | Interruption after `prerm`, before `postinst` | see FAILURES-AND-RECOVERY | PASS (CI: real 1.37.0-6 prerm) | interrupt N01_* |
| N02 | Interruption during new busybox install | covered by N10-class kill test | NOT_TESTED | interrupt N10_* |
| N03 | Missing `/opt/bin/sh` | reproduced inside N01 window | PASS (CI) | interrupt N01_PRERM_WINDOW |
| N04 | Dangling applet links | reproduced inside N01 window | NOT_TESTED | interrupt N01_* |
| N05 | Missing `rm`/`sleep` | same window (applet probes) | NOT_TESTED | interrupt N01_* |
| N06 | busybox wget vs wget-ssl conflict | NOT_TESTED (lock lacks wget-ssl) | NOT_TESTED | UNTESTED doc |
| N07 | Feed/DNS down during update | see MIPSEL-RESULTS | NOT_TESTED | feedfail N07_* |
| N08 | HTTPS/TLS error during download | error-mode evidence only | NOT_TESTED | feedfail N08_* |
| N09 | Incompatible IPK/ABI | NOT_TESTED (no foreign-ABI package loaded) | NOT_TESTED | — |
| N10 | Half-completed opkg state after kill | see MIPSEL-RESULTS | NOT_TESTED | interrupt N10_* |
| N11 | Dropbear refuses new connections | NOT_TESTED as a standalone scenario | NOT_TESTED | — |
| N12 | QEMU reset during update | NOT_TESTED (timing not attempted) | NOT_TESTED | UNTESTED doc |
| N13 | libcurses/ncurses conflict | NOT_TESTED (packages not in pinned lock) | NOT_TESTED | UNTESTED doc |
| N14 | Modified conffile → `<name>-opkg` backup | see MIPSEL-RESULTS | NOT_TESTED | transition N14_* |
| N15 | Reinstall after failed attempt | see MIPSEL-RESULTS | NOT_TESTED | interrupt N15_* |
| R1 | Package-owned postinst recovery | see MIPSEL-RESULTS | PASS (CI) | interrupt N01_* |
| R2 | Rerun-completes recovery (opkg self-heal) | see MIPSEL-RESULTS | NOT_TESTED | interrupt N15_* |
| R3 | Snapshot rollback | enforced by runner (fresh image per scenario) | same | methodology |

The KAS gates (KAS_INSTALL, KAS_UPDATE_*, …) are BLOCKED on MT7621 by the
documented missing `ext-utils` component and were not attempted on AArch64 in
this window; that is unchanged by this matrix and tracked by issue #226.

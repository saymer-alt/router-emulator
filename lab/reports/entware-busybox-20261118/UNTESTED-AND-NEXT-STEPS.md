# UNTESTED and next steps (honest gaps of the 2026-11-17/18 window)

## Blocked by external reality

- **busybox 1.37.0-6 baseline (both ABIs)** — the historical IPKs are no longer
  published (bin/test.entware.net both return 404, see SUMMARY.md). The plan's
  "reproduce 1.37.0-6 then upgrade" branch is therefore BLOCKED, not skipped.
  If a verified 1.37.0-6 IPK ever surfaces (e.g. from a trusted mirror
  snapshot), the same matrix runs unchanged with a new pin.

## Not attempted in this window

- **Full positive/negative matrix on AArch64 in one controller.** Tonight the
  AArch64 stand contributed the 41/41 boot/AUTH/reset-persistence run plus the
  reused 2026-11-06 Entware/transition CI evidence; the remaining AArch64 rows
  (P03–P12, N07/N08/N10/N14, TLS, hold semantics) are NOT_TESTED. The
  `busybox_matrix.py` controller needs a WR3000P profile (preset, NAND
  assembly, `entware.lock.json`, `ported_e2e` integration) — mechanical work,
  no new science required.
- **True mid-transaction interruption inside opkg internals** (SIGKILL between
  `prerm` and `postinst` at a chosen instruction boundary). Tonight's
  `N10_KILL_MID_TRANSACTION` kills the whole opkg process at a wall-clock
  offset, and `N01_PRERM_WINDOW` executes the maintainer script outside opkg —
  both reproduce *states* of an interrupted transaction, neither is a traced
  interruption. A follow-up needs an opkg debug/gdb hook in the guest.
- **QEMU hard reset during an opkg transaction (N12)** — the monitor
  `system_reset` path exists in the lab, but timing it inside the short busybox
  transaction window was not attempted; needs a scenario with a slowed
  (nfs/root_delay) transaction or a larger package.
- **libcurses/libncurses dangling-link case (N13)** — the mipsel 25-package
  pinned bootstrap does not include the ncurses pair in the conflicted
  configuration reported from the field; reproducing it requires adding
  `libncurses` + a consumer package to a test lock.
- **Dropbear service-level recovery scenarios of issue #226 section A** (stale
  PIDFILE, port conflicts 22/222, alternative access) — separate task, out of
  this matrix's scope tonight except the conffile observation.
- **`wget-ssl` vs busybox `wget` conflict (N06)** — inventoried in the fresh
  baseline (`BASELINE_WGET_TLS`), the replacement-conflict upgrade case is
  NOT_TESTED (needs a lock with wget-ssl installed and a busybox applet
  transition, then the field `Warning: '/opt/bin/wget' is already exists`
  reproduction with a *foreign* file at that path).
- **KAS installer integration** — KAS install on MT7621 remains BLOCKED by the
  missing `ext-utils` component (existing documented gate), untouched here on
  purpose; KAS gates are therefore BLOCKED in every evidence file.
- **Physical routers, real feeds, releases, merges** — explicitly out of scope
  and not touched.

## Infrastructure next steps

1. Give `ported_e2e.py` an injectable pre-built mirror (or a `--lan-forwards`
   parameter) so the two stands can share a host without network namespaces;
   the namespace wrap was a night-window expedient and cost the AArch64
   Entware rerun (no DNS inside the namespace).
2. Commit the WR3000P profile of `busybox_matrix.py` and run the same matrix
   on `cudy-wr3000p-v1` for a true two-ABI table.
3. Add `libncurses`/`wget-ssl` variants of the pinned lock for N06/N13.

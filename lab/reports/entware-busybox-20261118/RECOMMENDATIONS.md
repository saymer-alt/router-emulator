# RECOMMENDATIONS — Entware/OPKG/BusyBox on Keenetic (evidence-based)

Every claim below states which stand and run it comes from. Nothing here is an
instruction to run anything on a physical router without a backup and a
recovery path; it is an engineering summary of what the two emulators showed.

## 1. Нужно ли вообще обновлять BusyBox на исправном Keenetic?

**Нет, без конкретной причины.** Evidence:

- The pinned feed serves busybox **1.38.0-5** as `Essential: yes` with
  `Provides: busybox-any`; a fresh bootstrap already installs the current
  version (MIPSEL fresh run, `P01_FRESH_BOOTSTRAP_STATE`).
- On a healthy system `opkg install busybox` and `opkg upgrade busybox` at the
  same version are no-ops (MIPSEL fresh run, `P03_INSTALL_CURRENT`,
  `P12_IDEMPOTENT_UPGRADE`).
- The only real-world upgrade path that produced field breakage is the
  **1.37.0-6 → 1.38.x** jump, whose maintainer scripts of that generation
  delete every busybox applet symlink (including `/opt/bin/sh`) before the new
  package is configured (PR #56 aarch64 CI evidence; `Bad interpreter
  "/opt/bin/sh"` behavior). The current 1.38.0-5 scripts contain the
  `.busybox.opkg-keep` hard-link mechanism that avoids that window entirely
  (verified from the real IPK bytes, and exercised live on MIPSel in the
  downgrade/upgrade cycle).
- An update buys nothing observable in the lab (same applet set, same `wget`
  capabilities) and risks the documented broken-shell window on older
  installed generations. If the Entware environment works, leave it alone.

## 2. Что безопаснее: не обновлять, обновлять выборочно или полный `opkg upgrade`?

Ranked by measured risk:

1. **Не обновлять** — zero observed risk on both stands.
2. **Выборочное обновление одного пакета** (`opkg update && opkg upgrade <pkg>`,
   busybox excluded unless a fixed version is specifically needed) — on MIPSel
   the per-package `upgrade` of a package without a version delta is a clean
   no-op (`P12_IDEMPOTENT_UPGRADE`), and the mass `opkg upgrade` after a real
   busybox transition completed cleanly (`P07C_OPKG_UPGRADE`).
3. **Полный `opkg update && opkg upgrade`** — completed without breakage on
   MIPSel (all pinned versions equal), but it touches **every** package's
   maintainer scripts, so a single bad package script interrupts the whole
   chain; this is the sequence that field reports link to Dropbear/SSH losses
   (issue #226 section A). The lab cannot exclude a broken *third-party*
   package script; treat full upgrades as the risky end of the scale.

The advice sequence from the field — `opkg update; opkg install busybox;
opkg upgrade` — contains a **redundant** step: with a version delta available,
`opkg install busybox` already performs the upgrade (observed on MIPSel:
`P07B_INSTALL_BUSYBOX` upgraded 1.38.0-3 → 1.38.0-5), and the following mass
`opkg upgrade` adds no busybox benefit. The sequence itself did **not** break
the guest — the *old-generation* maintainer scripts were the hazard, and those
are gone from the current feed.

## 3. Какие последствия у `opkg flag hold busybox`?

- `hold` is an **opkg database flag, not a protection mechanism**. Its effect
  is limited to excluding the package from mass `opkg upgrade` resolution.
  It does **not** protect against `opkg install busybox` when a local IPK or a
  resolved candidate exists, and it does not protect `prerm`-time breakage of
  *other* packages' scripts. (Status: verified on the package database level;
  the hold-vs-install interaction on MIPSel is recorded in the transition run
  evidence — treat the flag as downgrade-of-convenience, never as a guarantee,
  matching the plan's "не считать hold гарантией, пока не подтверждено".)
- After `hold`, `opkg upgrade busybox` (explicit target) still upgrades — the
  admin must not assume the flag blocks explicit operations.

## 4. Когда оправдана чистая переустановка Entware?

When the package database itself is inconsistent (half-configured states that
`opkg install` re-runs do not heal) or `/opt` contents are corrupt. Evidence:
- every transient breakage in this window (killed opkg transaction, remove-branch
  `prerm` window) was recoverable **in place** with package-owned scripts and a
  re-run transaction (`N15_RERUN_COMPLETES`), so reinstall is the *last* resort,
  not the first;
- reinstall from scratch destroys `/opt` user data — the lab always used a
  fresh image, which is exactly what makes it safe there and unsafe in the field.

## 5. Какие действия опасны при единственном удалённом SSH-доступе?

- **Anything that removes busybox applet symlinks** (`opkg remove busybox`,
  upgrading from an old installed generation, or manual `rm` of
  `/opt/bin/sh`): the guest's own `/bin/sh` depends on `/opt/bin/sh`; SSH
  *exec* cannot start and script files cannot be interpreted until a link is
  restored (`N01_PRERM_WINDOW`, and the aarch64 `Bad interpreter` finding of
  PR #56). With a single SSH path this is a lock-out.
- **Mass `ln -sf` loops over `busybox --list`** are forbidden as a fix: they
  overwrite foreign package files (the field `already exists, skipping`
  warnings show exactly which paths are foreign: `/opt/bin/wget`, `egrep`,
  `fgrep`, `xargs`, `find`, `grep`, `/opt/sbin/ip`).
- **Restarting/killing Dropbear over the same SSH session** (`N14` reinstall
  attempt) can drop the only channel mid-transaction.
- If a broken state is suspected, the safe first tool from an *existing*
  session is the **package-owned** recovery script executed through the
  always-present binary: `/opt/bin/busybox sh /opt/lib/opkg/info/busybox.postinst`
  (observed restoring `sh`/`chmod` in `N01` recovery).

## 6. Что подтверждено обеими архитектурами, а что только одной?

| Claim | MIPSel | AArch64 |
|---|---|---|
| Fresh pinned bootstrap + real opkg DB + EXT4 `/opt` | this window | 2026-11-06 CI (run 42244661660) |
| Old-generation `prerm` deletes `sh`/applets; preflight FAILs in that state | this window (remove-branch window) | PR #56 CI (1.37.0-6 real prerm) |
| Package-owned `postinst` recovery restores links | this window | PR #56 CI |
| Real version transition with current-generation scripts completes without breakage | this window | partial (1.38.0-3 local IPK only) |
| Feed-outage / TLS failure cleanliness | this window | NOT_TESTED |
| `dropbear.conf-opkg` conffile backup mechanism | this window | NOT_TESTED |
| Killed-transaction (`kill -9 opkg`) behavior | this window | NOT_TESTED |

Cross-architecture claims are limited to the shared package *scripts* logic
(identical script generations verified from both architectures' IPKs); runtime
behavior differences between ABIs were **not** observed in this window but the
sample size for AArch64 tonight is deliberately small.

## 7. KAS preflight as a detector

The read-only `kas-entware-preflight.sh` (KAS `development@0bbe0cd`,
sha `defb91a0…`) correctly reports the broken states it was designed for and
stays quiet on healthy systems (per-run `PREFLIGHT_*` entries in
`evidence.json`). Its exit code is the recommended operator tripwire before
and after any opkg transaction. It never repairs anything — by design.

# Issue #356 — isolated native BusyBox 1.37 → 1.38 laboratory

**Status: EXPERIMENTAL / Draft PR. Do not interpret CI green as actual interrupted-opkg or Keenetic field acceptance.**

## Exact inputs and environment

- Router Emulator QEMU of a *ported* KeeneticOS on an emulated disposable Cudy WR3000P; separate synthetic NAND and EXT4 `/opt`, with throwaway credentials.
- Entware bootstrap `lab/entware.lock.json` pins *real* BusyBox `1.37.0-6` aarch64-3.10, SHA-256 `881b06cecc1c8c6b4a0658d551c2230034ea0a21354fab89ddbce744f3d51e24`.
- New IPK `https://test.entware.net/aarch64-k3.10/busybox_1.38.0-3_aarch64-3.10.ipk` is downloaded into the **ephemeral CI host cache only** and must match immutable SHA-256 `4d69a41404abd5df0aaf55633331d43e28b90b4446ca5b53ae987a9f04b5a381`. Neither host nor guest feed configuration is changed.
- The new package's `postinst` must match SHA-256 `479b2ad3cc2256e1ab537b85d6435282d658d3efdc9813253acafffad6647541`. The installed old `prerm` must match SHA-256 `d9d1c4e7d81a5b46c39f22cded480c75e8b8d2849744a4cb84ca494b1dd407e4` before execution.
- Read-only KAS preflight comes from fixed KAS `2ae4a29121f6c7124895d3ece7cf496c74abad54`. Source not executed on the host, only in the throwaway guest via `/bin/sh`.

## Lab phases

1. Boot native QEMU Entware, validate aarch64, real opkg database, real EXT4 `/opt` and reboot persistence before altering any package.
2. Transfer pin-checked 1.38 IPK and the read-only KAS preflight through authenticated loopback SSH; verify hashes in guest.
3. In a **single persistent native `/bin/sh` session**, run the genuinely installed 1.37 `busybox.prerm`, confirm missing `/opt/bin/sh` and `/opt/bin/chmod`, and check the preflight emits a `FAIL`.
4. Run the genuinely installed 1.37 `busybox.postinst` and verify it restores those paths and preflight returns zero. The script has an emergency `EXIT` trap to try restoring links inside the disposable guest after failures.
5. Execute a **real local-file** `opkg install /opt/lab/busybox-1.38.ipk` in the same throwaway QEMU VM, inside **one persistent `/bin/sh` session together with every post-install observation**: the old 1.37 `prerm` deletes `/opt/bin/sh` and a new SSH exec cannot start until links are restored. The guest script reports which post-install state was actually observed (`NATIVE_UPGRADE_SH_MISSING_REPRODUCED` or `NATIVE_UPGRADE_SH_RESTORED_BY_POSTINST`), verifies opkg reports `1.38.0-3`, then restores the links with the **pinned old 1.37 postinst** copied aside before the install, and only afterwards does the host re-check the inventory and the GNU `grep` digest.

## Static-analysis correction (2026-11-05, real IPK bytes, no execution)

Two baseline assumptions of the previous head were wrong and are fixed here:

- **Foreign GNU grep lives at `/opt/libexec/grep-gnu`, not `/opt/bin/grep`.** Entware `grep 3.12-1` ships only `/opt/libexec/{grep,egrep,fgrep}-gnu` and exposes them via the opkg `Alternatives:` field (`300:/opt/bin/grep:/opt/libexec/grep-gnu`). `/opt/bin/grep` is therefore always a *symlink*, never a regular file; `test ! -L /opt/bin/grep` can never pass and was the exact `phase=foreign-grep-baseline` failure of run 41910981207. The digest now tracks `/opt/libexec/grep-gnu` (real ELF, package-owned).
- **The 1.38.0-3 `postinst` is an OpenWrt-style wrapper, and stock Entware has no `/opt/lib/functions.sh`.** Verified from the real test-feed IPK: `postinst` (SHA-256 `479b2ad3…`) is `#!/bin/sh` + `default_postinst $0 $@` guarded by `[ -s /opt/lib/functions.sh ] || exit 0`; the actual link-restore logic moved to `postinst-pkg` (byte-identical to the 1.37 postinst, SHA-256 `cb8c4963…`). None of the 21 pinned bootstrap packages or the official installer archive contains `functions.sh`, so on stock Entware the wrapper exits 0 **without restoring links**. Consequently the stage no longer asserts that `/opt/bin/sh` is executable after the real opkg install: the disappearance of `/opt/bin/sh` is the phenomenon under research and is observed, recorded, and then recovered with the pinned old postinst — never with a `ln -sf` loop.

## Guest `/bin/sh` finding (native run 41932162970, head `60d0c81`)

After the real old 1.37 `prerm` deletes `/opt/bin/sh`, the ported guest's own `/bin/sh` **stops being able to run script files at all**: invoking it on the preflight prints exactly `Bad interpreter "/opt/bin/sh"` and exits **0** (with rc=0 there is no FAIL detection). The parallel `/opt/bin/busybox sh` invocation of the same script in the same broken state returned the correct rc=1 with the expected FAIL lines. This is the lab-level confirmation of the field phrase `Bad interpreter "/opt/bin/sh"`: on this firmware the system shell itself depends on the Entware shell for script execution. The preflight inside this lab is therefore invoked via the always-present `/opt/bin/busybox` (a KAS-verified POSIX dialect); the *field* consequence — after a broken busybox upgrade even `/bin/sh`-based rescue steps may fail until `/opt/bin/sh` is restored — belongs to the issue #356 rescue-decision-tree notes, not to automatic repair.

## Evidence limits and stop conditions

- Stage `busybox-transition` is strictly **opt-in** and never exercised in ordinary KAS PR/native release gates. A separate research-only workflow invokes it on this Draft PR. The ordinary emulator workflow still executes the established KAS suite.
- Real IPK is verified and executed **only inside the disposable QEMU guest**. No host `/opt`, physical Keenetic, production feed, stable/release branch or user configuration is touched.
- The interrupted phase manually runs the real old maintainer script but **does not interrupt an opkg transaction at an exact internal phase**. The subsequent actual opkg upgrade is a separate *clean* local-IPK operation. Therefore these two steps cannot prove actual interrupted OPKG database recovery.
- Dangling links, clobbered wget-ssl/HTTPS, package-owned custom files and a genuine half-configured OPKG DB still require separate evidence; never call that complete or automatically repair a real router.
- `ENTWARE_BUSYBOX_TRANSITION` is a research-specific check. Unrelated KAS gates remain NOT_TESTED; a successful stage must not be presented as KAS release or hardware PASS.
- If SHA/ABI, package metadata, guest availability or controlled recovery checks differ, the lab fails closed; do not update expected tests to accept unsafe behavior.

## Follow-up

After successful review, define a separate QEMU experiment with a real opkg interruption/forced termination while preserving the package database, plus ownership-preserving manual recovery on a clean disposable snapshot. This requires additional proof and is not implied by this test.

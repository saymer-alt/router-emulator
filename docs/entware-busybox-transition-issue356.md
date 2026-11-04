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
5. Execute a **real local-file** `opkg install /opt/lab/busybox-1.38.ipk` in the same throwaway QEMU VM. Verify opkg reports `1.38.0-3` installed and native shell/applets are executable. Check that a separate GNU `grep` file's digest is unchanged.

## Evidence limits and stop conditions

- Stage `busybox-transition` is strictly **opt-in** and never exercised in ordinary KAS PR/native release gates. A separate research-only workflow invokes it on this Draft PR. The ordinary emulator workflow still executes the established KAS suite.
- Real IPK is verified and executed **only inside the disposable QEMU guest**. No host `/opt`, physical Keenetic, production feed, stable/release branch or user configuration is touched.
- The interrupted phase manually runs the real old maintainer script but **does not interrupt an opkg transaction at an exact internal phase**. The subsequent actual opkg upgrade is a separate *clean* local-IPK operation. Therefore these two steps cannot prove actual interrupted OPKG database recovery.
- Dangling links, clobbered wget-ssl/HTTPS, package-owned custom files and a genuine half-configured OPKG DB still require separate evidence; never call that complete or automatically repair a real router.
- `ENTWARE_BUSYBOX_TRANSITION` is a research-specific check. Unrelated KAS gates remain NOT_TESTED; a successful stage must not be presented as KAS release or hardware PASS.
- If SHA/ABI, package metadata, guest availability or controlled recovery checks differ, the lab fails closed; do not update expected tests to accept unsafe behavior.

## Follow-up

After successful review, define a separate QEMU experiment with a real opkg interruption/forced termination while preserving the package database, plus ownership-preserving manual recovery on a clean disposable snapshot. This requires additional proof and is not implied by this test.

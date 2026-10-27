# KNVR native virtual acceptance

This consumer is separate from KAS. It uses the same pinned Ported 5.1.8,
KB-41 and patched AArch64 QEMU bootstrap. The guest reports KN-3811, but the
virtual board is WR3000P/MT7981; no physical model acceptance is implied.

The `.github/workflows/knvr-e2e.yml` PR workflow pins the KNVR source commit.
`lab/knvr-packages.lock.json` adds official FFmpeg/ffprobe, Cron, curl, jq and
stat packages with their full native dependencies and SHA256. The guest's
actual opkg installs them. The host never constructs a fake package database.

A second disposable 256 MiB EXT4 USB image holds the archive, separate from
Entware's USB disk. Production KNVR storage guards use actual `/proc/mounts`,
`/proc/PID/fd` and `df`; no mount or capacity overrides are supplied.
Three local synthetic H264 RTSP feeds come from pinned MediaMTX v1.21.3 and host
FFmpeg. They traverse one explicit TCP guestfwd to a loopback-only RTSP server.
Both emulated networks stay restricted. No real camera or Telegram token is used.

## Run

Prepare assets/QEMU as described in [PORTED-E2E.md](PORTED-E2E.md), then:

```sh
python3 -m lab.ported_e2e \
  --stage knvr \
  --emulator-root /private/Router-Emulator \
  --nand-dir /private/prepared-nand \
  --private-dir /private/new-knvr-run \
  --report-dir /private/knvr-public \
  --knvr-dir /path/to/keenetic-nvr \
  --knvr-ref FULL_40_HEX_COMMIT \
  --mediamtx /path/to/pinned/mediamtx
```

The KNVR checkout HEAD must equal that full SHA. `git archive` sends committed
source to the guest. Host worktree changes cannot silently affect the run.

The KNVR consumer boots and installs real Entware, authenticates its SSH with
fresh credentials, verifies opkg/EXT4 and resets the guest before KNVR. It skips
the additional two NDM console-login boots owned by the independent platform
workflow. BOOT_READY comes from actual coldboot waits; AUTH_READY (NDM CLI) stays
NOT_TESTED in this consumer, never a fabricated PASS. All KNVR checks still run.

## Checks and evidence

- Real `fresh-install.sh install-fresh`, including its unmodified SSH TTY wizard.
- Three actual FFmpeg recorders with nonempty MKV; recovery after killing one.
- Open MKV from the recorder's FD table remains protected while FFmpeg is stopped
  temporarily with SIGSTOP and its mtime is deliberately aged.
- TIME preserves younger files. CIRCULAR evicts younger-than-72h closed files
  under real disk pressure. HYBRID expires a 5 MiB old file AND must evict a
  40 MiB younger file to recover the reserve. Candidates cross camera folders.
  The newest file survives; dry-run preserves hashes even under pressure.
- Actual EXT4 RO remount and unmount refuse cleanup, then RW recovery.
- QEMU reset restores recording through a native Cron tick, without manual start.
- Busy production controller lock rejects removal and preserves programs.
- Actual `uninstall.sh --yes`, repeated removal, reset after removal and
  reinstallation through the native installer with the preserved config.
- Configuration hash, archive filenames, all package versions and foreign cron
  entry survive removal. Final ffprobe must identify real H264 in a closed MKV.

`knvr.json` / `knvr.md` contain independent KNVR results. Untested stages remain
NOT_TESTED; an earlier failure cannot grant later PASS. The generic Ported
`gates.json` includes unattempted KAS gates and is only prerequisite evidence,
not KNVR's aggregate verdict. Private UART, disk images, temporary credentials
and raw guest logs are destroyed and never uploaded.

This is a bounded virtual test: it does not establish NAND endurance, physical
USB unplug behavior, real-camera compatibility, external Telegram delivery,
Mesh-reset recovery, v2.4→v2.5 upgrade or multi-day reliability. The full top-level
`setup.sh` HTTPS download wrapper is covered by ordinary CI contracts; the
emulator runs the actual local fresh installer, not a fabricated setup PASS.

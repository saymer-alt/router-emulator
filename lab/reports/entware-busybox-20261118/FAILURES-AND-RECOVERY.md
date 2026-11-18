# FAILURES-AND-RECOVERY — Entware/BusyBox (MIPSel stand, 2026-11-18)

Scenario ids reference the matrix in MATRIX.md; verbatim guest output for each
claim lives in `lab/mt7621/evidence/ported-mt7621-busybox-matrix-20261118/<kind>/evidence.json`.

*(Filled from the runs of this window — see the evidence tables in
MIPSEL-RESULTS.md.)*

## Failure modes exercised

1. **Remove-branch `prerm` window (N01-class)** — the installed busybox
   maintainer script executed outside opkg deletes the applet symlinks
   (`/opt/bin/sh`, `/opt/bin/chmod`, …). Consequences observed in one persistent
   session: native `/bin/sh` (a symlink into `/opt/bin/sh`) can no longer start
   script files or new SSH exec sessions; the KAS read-only preflight reports
   FAIL through `/opt/bin/busybox sh` while the system is broken.
2. **Killed opkg transaction (N10/N12-class)** — `kill -9` of opkg during a
   forced busybox reinstall leaves whatever partial state opkg had reached at
   the kill point.
3. **Feed unreachable (N07)** — `opkg.conf` source pointed to a closed port;
   `opkg update` and package downloads must fail cleanly without modifying the
   installed package set.
4. **TLS failure class (N08)** — the available HTTP clients' behavior against
   an HTTPS endpoint that is actually an HTTP lab mirror (documented error
   modes; the lab mirror has no TLS, which is itself the honesty boundary of
   the N08 evidence).
5. **Modified conffile during package touch (N14)** — operator-edited
   `dropbear.conf` + `opkg install --force-reinstall dropbear` reproduces the
   field-observed `dropbear.conf-opkg` backup mechanism.

## Recovery methods validated (MIPSel)

- **Package-owned recovery script**: `/opt/bin/busybox sh
  /opt/lib/opkg/info/busybox.postinst` (pinned aside before the experiment)
  restores the applet links from the package's own logic — no `ln -sf` loops.
- **Re-running the transaction** after a killed opkg completes the operation
  and restores a clean database state (N15).
- **Feed restore + `opkg update`** returns the system to normal operation.
- **Snapshot discipline**: every scenario started from a freshly built EXT4
  `/opt` image and a pristine NAND copy (the runner enforces both), which is
  the same effect as restoring the pre-experiment snapshot.

## Emergency-failure honesty rule

Where an expectation did not hold, the evidence records FAIL with the observed
output kept verbatim. No broken state is reported as a successful update; no
detection failure is reported as a PASS.

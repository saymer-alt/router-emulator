# WR3000P Ported laboratory

This branch starts at `e38cf0ce0c15ba0ccdf9976e5b16e268956e6d0a`.
It uses the Router Emulator 3.8 wrapper/presets, compatible KeenBOOT KB-41 and
the unchanged Ported 5.1.8 FIT, all checked by SHA256. E3 requires the minimal
FE IRQ fix in patch 0063 (renumbered 0059 → 0060 → 0063 through the
upstream 3.9/4.0 patch-number collisions), built on pinned QEMU v10.1.0 commit
`f8b2f64e2336a28bf0d50b6ef8a7d8c013e9bcf3` plus this repository's patch series.
Only AArch64 is built; CI caches it by the exact patch-series hash.

The virtual board is Cudy WR3000P/MT7981 with Ported. The firmware's own
`show version` reports release `5.01.C.8.0-2`, `arch: aarch64` and model
KN-3811; its authentication realm is `Keenetic KN-3811`. These software
identities do not establish equivalence to a physical KN-3811 or KN-1012.

## Authentication and privacy

`provision_ported.py` writes only a disposable synthetic Config_1. The native
32-byte `ndm3` zero entry points to uncompressed text at `0x6200` inside the
configuration partition at physical NAND `0x2700000`. Firmware, signature
verification and native authentication remain intact.

Every run generates `admin` with a 192-bit random password. The native
`password plain` command computes the correct hashes for the running image.
This temporary cleartext exists only in the private initial NAND and credential
file; both must be destroyed. NDM's configuration-save command writes its native
configuration before the persistence test. An arbitrary cli-tagged username
without admin leaves Ported in first-setup mode, so an unauthenticated prompt
is deliberately rejected by the scenario.

UART logs, firmware, NAND, USB, generated host keys, password hashes and private
credentials are never uploaded. Only `gates.json`, `junit.xml`, and `summary.md`
from the fixed-label evidence writer are public artifacts. Do not upload the
private directory to troubleshoot a failure.

## Local Linux / WSL preparation

Install `python3`, `python3-paramiko`, `mtd-utils`, `socat`, `curl`, `wget`,
`unzip`, `e2fsprogs`, and `openssl`. Use a short private native Linux path for
UNIX sockets. Source and output paths must differ.
For the FE fix also install the build dependencies listed by the workflow,
run `bash lab/build_qemu.sh /var/tmp/ported-qemu`, and replace only the private
wrapper's `qemu/qemu-system-aarch64` with the resulting executable.

```sh
bash lab/fetch_lab_assets.sh /var/tmp/ported-assets
python3 -m lab.prepare_ported_wr3000p \
  --stock-nand /var/tmp/ported-assets/downloads/Router-Emulator/nand-wr3000p \
  --ported /var/tmp/ported-assets/downloads/ported.bin \
  --keenboot /var/tmp/ported-assets/downloads/keenboot.bin \
  --out /var/tmp/ported-nand
python3 -m lab.ported_e2e \
  --emulator-root /var/tmp/ported-assets/downloads/Router-Emulator \
  --nand-dir /var/tmp/ported-nand \
  --private-dir /var/tmp/ported-private \
  --report-dir /var/tmp/ported-public \
  --kas-dir /path/to/keenetic-auto-setup \
  --kas-ref 6dd6438e2b64bad0fe9205ab6575795f808dabd8
```

`BOOT_READY` requires all eight ordered boot stages. `AUTH_READY` requires the
real Login/Password protocol, a version response, configuration save, reset and
another authenticated version response. E2's console is NDM CLI. The native
`/bin/sh` dispatcher has no standalone ash in this image; a root shell is
checked separately after genuine Entware bootstrap.

## Why Ported needs the IRQ fix

Published 3.8 selects its newer SDK interrupt path whenever FE_INT_GRP differs
from `0x21021000`. Keenetic 4.9 sets `0x21021030`, changing only nibble 1, which
the model does not route. This incorrectly suppresses the older FE receive
interrupt path. Private packet capture showed slirp ARP replies and TCP SYN
frames reaching the virtual LAN, while native `show ip arp` stayed empty.

A disposable MMIO experiment changing only FE_INT_GRP to `0x21021000` immediately
restored the native SSH banner. This diagnosis is not an E3 acceptance run.
Patch 0063 compares only nibbles for modeled interrupt sources. It preserves
the separate newer SDK layout `0x210ffff2`; the exact production C helper is
compiled and tested for reset, mainline, Keenetic and SDK values.

The source-built fix passed all 11 OpenWrt coldboot/HTTP/reset/repeated-HTTP
checks locally. Native Entware acceptance uses an ordinary fresh boot with
the source fix, without any debugger or runtime register modification.

## Entware preparation

`entware.lock.json` pins the official AArch64 installer and every dependency.
`build_entware.py` constructs a regular RAW image with an MBR partition starting
at 1 MiB and a real EXT4 filesystem. `/install/aarch64-installer.tar.gz` uses
the official bootstrap, with local feed URLs, regenerated per-run SSH identity,
and a per-run root password installed before the SSH service starts. Native
guest `opkg` performs all package installs and creates its own package database.

The host verifies SHA256 before preparing the mirror; opkg retains the package
checksums in real feed metadata. No integrity option is disabled. The existing
virtual xHCI/USB-storage mechanism attaches a copy of this disk. `opkg disk
KASOPT:/` selects it through NDM. WAN is `restrict=on`; one explicit slirp TCP
mapping sends `192.168.1.100:80` only to a mirror bound to `127.0.0.1`.
Inherited `WAN_EXTRA` and `LAN_EXTRA` cannot silently open other mappings.

The real guest probe checks `/bin/sh` UID 0, `uname -m`, mounted EXT4 `/opt`,
executed opkg version/architectures, all pinned installed packages and a writable
persistence marker. Reboot must preserve the marker, package versions and SSH
host key. Merely copying a binary or constructing this disk is not E3 PASS.

The bootstrap's lab SSH PID lives in native volatile `/var/run`, rather than
persistent `/opt/var/run`. The original S51 script tests only whether that PID
exists: after a reboot a stale PID can belong to an unrelated process and
incorrectly suppress SSH startup. This transport-only configuration change
does not change Mihomo or MagiTrickle service checks.

## Reports and limits

Nine independent gates use PASS, FAIL, BLOCKED or NOT_TESTED. JUnit encodes
BLOCKED/NOT_TESTED as skipped, never as successful execution. A successful boot
cannot advance authentication, Entware or KAS gates. E4 must test actual kernel
operations; a component name in `show version` alone is insufficient.

## E4 native capability evidence

Local runtime probes confirmed the required component set, native Proxy creation
and deletion, MARK/CONNMARK rules in a temporary mangle chain, persistent TUN
creation and a route in a separate table, mounted EXT4, working opkg mirror,
local HTTP and a real DNS query to the native DNS proxy. Temporary resources are
removed before KAS. The synthetic `lab.invalid` host is a local fixture.

The guest reports 497680 kB MemTotal and one active `/dev/zram0` swap of 524284 kB.
No external swap is enabled; dual-swap checks are not bypassed. Time is set by
the real native date command from host UTC. External NTP, WAN/VPN traffic and
physical hardware are not accepted by these probes.

Before KAS, the controller saves a private NAND/EXT4 snapshot after guest `sync`
and a synchronous QEMU stop acknowledgement, then resumes the VM. A later E4
controller error must preserve an already proven ENTWARE_READY result; that
regression is covered independently from the capability parser tests.

Current pinned KAS commit `6dd6438e2b64bad0fe9205ab6575795f808dabd8` deliberately
has no removal implementation: `uninstall.sh --remove/--purge` return code 3.
Snapshot/transaction checks never grant removal authority. The lab must report
that native contract as BLOCKED and must not substitute manual package removal
or the fixture-only transaction rehearsal for actual uninstall/rollback.
The real native transaction gate additionally rejects this Ported profile's
`ip global 32767`: its narrow accepted Proxy0 profile allows `ip global 350`.
The laboratory preserves that rejection rather than changing the router's
generated directive or widening the production uninstall allowlist.

## Real KAS lifecycle and isolated delivery

`kas_mirror.py` reads committed KAS blobs through `git archive`; it never executes
candidate scripts on the host. The requested full SHA must equal checkout HEAD.
`kas-assets.lock.json` pins the real Mihomo/MagiTrickle packages, repository helper,
feed indexes and a complete upstream release API snapshot. These dependencies
stay fixed while a KAS PR changes the actual scripts executed by native Entware.
The native installer performs its ordinary package selection and validation.

Only the four dependency domains are mapped to the isolated mirror. An ephemeral
lab CA signs a certificate for their actual names; its public certificate enters
the disposable guest trust store, while the private key stays on the host. Native
curl uses normal certificate and hostname verification. The mirror serves only
registered paths; it cannot proxy arbitrary Internet traffic. No public watchdog
or VPN health target receives a fabricated successful response.

KAS requires native HTTP/RCI at localhost:79 for live policy marks. The synthetic
configuration enables `service http` on its private bridge, and preflight queries
the real policy API. A lab init script restores actual host UTC through the local
HTTP service after reboot, because QEMU has no accepted external NTP source here.

The lifecycle runs the committed `install.sh disk`, verifies real package versions,
one Mihomo and one `magitrickled`, private ownership records, actual Proxy upstream,
`bypass_wa`, DNS interception, MARK/CONNMARK, installed source hashes and a real
SOCKS request through Mihomo to local HTTP. The MARK/CONNMARK assertion samples the
chain until it settles, within the same bounded budget the native installer's own
self-check uses, because Netfilter rewrite callbacks apply asynchronously after the
installer's Proxy down/up; the expected rule content never changes. It runs the
real watchdog twice with WAN isolated, then the real Doctor with the same pinned
source reference.

Repeat installation compares persistent files, journal, Proxy and policy. A real
TERM during another repeat install must release its real lifecycle lock, clean
staging, preserve the live contract and allow a subsequent successful install.
This handled-signal test does not establish power-loss or first-install atomicity.
A real
QEMU reset must restore the same contract without manually restarting services.
The updater first completes a genuine same-version `--force` replacement. A
second update receives TERM after its actual atomic commit; production rollback
must restore binary and metadata hashes, configuration and live service. This is
an update rollback test, not a claim of arbitrary interrupted-install recovery.

Native snapshot creation/verification and transaction gate/prepare run before
the uninstall guard. Fixture-only transaction rehearsal is excluded. A separate
copy of the pre-install snapshot contains a disabled foreign Proxy0 and unrelated
cron entry: installation, repeat, reboot and rejected removal must preserve them.
Its result appears as `KAS_FOREIGN_RESOURCES`, separate from the clean profile.
A failed profile publishes one fixed public label naming the actual phase:
transport unavailability (`native_command_timed_out`, `serial_timeout`), source
or setup failure (`verified_tls_source_failed`, `foreign_setup_failed`),
a typed contract violation (`foreign_proxy_changed`, `foreign_cron_changed`,
`native_socks_not_ready`, `native_command_failed`, and similar allowlisted
reasons), or `runner_failure` for a controller defect. Guest text, UART output
and configuration stay private in every label.

## GitHub execution and owner operation

The workflow runs on laboratory changes and `workflow_dispatch`, and exposes
`workflow_call` with full `emulator_ref` and `kas_ref` SHA inputs. The KAS caller
pins the reusable workflow itself to an immutable emulator commit and passes the
PR head SHA. Review the three public report files in the `ported-gates` artifact
and the Actions job summary. Source and binary hashes are included in JSON.

A green job means all attempted assertions, including an expected safe refusal,
held. It does not override the JSON/JUnit outcome: unimplemented native removal
remains `KAS_UNINSTALL=BLOCKED`, and the aggregate remains BLOCKED. Unexpected
command failures or state changes fail the job. Doctor WARN is BLOCKED, never PASS.

Merge order, when the owner authorizes it: boot-fix PR, laboratory PR, then KAS
caller PR. Until merged, run the workflow on its feature branch. Once the caller
is on the default branch, new KAS PRs trigger automatically while the owner's PC
is off. Manual dispatch can test another full KAS SHA. Run it once on the default
branch to seed its QEMU cache; subsequent PRs reuse the exact patch-series key.
GitHub caches are repository/ref scoped, so the first KAS caller can require one
AArch64 build even when Router Emulator already has a cache. No Windows/MIPS/PGO
build or schedule is added. Normal GitHub-hosted runner access and Actions enabled
in both public repositories suffice; no router credentials or PAT are required.

## Acceptance still requiring separate evidence

This is WR3000P Ported QEMU acceptance. Hardware reboot/power loss, Wi-Fi/radio,
real USB wear/disconnect/fsck, switch offload, physical model compatibility,
external NTP/DNS, Internet/VPN transport and real upstream failover remain
NOT_TESTED. Native TUN creation is distinct from a working external VPN tunnel.
External swap is NOT_TESTED; the observed native zRAM remains active.

Useful next consumers are KeenWhite (native DNS/firewall ownership and persistence),
WDTT (opt-in service lifecycle, loopback authentication, architecture and restart
guards), and KNVR (storage/retention and daemon/resource recovery). Add their real
packages and pinned local services as separate gates; this KAS result does not
accept those projects automatically.

No schedule is enabled. No changes to real routers, networks, VPS, main/stable,
tags, releases or automatic merges are part of this laboratory.

## Foreign-profile Doctor warning: local reproduction and evidence limits

The safe-remove acceptance uses a controller-executed manual prerequisite,
without changing production KAS process attribution. Before the initial install,
`lab_origin()` requires no daemon/package/canonical executable in the disposable
VM and records expected executable/init SHA256 pairs from locked IPK payloads.
All subsequent launchers are the pinned installer, its init/watchdog and the
controller's reset. This closed-world provenance is essential: matching PID,
inode or argv on an arbitrary router never authorizes this laboratory helper.

With services running, the native `--remove` must return the ownership BLOCKED
message, preserve the contract and PID/starttime identities, and create no
journal/maintenance marker. Before recording identities, the controller waits
read-only for uptime > 300 and absence of the KN init shell (bounded to 350s).
The locked KN `S99magitrickle` starts its own background `startup_loop` below
that uptime, checking every 5s and restarting absent MagiTrickle independently
of KAS watchdog locks. Waiting for its natural exit avoids stopping a daemon
while that boot launcher is still active; no init shell is signalled or changed.
An unknown or persistent init process blocks at the deadline. The controller then revalidates both processes,
takes the existing KAS lifecycle lock, runs the real watchdog to prove its
maintenance skip, and addresses only those PID/starttime identities. TERM and
bounded KILL recheck inode, payload/init hashes and argv; uncertainty blocks.
After the real watchdog skip, the controller releases lifecycle while processes
still run, takes the existing watchdog per-run lock, and reacquires lifecycle
without waiting. Both identities are rechecked before the first signal. After
absence is proved, only lifecycle is released: the SAME guest shell retains
watchdog per-run ownership across snapshot/gate/cache and the child native
`--remove`. The child takes its own lifecycle lock; EXIT cleanup releases the
parent's remaining lock on success and abort. No lock ownership is transferred,
no stale lock is stolen, and a busy acquisition blocks instead of waiting in a
cycle. A concurrent non-watchdog process is still caught by native KAS guards.
Completed removal also requires both lock directories and the maintenance
marker to be absent, alongside the completed journal and preserved rollback cache.

On unexpected refusal, the parent captures process identity/parent information
before releasing its watchdog lock. Public output contains only allowlisted
daemon, identity-change, payload-match and observed-parent categories. Parent
argv matches are observations, not proof of the original launcher; reparented
processes remain unattributed. No discovered process is signalled by diagnostics.
PID/starttime/executable/parent data and cmdline hashes are saved mode 0600 in
guest `/opt/lab/uninstall-blocked.private` and host `uninstall-processes.private`.
Raw argv is never collected. These files follow the existing private-dir cleanup
policy and are never uploaded plaintext to public artifacts; public categories
are retained in the Actions log. A complete-scenario retry is never automatic.

After reinstall, the real `wan_guard()` probes run before Doctor, generating only
genuine offline-WAN history. No healthy heartbeat is fabricated. A remaining
Doctor WARN is still BLOCKED with foreign counters retained. `KAS_MANUAL_STOP`
records the separate laboratory prerequisite. Manual dispatch accepts an explicit
full `kas_ref`; both source commits are checked and retained in `gates.json`.

At KAS `891c336cf389c34b5c2b504a2bc67979ba8d287a`, `install.sh` touches
`/opt/var/log/mihomo_watchdog.log`. After fresh tmpfs or before the first
scheduled watchdog run it can be empty. The clean lifecycle invokes
`wan_guard()` before Doctor; the foreign lifecycle does not. The production
Doctor history section reports `Watchdog log is empty`, WARN=1, FAIL=0 and
exit 1 for that state. A missing log similarly warns. This is a reproducible
cause, not proof of the warning in historical run 40184425498: that run's
private SSH log was deleted and its public artifact omitted foreign counters.

Local reproduction from the KAS checkout, without guests or network:

```sh
python3 tests/maintenance-regression.py
```

`doctor_fresh_history()` executes the actual Doctor history section and summary
on missing, empty and recent WAN-outage-only logs under dash and BusyBox ash.
It preserves WARN for missing/empty history. A WAN-outage-only log exercises
the existing informational path; the test does not fabricate a healthy run.
`lab.test_lifecycle` separately verifies that post-reinstall WARN returns false,
stays BLOCKED and never prints `Doctor passed`. `KAS_FOREIGN_DOCTOR` and
`foreign_doctor_*` counters retain the foreign result in public gate evidence.
No cron timing change, fabricated watchdog log or relaxed Doctor criterion is
an acceptable way to turn this warning into PASS.

References: [native password command](https://storage.googleapis.com/docs.help.keenetic.com/cli/4.3/en/cli_manual_kn-3610.pdf),
[official Entware USB installation](https://support.keenetic.com/titan/kn-1812/en/20980-installing-the-entware-repository-on-a-usb-drive.html).

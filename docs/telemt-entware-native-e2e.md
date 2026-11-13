# TeleMT / TeleMT Panel Lite native Entware IPK — QEMU laboratory

## Scope and rationale

This laboratory **does not use physical Keenetic/Netcraze devices**. It uses
the existing disposable Ported WR3000P/MT7981 ARM64 QEMU guest with an actual
EXT4-mounted `/opt`, genuine `opkg`, and per-run synthetic credentials.
There is no need to connect actual Telegram clients. The objective is
to prove package integrity, on-device installation, package DB consistency,
and preservation of local settings when installing, upgrading and removing IPK.

Reuses `lab/ported_e2e.py` (`--stage telemt-ipk`) and the already pinned
`lab/entware.lock.json`; no new QEMU toolchain or persistent runner.
The tested installer source is **exactly** entware-go PR #33 commit
`0fd4c129a5d1fcb515fb8aa94b8d81da938641b1`.
No production build/release/tag or GitHub token is changed.

## Immutable published inputs

| Family | Release asset | SHA-256 |
| --- | --- | --- |
| TeleMT | `telemt_3.5.17-1_aarch64-3.10.ipk` | `ccab455710761f236f5da6f2f2a852de575e5db164f59b8157c30e270110e509` |
| Panel Lite | `telemt-panel-lite_1.0.0.rc.3-1_aarch64-3.10.ipk` | `e649a14fe0bfb670d620116fa4d503a96285a085f89fa328e4612ccedbb4fdaa` |

GitHub Actions downloads only these two assets from
`saymer-alt/entware-go/releases/download/latest/` and verifies the exact
immutable digests; missing/replaced assets **fail closed**. The release asset
name for Panel Lite uses `.rc.3`, while its `control` version uses `~rc.3`.
The host parses the actual compressed IPK format (`tar.gz` outer container,
`control.tar.gz` nested) before any guest mutation.

The **synthetic successor** for update mechanics contains the exact same
binary/data archive and changes only `control` Version:
`3.5.17-1 → 3.5.18-1` and
`1.0.0~rc.3-1 → 1.0.0~rc.4-1`.
It is **only a laboratory fixture**, not a claimed upstream release, and is
never uploaded or installed on real devices.

## Actual guest tests

All guest operations are performed via authenticated loopback SSH and an
isolated shared WAN mirror. Only the GitHub JSON and download HTTP client
are substituted by strictly allowlisted, locally served fixture files;
**`opkg` is real** and changes its actual persistent package database.

| Check | Acceptance |
| --- | --- |
| `TELEMT_IPK_INSTALL` | Dry-run changes nothing; genuine `opkg install` via the *unmodified* PR #33 clean installer installs both published packages and their byte-identical executables |
| `TELEMT_IPK_REFUSALS` | Running the clean installer on existing packages fails without mutation; wrong GitHub SHA-256 also fails before a package change |
| `TELEMT_IPK_STAGE` | Old IPKs are cached with SHA-256, future fixture versions are safely staged using the PR #33 upgrade script; package database stays unchanged |
| `TELEMT_IPK_UPGRADE` | Genuine `opkg install` upgrades to the synthetic next versions; installed bytes remain identical |
| `TELEMT_IPK_RESTORE` | Manual package-aware restoration (`opkg remove` + `opkg install` original verified IPK) restores version and executable |
| `TELEMT_IPK_REMOVE_REINSTALL` | Genuine `opkg remove` clears managed executables/records without deleting pre-existing synthetic `config.toml` and Panel state; clean reinstall succeeds |

Checks always compare the SHA-256 of private synthetic configurations and
Panel `panel-state.json` to their pre-install baseline. No secrets or
production addresses enter tests or public logs. Public JSON/JUnit evidence
includes only allowlisted status and reason labels; raw UART, SSH, flash
images, fixture packages and generated credentials remain private to the
ephemeral worker and are destroyed afterward.

## Not yet covered

- **Automatic rollback** after failed service health (currently manual,
  package-consistent rollback only);
- actual newer upstream binary and schema migration (synthetic version-only
  fixture does not emulate application behavior changes);
- TeleMT's real networking, MTProto clients or real TLS/SNI;
- an operable new Panel IPK update button or source fork; official Panel Lite
  has an upstream tar.gz updater, NOT our IPK-aware code;
- physical NC-4210/KN-3811, USB power failure, flash wear, Wi-Fi, ISP/WAN
  blocking, or Telegram connectivity.

These must remain `NOT_TESTED` or `BLOCKED`, **never PASS** based solely on
native package transactions.

## How to run

Go to Actions: [TeleMT IPK native QEMU acceptance](../.github/workflows/telemt-ipk-native.yml)
and run the targeted workflow (or open a PR containing the stage changes).
It calls the existing `ported-entware-kas.yml` reusable QEMU lab with
`stage=telemt-ipk` and a fixed complete SHA for the entware-go test scripts.
The ordinary KAS and KNVR regression suites and their results are unaffected.

No unattended overnight retries are configured. Repeat only when a new
source SHA, failure hypothesis or artifact changes; this avoids spending CI
budget on duplicate heavy QEMU runs.

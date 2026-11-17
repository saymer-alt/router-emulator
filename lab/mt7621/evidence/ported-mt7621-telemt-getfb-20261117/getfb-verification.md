# TeleMT MIPSel getrandom-fallback verification — REAL MT7621 guest cycle: PASS (74/74)

Date: 2026-11-17. Branch `lab/mt7621-telemt-entropy-fb-20261116` (Draft PR #95,
separate branch/pins per entware-go issue #49 task 5). Core under test: the NEW
separately named experimental `telemt_3.5.17-2fb1_mipsel-3.4.ipk` (entware-go
Draft PR #50, run 43948987955; IPK SHA256 `2c8a84fb…c85d8d`, ELF SHA256
`d4ba4381…b35b216f`, MIPS32r2 softfloat, `/opt/lib/ld.so.1`, max GLIBC 2.25).
Panel Lite stays the frozen PR #44 artifact (`0236dfb0…`, ELF `380cb511…`).

## Result

`ported-mt7621-telemt.json` (74 steps): **PASS, 74/74 rows, 825.84 s** —
sanitized `report-getfb.json` + `junit-getfb.xml` in this folder. For the first
time across PR #44/#93/#95 the TeleMT Core daemon **runs, serves its loopback
API, stops, restarts, survives a native reboot and autostarts via rc.unslung**
in the real QEMU-system MT7621 guest with the pinned Ported 5.1.8 image:

| Stage | Status | Evidence |
|---|---|---|
| Clean Entware baseline 33/33 | PASS | verbatim scenario steps 0-32 |
| Fixture seed (config/secret/init/wrapper) | PASS | step 33 |
| Loopback delivery + dual IPK SHA in-guest | PASS | step 34 (one `printf` manifest, both pins) |
| Native `opkg install` of BOTH IPKs + `opkg status` versions | PASS | steps 35-36 |
| Dual in-guest ELF SHA (both binaries) | PASS | step 37 |
| Core CLI negative contracts (EXIT 2 / EXIT 1, no panic) | PASS | steps 38-40 |
| **In-guest kernel-fact probe (corrected platform model)** | PASS | step 41: `PROBE_GLIBC_SYMBOL_RET_-1_ERRNO_89`, `PROBE_LIBC_GETRANDOM_RET_32_ERRNO_0`, `PROBE_RAW_SYSCALL_RET_32_ERRNO_0`, urandom OK + distinct bytes, `/dev/random` POLLIN ready; emitted to stdout AND UART first |
| Foreground diagnostic (fixed binary) | PASS | step 42: `start --foreground` alive for the bounded 20 s window (`FG_ALIVE_20S`), captured on stdout + UART |
| Daemon start / log / proc identity | PASS | steps 43-45 (`DAEMON_PROC_ALIVE` via pidfile + `/proc/$pid/cmdline`) |
| Loopback API health | PASS | step 46 `HEALTH_LIVE` (healthcheck over 127.0.0.1:9091); UART: `API endpoint ready … listen=Some(127.0.0.1:9091)`, `Listening on TCP endpoint addr=127.0.0.1:3443 transport=Mtproxy` |
| PID identity via `/proc/$pid/exe` | PASS | step 47 (`/opt/bin/telemt`) |
| Listening sockets in `/proc/net/tcp` | PASS | step 48 (`127.0.0.1:9091` + `127.0.0.1:3443`, state 0A) |
| Stability after 10 s | PASS | step 49 |
| Stop (SIGTERM) / health down | PASS | steps 50-51 |
| Restart via lab init script (gen=1) + health | PASS | steps 52-53 |
| Pre-reboot off | PASS | step 54 |
| Native reboot → boot-3 chain | PASS | steps 55-63 (`reboot: Restarting system` → `system ready`) |
| **rc.unslung autostart of the same config** | PASS | step 64 UART marker `telemtlab-start gen=2 pid=704`, then health/pid/single-instance/persisted-version/gen/pids/sync proofs (steps 65-73) |

Telegram upstream remains **NOT_TESTED** (no credentials, no Internet-facing
proxy). Panel HTTP stays NOT_TESTED (Panel Lite `--version`/install only, as in
PR #93).

## Corrected root cause (supersedes the round-2 kernel-level claim)

The round-2 diagnosis (PR #93) attributed the `rand 0.10.2` ThreadRng ENOSYS
panic to the guest kernel "not exposing getrandom(2)". The in-guest probe in
this cycle disproves that: **the Ported `4.9-ndm-5` kernel DOES implement
getrandom(2)** — o32 syscall 4353 returns 32 bytes both via the generic libc
`syscall()` wrapper and via raw `asm("syscall")`. The real blocker sits in
**Entware's glibc 2.27 `getrandom()` symbol**: built against kernel-3.4-era
headers, its `getrandom()` is an ENOSYS stub that fails before issuing any
syscall (`PROBE_GLIBC_SYMBOL_RET_-1_ERRNO_89` measured in-guest). That symbol
is exactly what `rand 0.10.2` → getrandom 0.4.3 → `libc::getrandom` reaches,
hence `could not initialize ThreadRng: Function not implemented (os error 89)`.

The shipped fix is therefore exactly right: the forked getrandom backends
dlsym-probe `getrandom`, land on the glibc stub's ENOSYS, and take the
reviewed upstream `/dev/urandom` fallback (POLLIN readiness barrier verified
ready in-guest; `/dev/urandom` reads fine with distinct consecutive bytes).

## Second upstream finding (documented, NOT patched)

`telemt stop` never delivers SIGTERM on this guest: `daemon/pid_file.rs`
`signal_pid_file()` calls `open_pidfd()` → `SYS_pidfd_open`, a syscall added
in **kernel 5.3**; on 4.9-ndm-5 it returns ENOSYS and `stop` exits nonzero
before signalling. The lab stop stage and the `S99telemtlab` fixture deliver
the identical SIGTERM via plain `kill(2)` and verify the graceful tokio
shutdown (which works: the foreground diagnostic's SIGTERM proved it, and the
stop/restart stages pass). `telemt reload` shares the same dependency. This
belongs to the upstream portability backlog together with the glibc-2.27
`getrandom()` stub issue; neither is patched in the entropy forks.

## Guest-cycle budget accounting (transparent)

Five QEMU boots this round, zero product failures:
1. launch aborted before the guest (runner env `QEMU_BIN` missing — not a
   guest run);
2. cycle A — failed at the probe step: fixture bug (evidence sat behind the
   assertion chain) → evidence-first rework;
3. cycle B — probe evidence corrected the platform model; fixture bug: the
   probe assertions encoded the disproved kernel-level hypothesis → assertions
   aligned to measured facts + glibc-symbol probe added;
4. cycle C — fixture bug: literal NUL bytes inside `tr` arguments in steps
   48/68 (unreachable in rounds 1-2; ash exit 2 "unterminated quoted string")
   → textual `\0` escapes + no-NUL regression test;
5. cycle D (final) — fixture bug: wrong listener port hex `0D6B` (3435) vs the
   lab config's actual `0D73` (3443), plus the pidfd-driven stop rework →
   **PASS 74/74**.

Each repair was verified against the previous run's own evidence before the
next boot; the product (fixed binary) passed every stage it reached.

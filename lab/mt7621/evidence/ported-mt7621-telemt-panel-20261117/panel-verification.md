# TeleMT Panel Lite Stage G verification — REAL MT7621 guest cycle: PASS (93/93)

Date: 2026-11-17. Branch `lab/mt7621-telemt-panel-g-20261117` (Draft PR #96,
router-emulator issue #94), stacked on the verified getfb Core acceptance
(Draft PR #95, 74/74 PASS). Artifacts: the SAME verified pins as the getfb
acceptance cycle — Core `telemt_3.5.17-2fb1_mipsel-3.4.ipk` (SHA256
`2c8a84fb…c85d8d`, ELF `d4ba4381…b35b216f`, entware-go Draft PR #50) and
Panel Lite `telemt-panel-lite_1.0.0.rc.4-1_mipsel-3.4.ipk` (SHA256
`0236dfb0…71e68`, ELF `380cb511…29a40c8`, frozen PR #44 artifact, reused
unchanged), plus the pinned in-guest kernel-fact probe (`81656b1e…`).

## Result

`ported-mt7621-telemt-panel.json` (93 steps = the 74 verified getfb steps
byte-identical + 19 Panel stages): **PASS, 93/93 rows, 900.83 s** — sanitized
`report-panel.json` + `junit-panel.xml` in this folder.

Panel stages that ran in the real guest (all evidence-first: the raw output
reaches stdout AND the UART console before any assertion):

| Stage | Status | Evidence (UART/console) |
|---|---|---|
| `telemt-panel version` in-guest | PASS | `telemt-panel 1.0.0-rc.4 (lite: memory)` |
| Config seed with in-guest `hash-password` | PASS | loopback-only `listen = "127.0.0.1:8080"`, Core API url `127.0.0.1:9091`, memory store, bcrypt `$2a$10$…` written to `/opt/etc/telemt-panel/config.toml` |
| Negative malformed-config contract | PASS | `ERROR load config err="unknown configuration fields"` — exit ≠ 0 before any serving, no panic |
| Wrapper-baked daemon start (`S99telemtlab-panel`) | PASS | UART `telemtlab-panel-start gen=1 pid=1066` |
| PID identity | PASS | `readlink /proc/$pid/exe` = `/opt/bin/telemt-panel` |
| `GET /api/health` (unauthenticated) | PASS | `{"drivers":["memory"],"state_durable":true,"status":"ok","variant":"lite","version":"1.0.0-rc.4"}` |
| Loopback socket | PASS | `/proc/net/tcp` `0100007F:1F90 … 0A` (127.0.0.1:8080 LISTEN) |
| Synthetic login + session | PASS | `me={"auth_disabled":false,"username":"labadmin","passkeys":[]}` via `panel_session` cookie |
| Wrong-password negative | PASS | HTTP 401 `invalid_credentials`, exit ≠ 0 |
| **Core–Panel integration (real protocol)** | PASS | `GET /api/telemt/info` → panel → Core `GET /v1/system/info`: `{"reachable":true,"version":"3.5.17","arch":"mips","os":"linux","uptime_seconds":39,"config_path":"/opt/etc/telemt-lab.toml",…}` |
| 10 s stability | PASS | health + proc alive after `sleep 10` |
| SIGTERM stop + no-orphan | PASS | health down, exe scan `panel workers = 0` |
| Pre-reboot off, native reboot | PASS | `reboot: Restarting system` → `system ready` |
| **rc.unslung autostart of BOTH services** | PASS | UART `telemtlab-start gen=2 pid=701` then `telemtlab-panel-start gen=2 pid=725` (S99telemtlab sorts before S99telemtlab-panel) |
| Post-reboot panel health/pid/single/gen=2/persisted | PASS | health JSON identical, exe scan `panel=1 core=1`, genfile `2`, config + `panel-state.json` persisted on /opt |
| **Post-reboot fresh-login integration** | PASS | new `panel_session` → `{"reachable":true,…,"uptime_seconds":13}` (fresh Core), Core healthcheck live in the same step |

## Panel HTTP PASS vs Core–Panel integration PASS (explicitly distinguished)

- **Panel HTTP PASS** = the panel binary serves its real HTTP API in the
  Entware guest: grounded `/api/health` body, `/proc/net/tcp` loopback
  listener, authenticated session flow, 401 negative, single live worker.
- **Core–Panel integration PASS** = the panel reached the NEW fixed Core
  through its actual protocol (`GET /api/telemt/info` → `GET
  {telemt.url}/v1/system/info`, telemt_handler.go:61 → read_routes.rs:67)
  and reported `"reachable":true` with the Core's own identity — proven both
  on the daemonized run (uptime 39 s) and after the native reboot via a
  fresh login (uptime 13 s). Capability flags are all `false`: the lab Core
  config exposes no editing/reload surface — consistent with the synthetic
  read-only lab configuration.

## Guest-cycle budget accounting (transparent)

Two QEMU boots this round, zero product failures:
1. cycle A — failed at `PANEL_BADCFG_REJECTED` (step 58): **fixture defect**,
   the step grepped `unknown config keys` (a different validator's wording,
   config.go:206) while the actual startup rejection of `loadStartupSource`
   is `unknown configuration fields` (internal/config/source.go:178). The
   guest evidence proves the panel itself behaved correctly: the malformed
   config was rejected with exit ≠ 0 before any serving, no panic. Needle
   aligned to the measured string from this cycle's own UART evidence
   (commit `fa98607`).
2. cycle B (final) — **PASS 93/93**.

Issue #94 budget respected: one diagnostic cycle + one extra cycle for a
concrete tested correction. The baseline (steps 0-32) and every Core
acceptance stage stayed byte-identical to the verified getfb scenario.

## Bounds and NOT_TESTED

Telegram upstream (no credentials, no Internet-facing proxy), Panel subpage
listener, Panel service-manager/status integrations (`service_manager =
"none"` in the synthetic lab config), Host-privilege flows, and opkg
clean-removal rollback (optional per issue #94; lifecycle acceptance did not
require it). Nothing merged, nothing released; cloud guest stage stays
intentionally SKIPPED without the `TELEMT_IPK_TOKEN` secret.

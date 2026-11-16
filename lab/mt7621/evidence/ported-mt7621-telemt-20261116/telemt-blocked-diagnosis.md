TeleMT MIPSel pinned-IPK guest lab (Issue #91 / Draft PR #93) - BLOCKED diagnosis
==================================================================================

Result line (the required field list)
-------------------------------------
- main SHA: 8feaa9cf3930c5c7dfc71e12e6bf24c801c39d57
- dependent PR SHA: 82adea2 (Draft PR #93 head; all CI checks green there,
  only the two intentional skips: the cloud guest stage awaiting
  TELEMT_IPK_TOKEN and the standard release skip)
- exact IPK SHA+ABI:
    Core  telemt_3.5.17-1_mipsel-3.4.ipk        IPK SHA-256 b8dfbb83380d8f4353151b0de43d8c34ceb2369aea45cb6ddaf3b3089a798e5d
                                                ELF SHA-256 3efa4a53c1d50bf1b7a0ed6ca7de98201faf0fa2e27eaaf5f1cd589a6bf26063
                                                ELF32 MIPS LE, dynamic, PT_MIPS_ABIFLAGS.fp_abi=3 (softfloat),
                                                interpreter /opt/lib/ld.so.1
    Panel telemt-panel-lite_1.0.0.rc.4-1_mipsel-3.4.ipk
                                                IPK SHA-256 0236dfb076b14b129a6392de49f287838a11aebe7681e092084d4ce87e771e68
                                                ELF SHA-256 380cb5112b58538eafec9ae9fab27fb22c994fd36ce3130c541c5e11b29a40c8
                                                ELF32 MIPS LE, static ET_EXEC pure-Go, fp_abi=3
    Both are frozen CI artifacts of entware-go Draft PR #44, Actions run
    43742529964 (artifacts verified present on 2026-11-16, never rebuilt).
- host gates: PASS (STATIC_IPK_CONTRACT_PASS: fail-closed host auditor over
  both artifacts, agreeing with entware-go's PR #44 preflight; synthetic
  ELF/IPK negative tests; QEMU-user CLI contract probe; config-acceptance
  probe "Mask: false" after the fixture change)
- QEMU MT7621 Entware: PASS - the committed 33-step tz-skip baseline is
  embedded verbatim as steps 0-32 of the 72-step scenario and passed inside
  both real guest cycles (clean install, native reboot, boot-2 persistence
  proofs, 4 VPE)
- opkg: PASS - both artifact hashes and both ELF payloads passed
  host-side SHA256 checks; the actual guest installed both IPKs using
  native opkg and confirmed their package versions. **Evidence correction:**
  the original two-printf shell commands redirected only the second (Panel)
  printf into the .sha manifest, so the guest sha256sum -c checked only
  Panel for the IPK and for the installed ELF. The Core hash was printed
  but NOT checked in the guest. The guest proof for BOTH hashes is therefore
  NOT_TESTED in these two cycles, despite the stage PASS markers. The
  committed scenario has subsequently been fixed; re-execution is required.
- Core CLI: PASS - the two PR #45 negative contracts on the real guest ELF:
  healthcheck --mode=lab-probe-invalid -> EXIT 2 with the exact text
  "Invalid --mode value 'lab-probe-invalid' (supported: liveness, ready)";
  telemt run with the PR #45 malformed TOML -> EXIT 1 + "unclosed table",
  no panic.  PASS in BOTH cycles.  NOTE: Core has no supported --version
  (PR #45: it takes the server path); the ELF-execution proof uses the two
  proven negative contracts instead.
- Core process/socket: BLOCKED - see below
- Panel: opkg install + in-guest ELF pin PASS; `telemt-panel --version`
  inside the guest NOT_TESTED (no dedicated scenario step in this round;
  host-side qemu-user probe of the pinned ELF prints
  "telemt-panel 1.0.0-rc.4 (lite: memory)" EXIT 0); Panel start/HTTP
  NOT_TESTED (the Lite binary takes only -config and its config schema is
  undiscovered research)
- reboot / rc.unslung autostart: NOT_TESTED (the scenario stops at step 44,
  before the native reboot; the sourcing-safe S99telemtlab wrapper, the
  UART marker synchronization and the gate derivation are committed and
  unit-tested, ready for the next attempt)
- workflow and artifacts: .github/workflows/mt7621-telemt-lab.yml on
  Draft PR #93 (acquire job pass/skip-documented; guest job activates as
  soon as the TELEMT_IPK_TOKEN secret with actions:read on
  saymer-alt/entware-go exists); the pinned artifact source is
  https://github.com/saymer-alt/entware-go/actions/runs/43742529964
  (artifact names telemt-core-mipsel-experimental / telemt-panel-lite-
  mipsel-experimental).  The local guest evidence is this folder:
  report.json + junit.xml of the diagnostic retry (sanitized: actions,
  outcomes, timings only). A PASS marker from the original guest checksum
  stage is NOT proof of two-file guest hashing (manifest fixed afterward).

The BLOCKED stage in detail
---------------------------
What both real guest cycles prove (report.json rows, all PASS):
  steps 0-32  clean Entware baseline verbatim (incl. its native reboot)
  33 APPLET_READY          busybox base64/readlink/wget/sha256sum present
  34 TELEMT_FILES_READY    S99telemtlab + synthetic config + PR #45 broken
                           TOML seeded over SSH (chmod 700/600)
  35 IPKS_DELIVERED        both IPKs downloaded; original manifest only
                           verified Panel SHA in guest (Core host-pinned).
  36 OPKG_CORE_OK          native `opkg install /opt/lab/telemt.ipk`,
                           `opkg status telemt` = Version: 3.5.17-1
  37 OPKG_PANEL_OK         native `opkg install
                           /opt/lab/telemt-panel.ipk`, opkg status
                           = Version: 1.0.0~rc.4-1
  38 BINARIES_PINNED       installed Panel ELF SHA verified in guest;
                           Core ELF SHA verified on host only (see erratum).
  39/40 CLI_REJECTED + text   healthcheck invalid mode EXIT 2, exact stderr
  41 TOML_REJECTED         run with malformed TOML EXIT 1 + unclosed table,
                           no panic
  42 TELEMT_STARTED        `telemt start --log-file /opt/var/log/
                           telemt-lab.log --pid-file /opt/var/run/
                           telemt-lab.pid /opt/etc/telemt-lab.toml` exits 0
  43 DAEMON_PROC_ALIVE     FAIL (cycle 2, ssh_exit_nonzero_1, 0.69 s):
                           the pidfile's pid has NO live /proc/<pid>/cmdline
                           within ~2 s of a successful daemon start

Cycle 1 additionally proved: the bounded liveness healthcheck (20 x 2 s)
never observed a working API (45 s of failures) - consistent with the
child dying at/near startup, which cycle 2 then pinned directly.

Why the exact crash point is unknown: the daemon child redirects
stdio to /dev/null and logs to /opt/var/log/telemt-lab.log (CLI docs);
that file lived in the guest-side disposable USB image which the runner
rmtree's after every run.  Both cycles' copies are destroyed; the UART
contains no daemon output (it never reached a console-emitting stage).
The task budget allows at most one full guest re-run after a proven
fixture fix; both cycles are used, so guest testing STOPS here and this
is the diagnostic handoff.

Ranked root-cause hypotheses (upstream source, telemt/telemt @ main):
  1. Proxy-secret acquisition.  general.proxy_secret_path defaults to the
     relative "proxy-secret"; fetch_proxy_secret_with_upstream() tries
     https://core.telegram.org/getProxySecret FIRST, then falls back to
     the file.  With me2dc_fallback=true (default) a total failure is NOT
     fatal ("falling back to direct mode"), so this alone should not kill
     the child - but the offline download attempt and any ME startup
     sequencing remain the prime suspect for a hang/abort interaction.
     NEXT-RUN FIX: seed a synthetic 16-byte secret and set an absolute
     proxy_secret_path (no real Telegram material involved).
  2. quota_state durable publication ("Initial durable publication must
     also succeed before startup can complete") - default path
     "telemt.limit.json" resolves against the daemon's cwd, which is "/"
     after daemonize (chdir("/") in the double-fork); root-writable, but
     an fs-permission/atomic-replace quirk on / is possible.
  3. tls_emulation cache init (tls_front_dir "tlsfront", relative -> "/")
     with censorship defaults; mask is already disabled in the lab config.
  4. A qemu-user-only gap (NOT a guest explanation, documented for
     entware-go): under qemu-mipsel 10.2.1 the server path panics at
     rand 0.10.2 ThreadRng init ("Function not implemented (os error 89)")
     right after the "Direct relay buffer budget initialized" line - the
     RNG seeding syscall is unavailable under qemu-user, so the full
     server path cannot be probed host-side.  PR #45 never started the
     server, so this was invisible until this task.

Next-run recipe (one instrumented attempt, no code changes beyond fixtures
and two step reorderings):
  1. Add to the seeded fixture set: a synthetic proxy-secret file; config
     gains proxy_secret_path = "/opt/etc/telemt-lab.secret".
  2. Start the daemon with --log-file /dev/console so every startup line
     lands in the private UART (the run keeps a private console log).
  3. Order: TELEMT_STARTED -> LOG_EMIT (cat the log file AND rely on
     /dev/console output) -> DAEMON_PROC_ALIVE -> HEALTH_LIVE; whichever
     stage fails, the UART then names the exact failing component.
  4. Optionally add a `telemt-panel --version` step (expect
     "telemt-panel 1.0.0-rc.4 (lite: memory)") - the host-side probe
     already shows the exact output.

Sanitization
------------
report.json and junit.xml contain only step actions/outcomes/timings and
the scenario name (no credentials, no host paths, no addresses, no UART).
The private console logs, the mirror cache and the disposable images stay
outside the repository.  The synthetic secret is generated locally; no
real Telegram keys, tokens or clients are involved anywhere.

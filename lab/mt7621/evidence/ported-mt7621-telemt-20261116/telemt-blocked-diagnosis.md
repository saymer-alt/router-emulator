TeleMT MIPSel pinned-IPK guest lab (Issue #91 / Draft PR #93) - ROOT CAUSE FOUND, Core BLOCKED
==============================================================================================

Status update (2026-11-16, diagnostic cycle 3)
----------------------------------------------
The assignment's follow-up round asked for the exact reason the daemon child
dies right after a successful `telemt start`.  The instrumented diagnostic
cycle (73-step scenario, commit c53237e) captured it.  The child dies because
the pinned Core 3.5.17 binary cannot initialize its userspace RNG on the
Ported KeeneticOS kernel:

    thread 'tokio-rt-worker' (864) panicked at
      /home/runner/.cargo/registry/src/index.crates.io-1949cf8c6b5b557f/
      rand-0.10.2/src/rngs/thread.rs:163:17:
    could not initialize ThreadRng: Function not implemented (os error 89)
    ...
    thread 'main' (860) panicked at ... rand-0.10.2/src/rngs/thread.rs:163:17:
    could not initialize ThreadRng: Function not implemented (os error 89)
    WARN telemt::api: Runtime generation channel closed before API bootstrap

- os error 89 = ENOSYS: the guest kernel ("Linux version 4.9-ndm-5") does not
  expose the getrandom(2) syscall.  Its random driver works fine
  ("random: crng init done" is in the boot log), i.e. /dev/urandom serves
  entropy, but the syscall rand 0.10.2's ThreadRng requires is not there.
- The panic is invisible in daemon mode: stderr is redirected to /dev/null
  by the double-fork, and the tracing file log ends at the channel-closed
  warning.  It became visible only when the new FOREGROUND diagnostic step
  ran `telemt start --foreground` with stdio attached.
- This also retro-explains the qemu-user probe finding from the first round:
  the same ENOSYS panic class, there caused by qemu-user 10.2.1 not
  emulating the MIPS getrandom syscall; here the real guest kernel itself
  lacks it.
- The failure sequence is identical in all three cycles: `start` exits 0
  (the parent forks and exits before the child reaches RNG init), the
  daemonized child panics ~1.5 s later, the pidfile pid has no
  /proc/<pid>/cmdline, and the API never comes up.

Result line (the required field list)
-------------------------------------
- main SHA: 8feaa9cf3930c5c7dfc71e12e6bf24c801c39d57
- dependent PR SHA: c53237e (Draft PR #93 head at the diagnostic cycle;
  unit suite 288 tests green locally and in CI)
- exact IPK SHA+ABI (unchanged, frozen entware-go artifacts):
    Core  telemt_3.5.17-1_mipsel-3.4.ipk        IPK SHA-256 b8dfbb83380d8f4353151b0de43d8c34ceb2369aea45cb6ddaf3b3089a798e5d
                                                ELF SHA-256 3efa4a53c1d50bf1b7a0ed6ca7de98201faf0fa2e27eaaf5f1cd589a6bf26063
                                                ELF32 MIPS LE, dynamic, PT_MIPS_ABIFLAGS.fp_abi=3 (softfloat),
                                                interpreter /opt/lib/ld.so.1
    Panel telemt-panel-lite_1.0.0.rc.4-1_mipsel-3.4.ipk
                                                IPK SHA-256 0236dfb076b14b129a6392de49f287838a11aebe7681e092084d4ce87e771e68
                                                ELF SHA-256 380cb5112b58538eafec9ae9fab27fb22c994fd36ce3130c541c5e11b29a40c8
                                                ELF32 MIPS LE, static ET_EXEC pure-Go, fp_abi=3
- host gates: PASS (fail-closed IPK/ELF/ABI auditor re-run at cycle start;
  both pinned artifacts verified again before the guest handoff)
- QEMU MT7621 Entware: PASS - the committed 33-step tz-skip baseline passed
  verbatim inside the diagnostic cycle as well (clean install, native
  reboot, persistence proofs, 4 VPE)
- opkg: PASS (third consecutive cycle) - loopback mirror delivery, in-guest
  dual sha256sum -c against both full IPK pins (the two-printf manifest bug
  found in round 2 is fixed: ONE printf builds expected.sha, so the guest
  really verified BOTH artifacts this time), native opkg install of both
  IPKs, opkg status shows telemt 3.5.17-1 and telemt-panel-lite
  1.0.0~rc.4-1, in-guest dual ELF sha256sum -c against both ELF pins
  (BINARIES_PINNED).  The round-2 erratum ("guest proof for both hashes NOT
  TESTED") is now closed.
- Core CLI: PASS - both PR #45 negative contracts reproduced on the real
  guest ELF (healthcheck invalid mode EXIT 2 with the exact text; run with
  malformed TOML EXIT 1 + "unclosed table", no panic)
- Core process/socket: BLOCKED - root cause PROVEN, see above
- Panel: opkg install + in-guest ELF pin PASS; `telemt-panel --version`
  inside the guest NOT_TESTED (host-side qemu-user probe prints
  "telemt-panel 1.0.0-rc.4 (lite: memory)" EXIT 0); Panel start/HTTP
  NOT_TESTED
- reboot / rc.unslung autostart: NOT_TESTED - the chain cannot pass while
  the Core child dies at RNG init; the scenario keeps the full reboot +
  rc.unslung stage (sourcing-safe S99telemtlab wrapper, UART marker sync,
  gate derivation) ready for an upstream-fixed binary
- workflow and artifacts: .github/workflows/mt7621-telemt-lab.yml on
  Draft PR #93 (acquire job pass/skip-documented; the guest job activates
  when the TELEMT_IPK_TOKEN secret exists).  Local guest evidence: this
  folder's report-diagnostic.json + junit-diagnostic.xml (46 rows, 45 PASS,
  1 FAIL = the DAEMON_PROC_ALIVE stage, 598.6 s, sanitized: actions,
  outcomes, timings only).

Why the root cause cannot be fixed inside this task
---------------------------------------------------
1. The IPKs are frozen experimental artifacts (entware-go run 43742529964);
   rebuilding them with a rand fix would break the SHA pins and is excluded
   by the assignment.
2. The firmware is a pinned Ported image; replacing the kernel to gain
   getrandom(2) would change the platform under test.
3. Upstream (telemt/telemt) would need either a getrandom(2)-ENOSYS
   fallback (e.g. seed ThreadRng via /dev/urandom when the syscall returns
   ENOSYS) or a kernel requirement note for KeeneticOS targets.  That is
   outside this repository and this task.
4. An LD_PRELOAD getrandom shim was considered and rejected: it would fake
   a libc symbol to produce a green marker, which the task rules exclude
   ("no C-ABI fakes"); a PASS claim under such a shim would misrepresent
   the platform result.

What the diagnostic cycle changed (c53237e)
-------------------------------------------
- fixtures: absolute proxy_secret_path + quota_state_path in the synthetic
  config (no relative-path variance between foreground and daemon runs); a
  32-byte synthetic proxy-secret (PROXY_SECRET_MIN_LEN) seeded with an
  in-guest length guard;
- scenario step 42 DIAG_CAPTURED: bounded FOREGROUND probe
  (`telemt start --foreground`, kill -0 polling loop, TERM after 20 s - the
  guest busybox 1.38 has no timeout applet) capturing tracing output AND
  the final panic text to stdout + the private UART console BEFORE the real
  daemon start;
- log emission moved BEFORE the pid-liveness check (3 s settle), and both
  failing branches (TELEMT_STARTED, DAEMON_PROC_ALIVE) dump the daemon log
  to stdout and the UART before exiting nonzero.

Sanitization
------------
report-diagnostic.json and junit-diagnostic.xml contain only step actions,
outcomes and timings plus the scenario name (no credentials, no host paths,
no addresses, no UART dump).  The private console logs, the mirror cache and
the disposable images stay outside the repository.  The synthetic secret is
generated locally; no real Telegram keys, tokens or clients are involved
anywhere.  Real Telegram upstream remains NOT_TESTED.

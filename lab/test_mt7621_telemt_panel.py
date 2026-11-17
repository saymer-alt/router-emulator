"""Contract tests for the Stage G Panel Lite scenario (issue #94).

No guest execution here: the committed scenario/fixtures/gate are validated
against the proof requirements.  The verified Core acceptance stages of the
getfb scenario (PR #95) must stay byte-identical; the added Panel stages must
ground every claim in real HTTP/process evidence: echo-safe markers,
evidence-first output before assertions, loopback-only binds, synthetic
credentials, exe-based liveness, and the actual panel->Core protocol
(GET /api/telemt/info -> Core /v1/system/info) for the integration claim.
"""
import base64
import json
import re
import unittest
from pathlib import Path

from lab.mt7621 import mt7621_gates as mg
from lab.mt7621 import telemt_lab as tl


def row(action, outcome="PASS"):
    return {"action": action, "outcome": outcome, "elapsed_seconds": 0.1}


def scenario(name="s", steps=()):
    return {"name": name, "steps": list(steps)}


def _folder():
    return Path(__file__).resolve().parents[1] / "lab"


def _load(name):
    from lab import run as runner
    return runner.load_scenario(str(_folder() / "scenarios" / name))


# panel step indices inside the 93-step scenario
P_VERSION, P_CFG, P_BADCFG, P_START, P_PROC, P_HEALTH, P_SOCKET, P_AUTH, \
    P_AUTHNEG, P_INTEGRATION, P_STABLE, P_STOPPED = range(55, 67)
P_BOOTWAIT = 77
P_AUTOHEALTH, P_AUTOPID, P_SINGLE, P_GEN2, P_PERSISTED, P_INTEGRATION2 = \
    range(86, 92)


class PanelScenarioStructureTests(unittest.TestCase):
    @staticmethod
    def inputs():
        core = _load("ported-mt7621-telemt.json")
        panel = _load("ported-mt7621-telemt-panel.json")
        return core, panel

    def test_panel_scenario_extends_the_verified_core_scenario(self):
        core, panel = self.inputs()
        self.assertEqual(len(core["steps"]), 74)
        self.assertEqual(len(panel["steps"]), 93)
        # baseline + core fixture/acceptance stages byte-identical
        self.assertEqual(panel["steps"][:55], core["steps"][:55])
        # getfb steps 55..64 (pre-reboot off .. core UART marker) unchanged
        self.assertEqual(panel["steps"][67:77], core["steps"][55:65])
        # getfb steps 65..72 (telnet/autostart proofs) unchanged
        self.assertEqual(panel["steps"][78:86], core["steps"][65:73])
        # the verified sync proof stays the FINAL step
        self.assertEqual(panel["steps"][92], core["steps"][73])
        self.assertEqual(panel["steps"][92]["expect"], "SYNC_COMPLETE")

    def test_no_embedded_nul_bytes_in_any_send(self):
        _, panel = self.inputs()
        for i, step in enumerate(panel["steps"]):
            self.assertNotIn("\x00", step.get("send", ""),
                             f"step {i} carries an embedded NUL byte")

    def test_every_ssh_step_has_the_lab_shape(self):
        _, panel = self.inputs()
        for i, step in enumerate(panel["steps"]):
            if step.get("action") == "ssh":
                self.assertEqual(step.get("port"), 8022, f"step {i}")
                self.assertTrue(step.get("timeout_sec"), f"step {i}")

    def test_panel_markers_are_unique_and_echo_safe(self):
        _, panel = self.inputs()
        seen = {}
        for i, step in enumerate(panel["steps"]):
            expect = step.get("expect")
            if not expect or not expect.startswith("PANEL_"):
                continue
            self.assertNotIn(expect, step.get("send", ""),
                             f"step {i} expect leaks into its send text")
            self.assertNotIn(expect, seen,
                             f"marker {expect} used twice")
            seen[expect] = i
        # the two start-marker regexes are not PANEL_-prefixed but must also
        # be echo-safe (the wrapper b64 blob never carries raw marker text)
        for i in (P_START, P_BOOTWAIT):
            step = panel["steps"][i]
            marker = step.get("expect") or step.get("pattern", "")
            self.assertNotIn(marker, step.get("send", ""), f"step {i}")

    def test_panel_gate_derives_on_the_panel_scenario_and_core_gates_pass_too(self):
        # the Core acceptance stages are byte-identical (asserted above), so a
        # full-PASS run of the panel scenario also satisfies the whole
        # MIPSEL_TELEMT chain; each gate derives from its OWN scenario/report
        # name pair, exactly as lab/run.py produces them
        core, panel = self.inputs()
        panel_report = {"scenario": panel["name"], "outcome": "PASS",
                        "steps": [row(s["action"]) for s in panel["steps"]]}
        gates = mg.evaluate(core, panel_report, panel_scenario=panel,
                            panel_report=panel_report)
        self.assertEqual(gates["MIPSEL_TELEMT_PANEL"]["status"], "PASS",
                         gates["MIPSEL_TELEMT_PANEL"])
        core_report = {"scenario": core["name"], "outcome": "PASS",
                       "steps": [row(s["action"]) for s in core["steps"]]}
        gates = mg.evaluate(core, core_report, telemt_scenario=core,
                            telemt_report=core_report)
        self.assertEqual(gates["MIPSEL_TELEMT"]["status"], "PASS",
                         gates["MIPSEL_TELEMT"])


class PanelStageEvidenceTests(unittest.TestCase):
    """Evidence-first doctrine: the raw output a claim is judged on reaches
    stdout AND the UART console BEFORE any assertion greps it, because the
    disposable work-dir image is destroyed when a step fails."""

    @staticmethod
    def inputs():
        return _load("ported-mt7621-telemt-panel.json")["steps"]

    def assert_evidence_first(self, idx, claim_grep):
        send = self.inputs()[idx]["send"]
        first_console = send.index("/dev/console")
        first_grep = send.index(claim_grep)
        self.assertLess(send.index("cat ", 0, first_console) if "cat " in send[:first_console] else send.index("printf '%s\\n' \"$"),
                        first_grep,
                        f"step {idx}: evidence must precede the {claim_grep} assertion")

    def test_version_output_is_captured_before_the_assertion(self):
        send = self.inputs()[P_VERSION]["send"]
        self.assertLess(send.index("cat /opt/lab/panel-version.out"),
                        send.index("grep -q 'telemt-panel 1.0.0-rc.4 (lite: memory)'"))
        self.assertIn("/dev/console", send)

    def test_badcfg_output_is_captured_before_the_assertion(self):
        send = self.inputs()[P_BADCFG]["send"]
        self.assertLess(send.index("cat /opt/lab/panel-bad.out"),
                        send.index("grep -qi 'unknown configuration fields'"))
        self.assertIn("/dev/console", send)

    def test_health_body_is_captured_before_the_assertions(self):
        send = self.inputs()[P_HEALTH]["send"]
        body = send.index("printf '%s\\n' \"$body\"")
        self.assertLess(body, send.index('grep -q \'"status":"ok"\''))
        self.assertIn("/dev/console", send)
        # grounded body assertions from the audited handler
        for needle in ('"status":"ok"', '"version":"1.0.0-rc.4"',
                       '"variant":"lite"'):
            self.assertIn(needle, send)

    def test_auth_flow_captures_the_me_body_and_keeps_the_cookie_private(self):
        send = self.inputs()[P_AUTH]["send"]
        self.assertIn("/api/auth/login", send)
        self.assertIn("panel_session=", send)
        self.assertIn("/api/auth/me", send)
        self.assertLess(send.index("printf 'me=%s\\n' \"$me\""),
                        send.index('grep -q \'"username":"labadmin"\''))
        self.assertIn("/dev/console", send)
        # the session cookie is stored for later steps, never printed
        self.assertIn("panel-cookie", send)
        self.assertNotIn("printf '%s\\n' \"$c\"", send)

    def test_auth_negative_proves_401_not_absence(self):
        send = self.inputs()[P_AUTHNEG]["send"]
        self.assertIn("panel-401.out", send)
        self.assertLess(send.index("cat /opt/lab/panel-401.out"),
                        send.index("grep -q '401'"))
        self.assertIn('"$rc" != 0', send)

    def test_integration_probe_uses_the_real_panel_core_protocol(self):
        send = self.inputs()[P_INTEGRATION]["send"]
        # grounded integration contract: panel /api/telemt/info -> Core
        # /v1/system/info (telemt_handler.go:61, read_routes.rs:67)
        self.assertIn("/api/telemt/info", send)
        self.assertIn('grep -q \'"reachable":true\'', send)
        self.assertIn('grep -q \'"version":"3.5.17\'', send)
        self.assertIn("/dev/console", send)

    def test_socket_check_targets_the_actual_loopback_port_hex(self):
        send = self.inputs()[P_SOCKET]["send"]
        # 8080 == 0x1F90, 127.0.0.1 == 0100007F in /proc/net/tcp
        self.assertIn("0100007F:1F90", send)
        self.assertIn("+0A ", send)

    def test_stability_and_stop_are_measured_not_assumed(self):
        stable = self.inputs()[P_STABLE]["send"]
        self.assertIn("sleep 10", stable)
        self.assertIn("/api/health", stable)
        stopped = self.inputs()[P_STOPPED]["send"]
        self.assertIn("S99telemtlab-panel stop", stopped)
        # no-orphan proof is an exe scan (the scanner's own cmdline contains
        # the needle, so the case pattern must be concatenation-split)
        self.assertIn('"/opt/bin/telemt-pan""el"', stopped)
        self.assertNotIn('"/opt/bin/telemt-panel")', stopped)

    def test_pid_identity_uses_proc_exe_not_the_pidfile_echo(self):
        send = self.inputs()[P_PROC]["send"]
        self.assertIn("readlink /proc/$pid/exe", send)
        self.assertIn("= \"/opt/bin/telemt-panel\"", send)

    def test_config_seed_generates_the_hash_in_guest_and_binds_loopback(self):
        send = self.inputs()[P_CFG]["send"]
        self.assertIn("hash-password", send)
        self.assertIn('listen = "127.0.0.1:8080"', send)
        self.assertIn('url = "http://127.0.0.1:9091"', send)
        self.assertIn('auth_header = ""', send)
        self.assertIn('driver = "memory"', send)
        self.assertIn('service_manager = "none"', send)
        self.assertIn('username = "labadmin"', send)
        self.assertIn("/opt/var/lib/telemt-panel", send)
        self.assertNotIn("0.0.0.0", send)
        # unknown keys are rejected by the audited loader; the negative case
        # reuses the seeded config so the rejection is causally connected
        bad = self.inputs()[P_BADCFG]["send"]
        self.assertIn("lab_unknown_key = 1", bad)
        self.assertIn("-config /opt/lab/panel-bad.toml", bad)


class PanelFixtureAndAutostartTests(unittest.TestCase):
    @staticmethod
    def steps():
        return _load("ported-mt7621-telemt-panel.json")["steps"]

    @staticmethod
    def wrapper():
        root = _folder() / "mt7621" / "fixtures"
        return (root / "S99telemtlab-panel").read_bytes()

    def test_wrapper_is_sourcing_safe_and_self_identifying(self):
        fixture = self.wrapper()
        self.assertTrue(fixture.startswith(b"#!/bin/sh"))
        # rc.unslung SOURCES every executable S* script in one shell: no
        # shell-option changes, no exit, wrapped main call.
        self.assertNotIn(b"set -e", fixture)
        self.assertNotIn(b"set -u", fixture)
        self.assertNotIn(b"exit ", fixture)
        self.assertIn(b'panellab_main "$@"', fixture)
        # Liveness proof is the /proc/<pid>/exe identity.
        self.assertIn(b'readlink "/proc/$1/exe"', fixture)
        self.assertIn(b"grep -q '^/opt/bin/telemt-panel$'", fixture)
        # Persistent boot-generation marker drives the UART autostart proof.
        self.assertIn(b"telemt-panel-lab.gen", fixture)
        self.assertIn(b"telemtlab-panel-start gen=$gen pid=$p", fixture)
        self.assertIn(b"> /dev/console", fixture)
        # Panel Lite runs foreground: the wrapper backgrounds it and records
        # the direct child PID (it ships no pidfile support).
        self.assertIn(b'"$bin" -config "$conf"', fixture)
        self.assertIn(b'printf \'%s\\n\' "$!" > "$pidfile"', fixture)
        # Graceful SIGTERM stop via plain kill(2) (no pidfd dependency).
        self.assertIn(b"panellab_stop", fixture)
        self.assertNotIn(b"curl", fixture)
        self.assertNotIn(b"wget", fixture)

    def test_start_step_bakes_the_fixture_verbatim(self):
        send = self.steps()[P_START]["send"]
        self.assertIn("base64 -d > /opt/etc/init.d/S99telemtlab-panel", send)
        self.assertIn("chmod 700 /opt/etc/init.d/S99telemtlab-panel", send)
        self.assertIn("S99telemtlab-panel start", send)
        encoded = re.findall(r"printf '%s' '([A-Za-z0-9+/=]+)'", send)
        self.assertEqual(len(encoded), 1)
        self.assertEqual(base64.b64decode(encoded[0], validate=True),
                         self.wrapper().replace(b"\r\n", b"\n"))

    def test_panel_boot_marker_is_waited_after_the_core_marker(self):
        steps = self.steps()
        core = [i for i, s in enumerate(steps)
                if s.get("action") == "wait_serial"
                and s.get("pattern") == "telemtlab-start gen=2 pid=[0-9]+"]
        panel = [i for i, s in enumerate(steps)
                 if s.get("action") == "wait_serial"
                 and s.get("pattern") == "telemtlab-panel-start gen=2 pid=[0-9]+"]
        self.assertEqual(len(core), 1)
        self.assertEqual(len(panel), 1)
        # S99telemtlab sorts before S99telemtlab-panel, so the panel UART
        # marker can only pass after the Core one has been consumed.
        self.assertLess(core[0], panel[0])
        self.assertEqual(panel[0], P_BOOTWAIT)

    def test_autostart_proofs_cover_both_daemons_and_integration(self):
        steps = self.steps()
        self.assertIn("/api/health", steps[P_AUTOHEALTH]["send"])
        self.assertIn('"version":"1.0.0-rc.4"', steps[P_AUTOHEALTH]["send"])
        pid = steps[P_AUTOPID]["send"]
        self.assertIn("/proc/$pid/exe", pid)
        self.assertIn('"/opt/bin/telemt-pan""el"', pid)
        single = steps[P_SINGLE]["send"]
        # exe-based scan counts BOTH workers; both needles are split
        self.assertIn('"/opt/bin/telemt-pan""el"', single)
        self.assertIn('"/opt/bin/tel""emt"', single)
        self.assertIn("[ \"$pn\" = 1 ] && [ \"$cn\" = 1 ]", single)
        self.assertIn("telemt-panel-lab.gen", steps[P_GEN2]["send"])
        persisted = steps[P_PERSISTED]["send"]
        self.assertIn("/opt/etc/telemt-panel/config.toml", persisted)
        self.assertIn("opkg status telemt-panel-lite", persisted)
        self.assertIn("1.0.0~rc.4-1", persisted)
        self.assertIn("3.5.17-2fb1", persisted)
        self.assertIn("/opt/var/lib/telemt-panel", persisted)
        # post-reboot integration re-logins (memory sessions never survive
        # the panel restart) and proves the Core is alive in the same step
        relogin = steps[P_INTEGRATION2]["send"]
        self.assertIn("/api/auth/login", relogin)
        self.assertIn("/api/telemt/info", relogin)
        self.assertIn('"reachable":true', relogin)
        self.assertIn("healthcheck --mode liveness", relogin)


class PanelGateTests(unittest.TestCase):
    @staticmethod
    def full_report(name, count):
        return {"scenario": name, "outcome": "PASS",
                "steps": [row(a) for a in ("ssh",) * count]}

    def _scenario_and_report(self, failed=None):
        panel = _load("ported-mt7621-telemt-panel.json")
        rows = [row(s["action"]) for s in panel["steps"]]
        if failed is not None:
            rows = rows[:failed + 1]
            rows[-1]["outcome"] = "FAIL"
        report = {"scenario": panel["name"],
                  "outcome": "FAIL" if failed is not None else "PASS",
                  "steps": rows}
        return panel, report

    def test_full_pass_report_derives_pass(self):
        panel, report = self._scenario_and_report()
        gates = mg.derive_telemt_panel_gate(panel, report)
        self.assertEqual(gates["MIPSEL_TELEMT_PANEL"]["status"], "PASS",
                         gates["MIPSEL_TELEMT_PANEL"])

    def test_wrong_scenario_name_is_fail_not_pass(self):
        panel, report = self._scenario_and_report()
        report["scenario"] = "something-else"
        gates = mg.derive_telemt_panel_gate(panel, report)
        self.assertEqual(gates["MIPSEL_TELEMT_PANEL"]["status"], "FAIL")
        self.assertEqual(gates["MIPSEL_TELEMT_PANEL"]["reason"],
                         "report_scenario_mismatch")

    def test_chain_step_removal_is_not_tested(self):
        panel, _ = self._scenario_and_report()
        stripped = dict(panel)
        stripped["steps"] = [s for s in panel["steps"]
                             if s.get("expect") != "PANEL_HEALTH_OK"]
        report = {"scenario": stripped["name"], "outcome": "PASS",
                  "steps": [row(s["action"]) for s in stripped["steps"]]}
        gates = mg.derive_telemt_panel_gate(stripped, report)
        self.assertEqual(gates["MIPSEL_TELEMT_PANEL"]["status"], "NOT_TESTED")
        self.assertEqual(gates["MIPSEL_TELEMT_PANEL"]["reason"],
                         "scenario_lacks_panel_chain")

    def test_reordered_chain_is_not_tested(self):
        panel, _ = self._scenario_and_report()
        reordered = dict(panel)
        steps = list(panel["steps"])
        # move the integration proof before the panel start
        steps.remove(steps[P_INTEGRATION])
        steps.insert(P_START, steps.pop(0) if False else steps[P_START])
        reordered["steps"] = steps
        report = {"scenario": reordered["name"], "outcome": "PASS",
                  "steps": [row(s["action"]) for s in reordered["steps"]]}
        gates = mg.derive_telemt_panel_gate(reordered, report)
        self.assertIn(gates["MIPSEL_TELEMT_PANEL"]["status"],
                      ("NOT_TESTED", "FAIL"))

    def test_chain_failure_is_fail(self):
        panel, report = self._scenario_and_report(failed=P_INTEGRATION)
        gates = mg.derive_telemt_panel_gate(panel, report)
        self.assertEqual(gates["MIPSEL_TELEMT_PANEL"]["status"], "FAIL")
        self.assertEqual(gates["MIPSEL_TELEMT_PANEL"]["reason"],
                         "panel_chain_step_failed")

    def test_baseline_failure_is_not_tested(self):
        panel, report = self._scenario_and_report(failed=10)
        gates = mg.derive_telemt_panel_gate(panel, report)
        self.assertEqual(gates["MIPSEL_TELEMT_PANEL"]["status"], "NOT_TESTED")
        self.assertEqual(gates["MIPSEL_TELEMT_PANEL"]["reason"],
                         "prerequisite_failed")

    def test_panel_gate_stays_outside_the_native_gates(self):
        self.assertNotIn("MIPSEL_TELEMT_PANEL", mg.GATES)
        self.assertNotIn("MIPSEL_TELEMT_PANEL", mg.NATIVE_GATES)
        with self.assertRaises(ValueError):
            mg.apply_labels({}, [("MIPSEL_TELEMT_PANEL", "PASS")])


class DriverScenarioSelectionTests(unittest.TestCase):
    def test_driver_knows_both_scenarios(self):
        self.assertTrue(tl.SCENARIO.name.endswith("ported-mt7621-telemt.json"))
        self.assertTrue(tl.PANEL_SCENARIO.name.endswith(
            "ported-mt7621-telemt-panel.json"))
        self.assertEqual(set(tl.SCENARIOS), {"core-getfb", "panel-g"})


if __name__ == "__main__":
    unittest.main()

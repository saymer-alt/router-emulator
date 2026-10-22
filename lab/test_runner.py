#!/usr/bin/env python3
"""Local stdlib tests: no QEMU, firmware, network or GitHub credentials."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

from lab import run as runner


class LabRunnerTests(unittest.TestCase):
    def test_private_credential_is_resolved_only_at_send(self):
        step = {"credential": "password"}
        self.assertEqual(runner.serial_payload(step, {"password": "runtime-only"}), b"runtime-only\r")
        with self.assertRaises(ValueError):
            runner.serial_payload(step)

    def test_reject_ambiguous_or_arbitrary_credential(self):
        with tempfile.TemporaryDirectory() as td:
            for step in ({"action": "send_serial", "text": "x", "credential": "password"},
                         {"action": "send_serial", "credential": "host_token"}):
                with self.assertRaises(ValueError):
                    runner.load_scenario(self.scenario(Path(td), step))

    def test_serial_menu_key_does_not_submit_default(self):
        self.assertEqual(runner.serial_payload({"text": "0", "enter": False}), b"0")
        self.assertEqual(runner.serial_payload({"text": "version"}), b"version\r")

    def test_reject_nonboolean_enter(self):
        with tempfile.TemporaryDirectory() as td:
            path = self.scenario(Path(td), {"action": "send_serial", "text": "0", "enter": "false"})
            with self.assertRaises(ValueError):
                runner.load_scenario(path)

    def scenario(self, root, step):
        p = root / "test.json"
        p.write_text(json.dumps({"name": "lab", "evidence": "OPENWRT_QEMU_SMOKE",
                                 "steps": [step]}), encoding="utf-8")
        return p

    def test_valid_wait_and_explicit_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            path = self.scenario(Path(td), {"action": "wait_serial", "pattern": "BL2:", "timeout_sec": 1})
            self.assertEqual(runner.load_scenario(path)["evidence"], "OPENWRT_QEMU_SMOKE")

    def test_reject_external_http_url(self):
        with tempfile.TemporaryDirectory() as td:
            path = self.scenario(Path(td), {"action": "probe_http",
                "url": "https://example.net/leak", "timeout_sec": 10})
            with self.assertRaises(ValueError):
                runner.load_scenario(path)

    def test_reject_secret_in_url(self):
        with tempfile.TemporaryDirectory() as td:
            path = self.scenario(Path(td), {"action": "probe_http",
                "url": "http://admin:password@127.0.0.1:8080/", "timeout_sec": 10})
            with self.assertRaises(ValueError):
                runner.load_scenario(path)

    def test_reject_missing_timeout(self):
        with tempfile.TemporaryDirectory() as td:
            path = self.scenario(Path(td), {"action": "wait_serial", "pattern": "boot"})
            with self.assertRaises(ValueError):
                runner.load_scenario(path)

    def test_redirects_never_follow(self):
        req = urllib.request.Request("http://127.0.0.1:9999/")
        out = runner.NoRedirect().redirect_request(req, None, 302, "redirect", {}, "https://example.net/")
        self.assertIsNone(out)

    def test_junit_and_json_outputs(self):
        with tempfile.TemporaryDirectory() as td:
            scenario = {"name": "offline", "evidence": "KEENETIC_PORTED_EXPERIMENTAL"}
            rows = [{"action":"wait_serial","outcome":"PASS","elapsed_seconds":1.2},
                    {"action":"reset","outcome":"FAIL","elapsed_seconds":2.1,"reason":"error"}]
            runner.write_report(Path(td), scenario, rows, "FAIL", "error", 3.3)
            j = json.loads((Path(td) / "report.json").read_text())
            self.assertEqual(j["evidence"], "KEENETIC_PORTED_EXPERIMENTAL")
            self.assertEqual(j["outcome"], "FAIL")
            self.assertIn('failures="1"', (Path(td) / "junit.xml").read_text())

    def test_classification_never_inferred_from_pass(self):
        with tempfile.TemporaryDirectory() as td:
            path = self.scenario(Path(td), {"action":"sleep","seconds":1})
            payload = runner.load_scenario(path)
            self.assertNotEqual(payload["evidence"], "KEENETICOS_VIRTUAL")


if __name__ == "__main__":
    unittest.main()

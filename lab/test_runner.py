#!/usr/bin/env python3
"""Local stdlib tests: no QEMU, firmware, network or GitHub credentials."""
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
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

    def test_empty_enter_for_login_is_single_cr(self):
        self.assertEqual(runner.serial_payload({"text": ""}), b"\r")

    def test_uart_login_diagnostic_no_guest_output(self):
        line = runner.uart_login_timeout_category(120, 120, "OPEN", True)
        self.assertEqual(
            line, "UART LOGIN DIAG: guest_rx_after_enter=NONE socket=OPEN qemu=RUNNING")

    def test_uart_login_diagnostic_activity_without_prompt(self):
        line = runner.uart_login_timeout_category(120, 400, "OPEN", True)
        self.assertEqual(
            line, "UART LOGIN DIAG: guest_rx_after_enter=SOME socket=OPEN qemu=RUNNING")

    def test_uart_login_diagnostic_socket_disconnect(self):
        line = runner.uart_login_timeout_category(17, 17, "EOF", True)
        self.assertEqual(
            line, "UART LOGIN DIAG: guest_rx_after_enter=NONE socket=EOF qemu=RUNNING")

    def test_uart_login_diagnostic_qemu_exit(self):
        self.assertEqual(
            runner.uart_login_timeout_category(0, 0, "SOCKET_ERROR", False),
            "UART LOGIN DIAG: guest_rx_after_enter=NONE socket=SOCKET_ERROR qemu=EXITED")

    def test_uart_login_diagnostic_invalid_snapshot_fails_closed(self):
        for args in ((0, -1, "OPEN", True), (1, 0, "OPEN", True),
                     (0, 1, "unexpected", True), (True, 4, "OPEN", True),
                     (0, 4, "OPEN", "true")):
            with self.subTest(args=args):
                self.assertEqual(
                    runner.uart_login_timeout_category(*args),
                    "UART LOGIN DIAG: UNAVAILABLE")

    def test_serial_drain_counts_bytes_without_retaining_guest_content(self):
        client, server = socket.socketpair()
        client.settimeout(0.1)
        telemetry = runner.SerialRxProgress()
        telemetry.connected()
        thread = threading.Thread(target=runner.drain_serial, args=(client, telemetry))
        thread.start()
        try:
            private_bytes = b"password=private-should-never-be-printed"
            server.sendall(private_bytes)
            server.shutdown(socket.SHUT_WR)
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
            received, state = telemetry.snapshot()
            self.assertEqual(received, len(private_bytes))
            self.assertEqual(state, "EOF")
            public = runner.uart_login_timeout_category(
                0, received, state, True)
            self.assertNotIn("private", public)
            self.assertNotIn("password", public)
            self.assertNotIn("should", public)
        finally:
            server.close()
            client.close()
            if thread.is_alive():
                thread.join(timeout=2)

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


class InteractiveStepTests(unittest.TestCase):
    """telnet/ssh steps: loopback-only validation, echo-safe markers,
    timeout/disconnect handling, IAC/ANSI stream filtering."""

    def scenario(self, root, step):
        p = root / "test.json"
        p.write_text(json.dumps({"name": "lab", "evidence": "KEENETICOS_VIRTUAL",
                                 "steps": [step]}), encoding="utf-8")
        return p

    def load(self, step):
        with tempfile.TemporaryDirectory() as td:
            return runner.load_scenario(self.scenario(Path(td), step))

    def refused(self, step):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):
                runner.load_scenario(self.scenario(Path(td), step))

    # --- scenario validation -------------------------------------------
    def test_telnet_greeting_only_is_valid(self):
        payload = self.load({"action": "telnet", "port": 8023,
                             "greeting": r"\(config\)>", "timeout_sec": 5})
        self.assertEqual(payload["steps"][0]["port"], 8023)

    def test_telnet_command_needs_verified_answer(self):
        self.refused({"action": "telnet", "port": 8023, "send": "show version",
                      "timeout_sec": 5})

    def test_telnet_echo_unsafe_marker_refused(self):
        # "version" occurs in the echoed command line: the echo could fake it.
        self.refused({"action": "telnet", "port": 8023, "send": "show version",
                      "expect": "version", "timeout_sec": 5})

    def test_telnet_multiline_send_refused(self):
        self.refused({"action": "telnet", "port": 8023,
                      "send": "show version\nshow system",
                      "expect": "release:", "timeout_sec": 5})

    def test_telnet_only_loopback_no_host_field(self):
        self.refused({"action": "telnet", "host": "example.net", "port": 8023,
                      "greeting": "x", "timeout_sec": 5})
        self.refused({"action": "ssh", "hostname": "example.net", "port": 8022,
                      "send": "id", "expect": "uid", "timeout_sec": 5})

    def test_telnet_port_type_and_range(self):
        for port in ("8023", True, 80, 70000):
            self.refused({"action": "telnet", "port": port, "greeting": "x",
                          "timeout_sec": 5})

    def test_telnet_needs_greeting_or_send(self):
        self.refused({"action": "telnet", "port": 8023, "timeout_sec": 5})

    def test_telnet_expect_without_send_refused(self):
        self.refused({"action": "telnet", "port": 8023, "expect": "release:",
                      "timeout_sec": 5})

    def test_ssh_needs_send_and_rejects_greeting(self):
        self.refused({"action": "ssh", "port": 8022, "timeout_sec": 5})
        self.refused({"action": "ssh", "port": 8022, "greeting": "x",
                      "send": "id", "expect": "uid", "timeout_sec": 5})
        self.load({"action": "ssh", "port": 8022, "send": "uname -m",
                   "expect": "mips", "timeout_sec": 5})

    def test_runtime_access_requires_forwarded_port(self):
        step = {"action": "telnet", "port": 9999, "greeting": "x",
                "timeout_sec": 5}
        with self.assertRaises(ValueError) as cm:
            runner.validate_runtime_access([step], {8023}, None)
        self.assertIn("9999", str(cm.exception))

    def test_runtime_access_requires_credentials_for_ssh(self):
        step = {"action": "ssh", "port": 8022, "send": "id", "expect": "uid",
                "timeout_sec": 5}
        runner.validate_runtime_access([step], {8022},
                                       {"username": "lab", "password": "x"})
        with self.assertRaises(ValueError):
            runner.validate_runtime_access([step], {8022}, None)

    def test_forwarded_ports_parsing(self):
        self.assertEqual(runner.forwarded_ports("8080:80;8023:23,8022:22"),
                         {8080, 8023, 8022})

    # --- stream filter -------------------------------------------------
    def test_cleaner_strips_iac_and_csi(self):
        cleaner = runner.TelnetCleaner()
        self.assertEqual(
            cleaner.feed(b"\xff\xfd\x01\x1b[K(config)> \x1b[K"),
            b"(config)> ")

    def test_cleaner_survives_split_sequences(self):
        cleaner = runner.TelnetCleaner()
        first = cleaner.feed(b"\xff\xfd")
        second = cleaner.feed(b"\x01a\x1b")
        third = cleaner.feed(b"[Ko")
        self.assertEqual(first + second + third, b"ao")

    def test_cleaner_strips_subnegotiation(self):
        cleaner = runner.TelnetCleaner()
        self.assertEqual(
            cleaner.feed(b"\xff\xfa\x18\x00x\xff\xf0ab"), b"ab")

    # --- runtime against a local fake CLI ------------------------------
    def serve(self, script):
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.settimeout(10)
        port = server.getsockname()[1]
        server.listen(4)
        received = []

        def behavior(conn, action):
            if action == "greet":
                conn.sendall(b"\xff\xfd\x01\x1b[K(config)> \x1b[K")
                time.sleep(0.2)
                received.append(conn.recv(1024))
                conn.sendall(b"\r\nrelease: 5.01.C.8.0-2\r\n(config)> \x1b[K")
                time.sleep(0.3)
            elif action == "close":
                conn.close()
                return
            elif action == "silent":
                time.sleep(0.6)

        def worker():
            for action in script:
                conn, _ = server.accept()
                conn.settimeout(5)
                try:
                    behavior(conn, action)
                finally:
                    try:
                        conn.close()
                    except OSError:
                        pass

        threading.Thread(target=worker, daemon=True).start()
        return server, port, received

    def step_run(self, step, port):
        runner.run_telnet_step(step, {port}, lambda s: time.monotonic() + s,
                               lambda: None)

    def test_telnet_greeting_command_answer_passes(self):
        server, port, received = self.serve(["greet"])
        try:
            step = {"action": "telnet", "port": port,
                    "greeting": r"\(config\)>", "send": "show version",
                    "expect": "release:", "timeout_sec": 10}
            self.step_run(step, port)
            self.assertIn(b"show version\n", received[0])
        finally:
            server.close()

    def test_telnet_timeout_without_marker(self):
        server, port, _ = self.serve(["silent", "silent"])
        try:
            step = {"action": "telnet", "port": port,
                    "greeting": r"never", "timeout_sec": 1}
            with self.assertRaises(TimeoutError):
                self.step_run(step, port)
        finally:
            server.close()

    def test_telnet_eof_replays_command_on_fresh_session(self):
        server, port, received = self.serve(["close", "greet"])
        try:
            step = {"action": "telnet", "port": port, "send": "show version",
                    "expect": "release:", "timeout_sec": 15}
            self.step_run(step, port)
            self.assertIn(b"show version\n", received[0])
        finally:
            server.close()

    def test_telnet_refuses_unforwarded_port(self):
        step = {"action": "telnet", "port": 9999, "greeting": "x",
                "timeout_sec": 1}
        with self.assertRaises(ValueError):
            runner.run_telnet_step(step, set(),
                                   lambda s: time.monotonic() + s,
                                   lambda: None)

    def test_ssh_refuses_unforwarded_port_before_paramiko(self):
        step = {"action": "ssh", "port": 9999, "send": "id", "expect": "uid",
                "timeout_sec": 1}
        with self.assertRaises(ValueError):
            runner.run_ssh_step(step, {"username": "lab", "password": "x"},
                                set(), lambda s: time.monotonic() + s,
                                lambda: None)


if __name__ == "__main__":
    unittest.main()

"""Offline local HTTP fixtures; no QEMU, router or network access needed."""

import importlib.util
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import unittest

spec = importlib.util.spec_from_file_location("networkhelm_rci_probe", Path(__file__).with_name("networkhelm_rci_probe.py"))
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class Guest(BaseHTTPRequestHandler):
    mode = "challenge"
    realm, nonce, user, password = "lab", "123456", "tester", "super-secret-fixture"

    def log_message(self, *_args):
        pass

    def respond(self, status, body=b"{}", headers=None):
        self.send_response(status)
        for key, val in (headers or {}).items():
            self.send_header(key, val)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/":
            return self.respond(200, b"ok")
        if self.path == "/auth":
            if self.mode == "public":
                return self.respond(200)
            return self.respond(401, headers={"X-Ndm-Realm": self.realm, "X-Ndm-Challenge": self.nonce,
                                              "Set-Cookie": "session=challenge; HttpOnly"})
        if self.path.startswith("/rci/show/"):
            if self.mode == "bad-json":
                return self.respond(200, b"html")
            if self.mode == "expired-session":
                return self.respond(401)
            if self.mode == "challenge" and self.headers.get("Cookie") != "session=authorized":
                return self.respond(403)
            return self.respond(200, json.dumps({"show": {self.path.rsplit('/', 1)[-1]: {"field": 1}}}).encode())
        return self.respond(404)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/rci/":
            if body != probe.SHOW_BATCH:
                return self.respond(400)
            if self.mode == "batch-denied":
                return self.respond(403)
            if self.mode == "batch-unavailable":
                return self.respond(404)
            if self.mode == "bad-batch-shape":
                return self.respond(200, b"[]")
            if self.mode == "bad-batch-envelope":
                return self.respond(200, b'[{}, {}, {}]')
            if self.mode == "batch-rci-error":
                return self.respond(200, json.dumps([
                    {"show": {"version": {"release": "5.1.8"}}},
                    {"show": {"system": {"status": [{"status": "error", "message": "denied"}]}}},
                    {"show": {"interface": {}}},
                ]).encode())
            return self.respond(200, json.dumps([{"show": {name: {"ok": True}}} for name in probe.SHOW_NAMES]).encode())
        if self.path != "/auth":
            return self.respond(405)
        correct = probe.password_hash(self.nonce, self.user, self.realm, self.password)
        if body.get("login") != self.user or body.get("password") != correct:
            return self.respond(403)
        return self.respond(200, headers={"Set-Cookie": "session=authorized; HttpOnly"})


class ProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Guest)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)

    def test_challenge_login_and_read_only(self):
        Guest.mode = "challenge"
        result = probe.run(self.url, Guest.user, Guest.password)
        self.assertEqual(result["result"], "PASS")
        self.assertEqual(len(result["checks"]), 6)
        self.assertEqual(result["checks"][-1], {"check": "RCI_BATCH_SHOW", "status": "PASS", "reason": "show_batch_ok"})
        self.assertNotIn(Guest.password, json.dumps(result))
        self.assertNotIn("session=", json.dumps(result))

    def test_auth_missing_is_blocked(self):
        Guest.mode = "challenge"
        result = probe.run(self.url)
        self.assertEqual(result["result"], "BLOCKED")
        self.assertEqual(result["checks"][-1]["reason"], "credentials_not_provided")

    def test_bad_json_is_failure(self):
        Guest.mode = "bad-json"
        result = probe.run(self.url, Guest.user, Guest.password)
        self.assertEqual(result["result"], "FAIL")
        self.assertTrue(any(x["reason"] == "invalid_json" for x in result["checks"]))

    def test_public_access(self):
        Guest.mode = "public"
        self.assertEqual(probe.run(self.url)["result"], "PASS")

    def test_bad_password_is_not_leaked(self):
        Guest.mode = "challenge"
        result = probe.run(self.url, Guest.user, "incorrect-secret")
        self.assertEqual(result["result"], "FAIL")
        self.assertNotIn("incorrect-secret", json.dumps(result))
        self.assertEqual(result["checks"][-1]["reason"], "login_http_403")

    def test_expired_session_during_get_is_blocked(self):
        Guest.mode = "expired-session"
        result = probe.run(self.url, Guest.user, Guest.password)
        self.assertEqual(result["result"], "BLOCKED")
        self.assertEqual(result["checks"][-1]["reason"], "http_401")

    def test_batch_error_classification(self):
        for mode, status, reason in (
            ("batch-rci-error", "FAIL", "rci_status_error"),
            ("bad-batch-shape", "FAIL", "invalid_json_shape"),
            ("bad-batch-envelope", "FAIL", "invalid_batch_envelope"),
            ("batch-denied", "BLOCKED", "http_403"),
            ("batch-unavailable", "FAIL", "http_404"),
        ):
            with self.subTest(mode=mode):
                Guest.mode = mode
                result = probe.run(self.url, Guest.user, Guest.password)
                self.assertEqual(result["result"], status)
                self.assertEqual(result["checks"][-1]["reason"], reason)

    def test_no_empty_json_as_success(self):
        self.assertEqual(probe.check_json(b"[]"), "invalid_json_shape")
        self.assertEqual(probe.check_json(b"{}"), "invalid_json_shape")

    def test_nonloopback_rejected(self):
        for url in ("https://127.0.0.1:8080", "http://192.168.1.1", "http://example.com", "http://127.0.0.1:8080/rci", "http://user:pass@localhost"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                probe.parse_target(url)


if __name__ == "__main__":
    unittest.main()

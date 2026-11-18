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
            if self.mode == "challenge" and self.headers.get("Cookie") != "session=authorized":
                return self.respond(403)
            return self.respond(200, json.dumps({"show": {self.path.rsplit('/', 1)[-1]: {"field": 1}}}).encode())
        return self.respond(404)

    def do_POST(self):
        if self.path != "/auth":
            return self.respond(405)
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
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
        self.assertEqual(len(result["checks"]), 5)
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

    def test_nonloopback_rejected(self):
        for url in ("https://127.0.0.1:8080", "http://192.168.1.1", "http://example.com", "http://127.0.0.1:8080/rci", "http://user:pass@localhost"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                probe.parse_target(url)


if __name__ == "__main__":
    unittest.main()

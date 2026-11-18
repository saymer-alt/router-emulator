#!/usr/bin/env python3
"""Read-only localhost RCI smoke probe for an isolated KeeneticOS QEMU guest.

No configuration changes, no response bodies or secrets in reports. The only
POST is the KeeneticOS /auth handshake. Python standard library only.
"""

import argparse
import hashlib
import http.client
import ipaddress
import json
import os
import socket
import sys
from urllib.parse import urlsplit

MAX_RESPONSE = 256 * 1024
PATHS = ("/rci/show/version", "/rci/show/system", "/rci/show/interface")


def parse_target(url):
    parsed = urlsplit(url)
    if parsed.scheme != "http" or not parsed.hostname:
        raise ValueError("only http://localhost or http://127.0.0.1 is allowed")
    if parsed.username or parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("URL must be a bare origin without credentials, path or query")
    host = parsed.hostname
    if host != "localhost":
        try:
            if not ipaddress.ip_address(host).is_loopback:
                raise ValueError("only loopback addresses are allowed")
        except ValueError as err:
            raise ValueError("only loopback addresses are allowed") from err
    try:
        port = parsed.port or 80
    except ValueError as err:
        raise ValueError("invalid port") from err
    return host, port


def get_cookie(headers):
    value = headers.get("set-cookie", "")
    first = value.split(";", 1)[0]
    return first if "=" in first else None


def password_hash(nonce, user, realm, password):
    first = hashlib.md5(f"{user}:{realm}:{password}".encode()).hexdigest()
    return hashlib.sha256((nonce + first).encode()).hexdigest()


class Client:
    def __init__(self, host, port, timeout):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.cookie = None

    def request(self, method, path, payload=None):
        conn = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)
        headers = {"Accept": "application/json"}
        if self.cookie:
            headers["Cookie"] = self.cookie
        if payload is not None:
            headers["Content-Type"] = "application/json"
        try:
            conn.request(method, path, body=None if payload is None else json.dumps(payload), headers=headers)
            response = conn.getresponse()
            body = response.read(MAX_RESPONSE + 1)
            if len(body) > MAX_RESPONSE:
                raise ValueError("response_too_large")
            hdrs = {key.lower(): value for key, value in response.getheaders()}
            cookie = get_cookie(hdrs)
            if cookie:
                self.cookie = cookie
            return response.status, hdrs, body
        finally:
            conn.close()


def check_json(body):
    try:
        obj = json.loads(body)
    except (ValueError, UnicodeError):
        return "invalid_json"
    if not isinstance(obj, (list, dict)):
        return "invalid_json_shape"

    def has_error(value):
        if isinstance(value, dict):
            status = value.get("status")
            if isinstance(status, list):
                for entry in status:
                    if isinstance(entry, dict) and entry.get("status") == "error":
                        return True
            return any(has_error(v) for v in value.values())
        if isinstance(value, list):
            return any(has_error(v) for v in value)
        return False

    return "rci_status_error" if has_error(obj) else None


def run(url, user="", password="", timeout=8):
    host, port = parse_target(url)
    client = Client(host, port, timeout)
    checks = []

    def record(name, status, reason):
        checks.append({"check": name, "status": status, "reason": reason})

    def finish():
        statuses = [entry["status"] for entry in checks]
        result = "FAIL" if "FAIL" in statuses else "BLOCKED" if "BLOCKED" in statuses else "PASS"
        return {"target": f"http://{host}:{port}", "result": result, "checks": checks,
                "scope": "localhost_read_only_rci", "guest_e2e": False}

    try:
        status, _, _ = client.request("GET", "/")
        if status not in (200, 301, 302, 401, 403):
            record("HTTP_REACHABLE", "FAIL", f"http_{status}")
            return finish()
        record("HTTP_REACHABLE", "PASS", f"http_{status}")
        status, headers, _ = client.request("GET", "/auth")
        if status == 200:
            record("AUTH", "PASS", "public_or_existing_session")
        elif status == 401:
            realm, nonce = headers.get("x-ndm-realm"), headers.get("x-ndm-challenge")
            if not realm or not nonce:
                record("AUTH", "FAIL", "missing_challenge_headers")
                return finish()
            if not user or not password:
                record("AUTH", "BLOCKED", "credentials_not_provided")
                return finish()
            secret = password_hash(nonce, user, realm, password)
            code, _, _ = client.request("POST", "/auth", {"login": user, "password": secret})
            if code != 200:
                record("AUTH", "FAIL", f"login_http_{code}")
                return finish()
            record("AUTH", "PASS", "challenge_login_ok")
        else:
            record("AUTH", "FAIL", f"auth_http_{status}")
            return finish()

        for path in PATHS:
            code, _, body = client.request("GET", path)
            name = "RCI_" + path.rsplit("/", 1)[-1].upper()
            if code in (401, 403):
                record(name, "BLOCKED", f"http_{code}")
                break
            if code != 200:
                record(name, "FAIL", f"http_{code}")
                continue
            issue = check_json(body)
            record(name, "FAIL" if issue else "PASS", issue or "json_ok")
        return finish()
    except (OSError, http.client.HTTPException, TimeoutError, ValueError) as exc:
        if isinstance(exc, (TimeoutError, socket.timeout)):
            reason = "timeout"
        elif isinstance(exc, ConnectionRefusedError):
            reason = "connection_refused"
        elif isinstance(exc, ValueError):
            reason = "invalid_response"
        else:
            reason = "transport_error"
        record("TRANSPORT", "FAIL", reason)
        return finish()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8080", help="loopback host-forward origin only")
    parser.add_argument("--timeout", type=float, default=8.0)
    args = parser.parse_args(argv)
    if not (0 < args.timeout <= 60):
        parser.error("timeout must be between 0 and 60 seconds")
    try:
        result = run(args.url, os.environ.get("NETWORKHELM_RCI_USER", ""),
                     os.environ.get("NETWORKHELM_RCI_PASSWORD", ""), args.timeout)
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return {"PASS": 0, "BLOCKED": 2, "FAIL": 1}[result["result"]]


if __name__ == "__main__":
    sys.exit(main())

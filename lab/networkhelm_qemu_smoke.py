#!/usr/bin/env python3
"""MT7621 Ported QEMU -> authentic NDMS CLI -> localhost-only RCI smoke.

Runs the existing scenario with the real QEMU guest, then executes the
NetworkHelm RCI probe *before* the emulator is shut down. No network access
outside loopback, no guest changes, no CI launch.
"""
import argparse
import json
import os
from pathlib import Path
import socket
import sys
from types import SimpleNamespace

from lab import run as runner
from lab import networkhelm_rci_probe as rci

HTTP_PORT, TELNET_PORT = 8080, 8023
FORWARDS = "8080:80;8023:23"


def check_ports_free(ports=(HTTP_PORT, TELNET_PORT)):
    """Fail before starting QEMU rather than probe somebody else's service."""
    for port in ports:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError as exc:
                raise ValueError("local_forward_port_busy_%d" % port) from exc


def private_credentials(filename):
    if not filename:
        return "", ""
    path = Path(filename)
    if not path.is_file() or path.is_symlink():
        raise ValueError("credentials_must_be_private_regular_file")
    if os.name == "posix" and (path.stat().st_mode & 0o077):
        raise ValueError("credentials_permissions_must_be_0600")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("username"), str) or not isinstance(data.get("password"), str):
        raise ValueError("invalid_credentials_format")
    return data["username"], data["password"]


def combined(guest_rc, rci_result):
    if guest_rc:
        return {"guest": "FAIL", "rci": "NOT_TESTED", "outcome": "BLOCKED",
                "reason": "guest_boot_or_cli_unverified"}
    if rci_result is None:
        return {"guest": "PASS", "rci": "NOT_TESTED", "outcome": "BLOCKED",
                "reason": "rci_callback_not_executed"}
    status = rci_result["result"]
    return {"guest": "PASS", "rci": status,
            "outcome": status, "reason": "see_networkhelm-rci_json",
            "gui_tested": False, "physical_router_tested": False}


def smoke(args):
    # Before booting anything, ensure the caller's selected ports are unused.
    check_ports_free()
    username, password = private_credentials(args.credentials_file)
    output = Path(args.output_dir).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("output_directory_must_be_empty")
    output.mkdir(parents=True, exist_ok=True)
    rci_result = None

    def after_scenario(**_context):
        nonlocal rci_result
        rci_result = rci.run("http://127.0.0.1:%d" % HTTP_PORT,
                             user=username, password=password, timeout=8.0)
        # The raw probe report includes only fixed labels and HTTP codes.
        (output / "networkhelm-rci.json").write_text(
            json.dumps(rci_result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")

    opts = SimpleNamespace(
        scenario=str(Path(__file__).resolve().parent / "scenarios" / "ported-mt7621-cli.json"),
        emulator_root=args.emulator_root, nand_dir=args.nand_dir,
        preset="xiaomi-mi-router-3g-breed", output_dir=str(output),
        lan_forwards=FORWARDS, max_seconds=args.max_seconds,
        credentials_file=args.credentials_file, opt_image=None,
        archive_image=None, mirror_port=None, mirror_tls_port=None,
        rtsp_port=None, after_scenario=after_scenario)
    guest_rc = runner.run(opts)
    result = combined(guest_rc, rci_result)
    output.mkdir(parents=True, exist_ok=True)
    (output / "networkhelm-summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    print("NETWORKHELM RCI: %s; guest=%s" % (result["rci"], result["guest"]))
    return 0 if result["outcome"] == "PASS" else 2 if result["outcome"] == "BLOCKED" else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--emulator-root", default=".")
    ap.add_argument("--nand-dir", required=True)
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--credentials-file", help="optional private lab account JSON, mode 0600")
    ap.add_argument("--max-seconds", type=int, default=1800)
    args = ap.parse_args(argv)
    if args.max_seconds < 10:
        ap.error("--max-seconds must be >= 10")
    try:
        return smoke(args)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        # Never print exception detail: may contain private filesystem paths.
        print("NETWORKHELM RCI: BLOCKED — %s" % type(exc).__name__, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

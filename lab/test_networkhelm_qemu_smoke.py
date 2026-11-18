"""Offline tests for the MT7621 QEMU -> RCI bridge; never boots firmware."""

import json
import os
from pathlib import Path
import socket
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from lab import networkhelm_qemu_smoke as smoke


class QemuRciBridgeTests(unittest.TestCase):
    def args(self, tmp):
        return SimpleNamespace(emulator_root=str(tmp), nand_dir=str(tmp / "flash"),
                               output_dir=str(tmp / "out"), credentials_file=None,
                               max_seconds=60)

    def test_busy_forward_is_refused_before_guest_start(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            with self.assertRaisesRegex(ValueError, "local_forward_port_busy"):
                smoke.check_ports_free([sock.getsockname()[1]])

    def test_private_credential_permissions_and_format(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "lab-credentials.json"
            path.write_text(json.dumps({"username": "tester", "password": "lab-fixture-secret"}))
            if os.name == "posix":
                os.chmod(path, 0o644)
                with self.assertRaisesRegex(ValueError, "permissions"):
                    smoke.private_credentials(path)
                os.chmod(path, 0o600)
            self.assertEqual(smoke.private_credentials(path), ("tester", "lab-fixture-secret"))
            path.write_text('{"password": 123}')
            with self.assertRaisesRegex(ValueError, "format"):
                smoke.private_credentials(path)

    def test_combined_does_not_promote_qemu_failure(self):
        self.assertEqual(smoke.combined(1, None)["rci"], "NOT_TESTED")
        self.assertEqual(smoke.combined(0, None)["outcome"], "BLOCKED")
        self.assertEqual(smoke.combined(0, {"result": "BLOCKED"})["outcome"], "BLOCKED")
        self.assertEqual(smoke.combined(0, {"result": "FAIL"})["outcome"], "FAIL")
        self.assertEqual(smoke.combined(0, {"result": "PASS"})["outcome"], "PASS")

    def test_fake_guest_lifecycle_executes_probe_before_shutdown(self):
        with tempfile.TemporaryDirectory() as td:
            args = self.args(Path(td))
            state = {}

            def fake_run(opts):
                self.assertEqual(opts.lan_forwards, "8080:80;8023:23")
                self.assertEqual(opts.preset, "xiaomi-mi-router-3g-breed")
                state["inside_guest"] = True
                opts.after_scenario()
                state["inside_guest"] = False
                return 0

            def fake_probe(*_a, **_kw):
                self.assertTrue(state["inside_guest"])
                return {"result": "PASS", "checks": [{"check": "RCI_BATCH_SHOW", "status": "PASS", "reason": "show_batch_ok"}]}

            with patch.object(smoke, "check_ports_free"), patch.object(smoke.runner, "run", side_effect=fake_run), patch.object(smoke.rci, "run", side_effect=fake_probe):
                self.assertEqual(smoke.smoke(args), 0)
            output = Path(args.output_dir)
            self.assertEqual(json.loads((output / "networkhelm-summary.json").read_text())["outcome"], "PASS")
            self.assertEqual(json.loads((output / "networkhelm-rci.json").read_text())["result"], "PASS")

    def test_no_rci_run_or_false_pass_when_guest_fails(self):
        with tempfile.TemporaryDirectory() as td:
            args = self.args(Path(td))
            with patch.object(smoke, "check_ports_free"), patch.object(smoke.runner, "run", return_value=1), patch.object(smoke.rci, "run") as probe:
                self.assertEqual(smoke.smoke(args), 2)
                probe.assert_not_called()
            self.assertEqual(json.loads((Path(args.output_dir) / "networkhelm-summary.json").read_text())["rci"], "NOT_TESTED")
            self.assertFalse((Path(args.output_dir) / "networkhelm-rci.json").exists())

    def test_rciprober_blocked_never_claims_gui(self):
        with tempfile.TemporaryDirectory() as td:
            args = self.args(Path(td))

            def fake_guest(opts):
                opts.after_scenario()
                return 0

            with patch.object(smoke, "check_ports_free"), patch.object(smoke.runner, "run", side_effect=fake_guest), patch.object(smoke.rci, "run", return_value={"result": "BLOCKED", "checks": []}):
                self.assertEqual(smoke.smoke(args), 2)
            summary = json.loads((Path(args.output_dir) / "networkhelm-summary.json").read_text())
            self.assertEqual(summary["outcome"], "BLOCKED")
            self.assertFalse(summary["gui_tested"])

    def test_existing_output_directory_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            args = self.args(Path(td))
            output = Path(args.output_dir)
            output.mkdir()
            (output / "old-report.json").write_text('{"result":"PASS"}')
            with patch.object(smoke, "check_ports_free"), self.assertRaisesRegex(ValueError, "output_directory"):
                smoke.smoke(args)


if __name__ == "__main__":
    unittest.main()

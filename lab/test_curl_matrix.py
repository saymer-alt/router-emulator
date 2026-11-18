#!/usr/bin/env python3
"""Offline regression for curl/libcurl matrix status handling (issue #108).

Test only selected pure functions/classes from the actual controller source
via AST. This avoids importing the QEMU/SSH harness or installing paramiko
into the lightweight CI job. No firmware, network, /opt or privileged files.
"""
import ast
import re
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parent / "mt7621" / "busybox_matrix.py"


def isolated_controller():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
    needed = {"Recorder", "Matrix", "capture_probe", "plain_curl_expectation"}
    items = [node for node in tree.body
             if isinstance(node, (ast.ClassDef, ast.FunctionDef))
             and node.name in needed]
    if {node.name for node in items} != needed:
        raise AssertionError("missing expected controller definitions")
    scope = {"re": re, "time": time, "Path": Path}
    code = compile(ast.Module(body=items, type_ignores=[]), str(SOURCE), "exec")
    exec(code, scope)
    return scope


class LocalShellGuest:
    """Exercise the same exit-code contract as Guest.execute, without SSH."""

    def execute(self, command, timeout=30):
        result = subprocess.run(["sh", "-c", command], capture_output=True,
                                text=True, timeout=timeout)
        return result.returncode, result.stdout, result.stderr


class CurlStatusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.controller = isolated_controller()

    def run_probe(self, command, expected, arch="mipsel-3.4"):
        with tempfile.TemporaryDirectory() as directory:
            outfile = str(Path(directory) / "capture.log")
            probe = self.controller["capture_probe"](
                command, outfile=outfile, head="head", lines=5)
            rec = self.controller["Recorder"]("curlunit", {"arch": arch})
            mx = self.controller["Matrix"](
                LocalShellGuest(), rec, time.monotonic() + 20)
            entry = mx.run("TEST", "verify original command status",
                           probe, expected, timeout=10)
            return entry, Path(outfile).read_text()

    def test_success_and_output(self):
        result, output = self.run_probe(
            "printf 'healthy libcurl\\n'", {"rc": 0, "contains": ["libcurl"]})
        self.assertEqual((result["rc"], result["status"]), (0, "PASS"))
        self.assertIn("healthy libcurl", output)

    def test_loader_failure_retains_127_not_head_success(self):
        result, output = self.run_probe(
            "printf 'undefined symbol: curl_multi_get_offt\\n' >&2; exit 127",
            {"rc_nonzero": True, "contains": ["curl_multi_get_offt"]})
        self.assertEqual((result["rc"], result["status"]), (127, "PASS"))
        self.assertIn("curl_multi_get_offt", output)

    def test_unexpected_success_is_failure(self):
        result, _ = self.run_probe(
            "printf 'unexpectedly healthy\\n'",
            {"rc_nonzero": True})
        self.assertEqual((result["rc"], result["status"]), (0, "FAIL"))
        self.assertTrue(any("expected nonzero" in x for x in result["reasons"]))

    def test_missing_error_does_not_pass_by_rc_alone(self):
        result, _ = self.run_probe(
            "printf 'unrelated error\\n'; exit 9",
            {"rc_nonzero": True, "contains": ["curl_multi_get_offt"]})
        self.assertEqual((result["rc"], result["status"]), (9, "FAIL"))

    def test_output_preview_does_not_change_command_rc(self):
        result, output = self.run_probe(
            "printf 'first\\nsecond\\nthird\\n'; exit 22",
            {"rc": 22, "contains": ["second"]})
        self.assertEqual((result["rc"], result["status"]), (22, "PASS"))
        self.assertIn("third", output)

    def test_architecture_specific_expected_outcome(self):
        fn = self.controller["plain_curl_expectation"]
        self.assertEqual(fn("mipsel-3.4")["rc"], 0)
        self.assertEqual(fn("aarch64-3.10")["rc_nonzero"], True)
        with self.assertRaises(ValueError):
            fn("unknown-arch")

    def test_expected_failure_serializes_into_junit_as_pass(self):
        result, _ = self.run_probe(
            "printf 'loader err\\n'; exit 127", {"rc_nonzero": True})
        self.assertEqual(result["status"], "PASS")
        rec = self.controller["Recorder"]("curlunit", {})
        rec.record("NEGATIVE", "loader expected", "exit 127",
                   "loader error\\n__RC__127", {"rc_nonzero": True})
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "unit.xml"
            rec.write_junit(path)
            self.assertIn('status="PASS"', path.read_text())
            self.assertNotIn("<failure", path.read_text())


if __name__ == "__main__":
    unittest.main()

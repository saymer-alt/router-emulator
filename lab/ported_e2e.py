#!/usr/bin/env python3
"""Isolated Ported laboratory controller; all guest assets remain private."""
import argparse
import json
import os
from pathlib import Path
import shutil
from types import SimpleNamespace

from lab.gates import Evidence
from lab.provision_ported import provision
from lab.run import run


def wait(pattern, timeout=120):
    return {'action': 'wait_serial', 'pattern': pattern, 'timeout_sec': timeout}


def send(text):
    return {'action': 'send_serial', 'text': text}


def login_steps():
    return [
        wait('Core::Server: system ready', 240),
        # Native init defers its console dispatcher during initial startup.
        # Connecting/draining from launch avoids blocking the UART transmitter.
        {'action': 'sleep', 'seconds': 120}, send(''), wait('Login:', 120),
        {'action': 'send_serial', 'credential': 'username'}, wait('Password:', 20),
        {'action': 'send_serial', 'credential': 'password'}, wait(r'\(config\)>', 40),
        send('show version'), wait(r'release:\s+5\.01\.C\.8\.0-2', 30), wait(r'\(config\)>', 30)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--emulator-root', required=True)
    parser.add_argument('--nand-dir', required=True)
    parser.add_argument('--private-dir', required=True)
    parser.add_argument('--report-dir', required=True)
    args = parser.parse_args()
    private = Path(args.private_dir).resolve()
    if private.exists():
        parser.error('private workspace must be new')
    private.mkdir(parents=True, mode=0o700)
    os.umask(0o077)
    evidence = Evidence()
    try:
        nand = private / 'nand'
        shutil.copytree(args.nand_dir, nand)
        credentials = provision(nand, private / 'credentials')
        coldboot = json.loads(Path(__file__).with_name('scenarios').joinpath('ported-wr3000p-firstboot.json').read_text())['steps']
        steps = coldboot + login_steps()
        steps += [send('system configuration save'), wait(r'\(config\)>', 60), {'action': 'reset'}]
        steps += coldboot + login_steps()
        scenario = private / 'scenario.json'
        scenario.write_text(json.dumps({'name': 'Ported authenticated CLI and persistence',
                                      'evidence': 'KEENETIC_PORTED_EXPERIMENTAL', 'steps': steps}))
        result = run(SimpleNamespace(scenario=scenario, emulator_root=args.emulator_root,
                     nand_dir=nand, output_dir=private / 'uart', max_seconds=1500,
                     preset='cudy-wr3000p-v1', lan_forwards='8080:80;8443:443;8022:22',
                     credentials_file=credentials))
        report = json.loads((private / 'uart' / 'report.json').read_text())
        rows = report['steps']
        if len(rows) >= len(coldboot) and all(s['outcome'] == 'PASS' for s in rows[:len(coldboot)]):
            evidence.set('BOOT_READY', 'PASS', 'boot_stages_observed')
            evidence.set('AUTH_READY', 'PASS' if result == 0 else 'FAIL',
                         'authenticated_version_and_reboot' if result == 0 else 'serial_timeout')
        else:
            evidence.set('BOOT_READY', 'FAIL', 'serial_timeout')
            evidence.set('AUTH_READY', 'BLOCKED', 'prerequisite_blocked')
        for name in tuple(evidence.gates)[2:]:
            evidence.set(name, 'BLOCKED', 'prerequisite_blocked')
        return result
    except Exception:
        evidence.set('BOOT_READY', 'FAIL', 'runner_failure')
        return 1
    finally:
        evidence.write(args.report_dir)


if __name__ == '__main__':
    raise SystemExit(main())

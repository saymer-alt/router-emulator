#!/usr/bin/env python3
"""Isolated Ported laboratory controller; all guest assets remain private."""
import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from types import SimpleNamespace

from lab.gates import Evidence
from lab.provision_ported import provision, config_image, synthetic_config, entware_config, CONFIG_OFFSET
from lab.prepare_ported_wr3000p import write_at
from lab.run import run, send_monitor


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


def phase_failure(evidence, gate):
    # A later controller error cannot rewrite an already completed gate.
    if evidence.gates[gate]['status'] == 'NOT_TESTED':
        evidence.set(gate, 'FAIL', 'runner_failure')


def run_entware(args, private, credentials, coldboot, evidence):
    # Import only for the actual guest phase; baseline harness tests need no SSH.
    from lab.build_entware import build, add_packages
    from lab.guest import Guest
    from lab.mirror import Mirror
    phase = {'name': 'ENTWARE_READY'}
    evidence.set('ENTWARE_READY', 'NOT_TESTED', 'unattempted')
    nand = private / 'entware-nand'
    shutil.copytree(args.nand_dir, nand)
    files = [next(nand.glob('mt7981.mtd%d.*.bin' % n)) for n in range(6)]
    for path in files:
        os.chmod(path, 0o600)
    secret = json.loads(credentials.read_text())
    write_at(files, CONFIG_OFFSET, config_image(synthetic_config(secret['username'], secret['password'], entware_config())))
    image, mirror = private / 'opt.img', private / 'mirror'
    build(image, mirror, private / 'cache', credentials)
    if args.stage != 'entware':
        add_packages(mirror, private / 'cache', Path(__file__).with_name('kas-packages.lock.json'))
    tls = None
    if args.stage == 'kas':
        from lab.kas_mirror import KASMirror
        tls = KASMirror(args.kas_dir, args.kas_ref, private, private / 'cache')
    completed = {'ok': True}
    steps = coldboot + [wait('Core::Server: system ready', 240),
        {'action': 'wait_serial', 'pattern': r'\[5/5\] "Entware" installed!',
         'reject_pattern': 'Critical error:|Please use ext2/ext3/ext4', 'timeout_sec': 600}]
    scenario = private / 'entware-scenario.json'
    scenario.write_text(json.dumps({'name': 'Native USB Entware and persistence',
                                   'evidence': 'KEENETIC_PORTED_EXPERIMENTAL', 'steps': steps}))

    def probes(**context):
        guest = Guest(credentials, private, deadline=context['deadline'])
        try:
            guest.connect(120)
            marker = guest.entware()
            print('ENTWARE: native shell, real EXT4 and pinned opkg database verified', flush=True)
            guest.command('/bin/ndmc -c "system configuration save"')
            guest.close()
            send_monitor(context['monitor'], 'system_reset')
            time.sleep(5)
            guest.connect(300)
            guest.entware(marker)
            evidence.set('ENTWARE_READY', 'PASS', 'ext4_opkg_abi_and_reboot')
            print('ENTWARE: authenticated SSH, filesystem and packages survived reset', flush=True)
            if args.stage == 'entware':
                return
            phase['name'] = 'KAS_PREFLIGHT'
            evidence.set('KAS_PREFLIGHT', 'NOT_TESTED', 'unattempted')
            guest.command('opkg update && opkg install ca-bundle curl jq iptables ip-full nano cron socat', timeout=240)
            guest.command('/bin/ndmc -c "system configuration save"; sync')
            context['snapshot'](private / 'prepared-entware')
            from lab.preflight import verify
            if not verify(guest, evidence):
                print('KAS PREFLIGHT: BLOCKED by an actual capability probe', flush=True)
                completed['ok'] = False
                return
            print('KAS PREFLIGHT: native runtime capabilities verified', flush=True)
            if args.stage == 'kas':
                from lab.kas_lifecycle import lifecycle
                completed['ok'] = lifecycle(guest, tls, args.kas_ref, evidence, context, private, phase)
        finally:
            guest.close()

    with ExitStack() as stack:
        tls_port = stack.enter_context(tls) if tls else None
        port = stack.enter_context(Mirror(mirror, tls.http_routes if tls else None))
        result = run(SimpleNamespace(scenario=scenario, emulator_root=args.emulator_root,
            nand_dir=nand, output_dir=private / 'entware-uart', max_seconds=2400,
            preset='cudy-wr3000p-v1', lan_forwards='8080:80;8022:222',
            opt_image=image, mirror_port=port, mirror_tls_port=tls_port, after_scenario=probes))
        if result == 0 and completed['ok'] and args.stage == 'kas':
            # A separate copy of the pre-install snapshot exercises collision
            # handling without changing the clean profile's nine gate results.
            from lab.kas_lifecycle import foreign_profile
            completed['ok'] = foreign_profile(args, private, credentials, coldboot,
                                              tls, port, tls_port, evidence)
    if evidence.gates['ENTWARE_READY']['status'] != 'PASS':
        evidence.set('ENTWARE_READY', 'FAIL', 'entware_install_failed')
        return 1
    if result != 0:
        phase_failure(evidence, phase['name'])
        return 1
    return 0 if completed['ok'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--emulator-root', required=True)
    parser.add_argument('--nand-dir', required=True)
    parser.add_argument('--private-dir', required=True)
    parser.add_argument('--report-dir', required=True)
    parser.add_argument('--stage', choices=('entware', 'preflight', 'kas'), default='kas')
    parser.add_argument('--kas-dir')
    parser.add_argument('--kas-ref', default='6dd6438e2b64bad0fe9205ab6575795f808dabd8')
    args = parser.parse_args()
    if args.stage == 'kas' and not args.kas_dir:
        parser.error('--kas-dir is required for actual KAS execution')
    private = Path(args.private_dir).resolve()
    if private.exists():
        parser.error('private workspace must be new')
    private.mkdir(parents=True, mode=0o700)
    os.umask(0o077)
    evidence = Evidence()
    repo = Path(__file__).parent.parent
    evidence.reference('emulator_commit', subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD']).decode().strip())
    evidence.reference('qemu_source_commit', 'f8b2f64e2336a28bf0d50b6ef8a7d8c013e9bcf3')
    evidence.reference('qemu_binary_sha256', hashlib.sha256((Path(args.emulator_root) / 'qemu/qemu-system-aarch64').read_bytes()).hexdigest())
    evidence.reference('qemu_patches_sha256', hashlib.sha256(b''.join(p.read_bytes() for p in sorted((repo / 'qemu-patches').glob('*.patch')))).hexdigest())
    evidence.reference('firmware_sha256', '710e4a37b1aa9f3a65a5122edd0650f5c5ad3e6eca5df53b11ac28aab2664eb7')
    evidence.reference('keenboot_sha256', '8b8615b345d912187853fea6bef6b1868760fd52ed92ba7c3a380ad5533cbf08')
    for name, filename in (('entware_lock_sha256', 'entware.lock.json'), ('kas_assets_lock_sha256', 'kas-assets.lock.json')):
        evidence.reference(name, hashlib.sha256(Path(__file__).with_name(filename).read_bytes()).hexdigest())
    if args.stage == 'kas':
        evidence.reference('kas_commit', args.kas_ref)
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
        if result == 0:
            result = run_entware(args, private, credentials, coldboot, evidence)
        return result
    except Exception:
        if evidence.gates['BOOT_READY']['status'] != 'PASS':
            evidence.set('BOOT_READY', 'FAIL', 'runner_failure')
        elif evidence.gates['AUTH_READY']['status'] == 'PASS':
            attempted = next((name for name in tuple(evidence.gates)[2:]
                              if evidence.gates[name]['status'] == 'NOT_TESTED'), None)
            if attempted:
                evidence.set(attempted, 'FAIL', 'runner_failure')
            elif evidence.gates['ENTWARE_READY']['status'] != 'PASS':
                evidence.set('ENTWARE_READY', 'FAIL', 'runner_failure')
        return 1
    finally:
        evidence.write(args.report_dir)


if __name__ == '__main__':
    raise SystemExit(main())

"""Actual KNVR source in native Entware; synthetic video and disposable EXT4."""
import base64
import hashlib
import json
import re
from pathlib import Path
import shlex
import subprocess
import time

CHECKS = ('source', 'fresh_install', 'recording_3', 'watchdog', 'open_segment',
          'time', 'circular', 'hybrid', 'read_only', 'unmount', 'recovery',
          'reboot', 'busy_guard', 'uninstall', 'repeat', 'removed_reboot', 'reinstall')


class Results:
    def __init__(self, directory, source_ref, evidence=None):
        self.directory = Path(directory)
        self.data = {'platform': 'WR3000P_PORTED_QEMU', 'knvr_commit': source_ref,
                     'hardware_acceptance': 'NOT_TESTED',
                     'video_source': 'LOCAL_SYNTHETIC_RTSP_H264',
                     'storage': 'SEPARATE_REAL_EXT4_USB_IMAGE',
                     'references': dict(evidence.references) if evidence else {},
                     'prerequisites': {n: evidence.gates[n] for n in ('BOOT_READY', 'AUTH_READY', 'ENTWARE_READY')} if evidence else {},
                     'checks': {name: 'NOT_TESTED' for name in CHECKS}}

    def mark(self, name, status):
        self.data['checks'][name] = status
        self.write()
        print('KNVR: %s %s' % (name, status), flush=True)

    def write(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        values = self.data['checks'].values()
        self.data['outcome'] = 'FAIL' if 'FAIL' in values else 'PASS' if all(v == 'PASS' for v in values) else 'BLOCKED'
        (self.directory / 'knvr.json').write_text(json.dumps(self.data, indent=2) + '\n')
        (self.directory / 'knvr.md').write_text('# KNVR virtual acceptance\n\n'
            + '\n'.join('- %s: **%s**' % row for row in self.data['checks'].items())
            + '\n\nPhysical hardware, NAND wear, external cameras and Telegram delivery: NOT_TESTED.\n')


def archive_image(path):
    # Raw filesystem on a separate emulated USB device, not a directory or mock df.
    path = Path(path)
    with path.open('xb') as fh:
        fh.truncate(256 * 1024 * 1024)
    subprocess.run(['mkfs.ext4', '-q', '-F', '-b', '4096', '-L', 'KNVRTEST',
                    '-O', '^orphan_file,^metadata_csum_seed', str(path)], check=True)
    return path


def upload(guest, source, source_ref):
    actual = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    if actual != source_ref or len(actual) != 40:
        raise ValueError('KNVR input must equal the pinned commit')
    data = subprocess.check_output(['git', '-C', str(source), 'archive', '--format=tar.gz', source_ref])
    guest.command('mkdir -p /tmp/knvr-source; base64 -d | tar xz -C /tmp/knvr-source',
                  input_data=base64.b64encode(data).decode() + '\n')
    guest.command('test "$(sha256sum /tmp/knvr-source/uninstall.sh | cut -d " " -f1)" = ' +
                  hashlib.sha256(subprocess.check_output(['git', '-C', str(source), 'show', source_ref + ':uninstall.sh'])).hexdigest())


def wizard(guest, command, answers):
    # A real SSH PTY exercises the unchanged /dev/tty installer, with synthetic credentials.
    stdin, stdout, stderr = guest.client.exec_command(command, get_pty=True, timeout=240)
    channel = stdout.channel
    output = b''
    until = time.monotonic() + 240
    try:
        for prompt, answer in answers:
            while prompt.encode() not in output:
                if time.monotonic() >= until or channel.exit_status_ready():
                    raise RuntimeError('fresh installer prompt unavailable')
                if channel.recv_ready():
                    output += channel.recv(65536)
                else:
                    time.sleep(.1)
            output = b''
            channel.send(answer + '\n')
        while not channel.exit_status_ready():
            if time.monotonic() >= until:
                raise TimeoutError('fresh installer timeout')
            if channel.recv_ready():
                output += channel.recv(65536)
            else:
                time.sleep(.1)
        if channel.recv_exit_status() != 0:
            raise RuntimeError('fresh installer failed')
    finally:
        (guest.private / 'knvr-wizard-private.log').write_bytes(output)
        channel.close()


def recording_files_command(base):
    return ('B=' + shlex.quote(str(base)) + '; '
            'for id in 101 201 301; do '
            'f=$(ls -t "$B/cam$id"/cam${id}_*.mkv 2>/dev/null | head -n 1); '
            'test -s "$f" || exit 1; done')


def checked(guest, text, **kwargs):
    return guest.command('set -e; ' + text, **kwargs)


def lifecycle(guest, args, evidence, context, private, cameras):
    results = Results(args.report_dir, args.knvr_ref, evidence)
    current = 'source'
    q = shlex.quote
    probe_number = 0
    def command(text, **kwargs):
        nonlocal probe_number
        probe_number += 1
        code, data, error = guest.execute('set -e; ' + text, **kwargs)
        if code != 0:
            results.data['failed_probe'] = {'index': probe_number, 'exit_code': code,
                'command_sha256': hashlib.sha256(text.encode()).hexdigest(),
                'syntax_error': 'syntax error' in error.lower(),
                'not_found': 'not found' in error.lower()}
            results.write()
            print('KNVR probe failed: index=%d exit=%d' % (probe_number, code), flush=True)
            raise RuntimeError('native probe failed')
        return data
    try:
        upload(guest, args.knvr_dir, args.knvr_ref)
        command('opkg update && opkg install ffmpeg ffprobe cron curl jq coreutils-stat', timeout=240)
        command('date -s ' + q(time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())))
        mount = command("awk '$3==\"ext4\" && $2 ~ /^\\/tmp\\/mnt\\// && $1 !~ /sda/ {print $2; exit}' /proc/mounts")
        if not mount:
            # Never manufacture a mount record. Require actual native disk automount.
            raise RuntimeError('separate real EXT4 archive was not mounted')
        base = mount + '/cctv'
        results.mark(current, 'PASS')
        current = 'fresh_install'
        command('crontab -', input_data='11 4 * * * echo KNVR_FOREIGN_KEEP\n')
        wizard(guest, 'sh /tmp/knvr-source/NVR/v2/fresh-install.sh install-fresh', [
            ('Choose mount', mount), ('RTSP IP/hostname', '192.168.1.100'),
            ('RTSP username:', 'synthetic'), ('RTSP password', 'synthetic-only'),
            ('RTSP channel IDs', ''), ('Choose storage mode', '1'),
            ('how many hours', '72'), ('Configure notifications', 'n')])
        results.mark(current, 'PASS')
        run = lambda cmd: command('sh /opt/etc/nvr-v2.sh ' + cmd, timeout=90)
        def healthy(timeout=90):
            until = time.monotonic() + timeout
            while time.monotonic() < until:
                text = run('status')
                if all('CAM %s: HEALTHY' % c in text for c in ('101', '201', '301')):
                    code, _, _ = guest.execute(recording_files_command(base))
                    if code == 0:
                        return
                time.sleep(3)
            results.data['camera_diagnostics'] = {camera: next((state for cid, state in re.findall(r'CAM ([0-9]+): (HEALTHY|DOWN|GRACE|STALE|PAUSED|UNKNOWN)', text) if cid == camera), 'UNRECOGNIZED') for camera in ('101', '201', '301')}
            results.write()
            raise RuntimeError('fresh recording not observed')
        current = 'recording_3'
        healthy()
        print('KNVR recording status: HEALTHY 3/3', flush=True)
        results.mark(current, 'PASS')
        current = 'watchdog'
        # First observe a healthy tick: initial start attempts retain a bounded
        # retry backoff until check confirms real footage. Do not erase it.
        run('check')
        command('for id in 101 201 301; do test \"$(cat /tmp/nvr-v2/cam$id.failures)\" = 0 || exit 1; done')
        old_pid = command('cat /tmp/nvr-v2/cam101.pid')
        command('kill -KILL ' + old_pid)
        run('check')
        healthy()
        command('test "$(cat /tmp/nvr-v2/cam101.pid)" != ' + old_pid)
        results.mark(current, 'PASS')
        current = 'open_segment'
        # Open MKV cannot be removed even if its mtime is beyond the time horizon.
        active_pid = command('cat /tmp/nvr-v2/cam101.pid')
        active = command('for fd in /proc/' + active_pid + '/fd/*; do readlink "$fd"; done | grep -E "/cam101/.*[.]mkv$" | head -1')
        if not active.startswith(base + '/cam101/'):
            raise RuntimeError('actual open segment unavailable')
        command('kill -STOP ' + active_pid + '; touch -t 202001010000 ' + q(active))
        try:
            run('cleanup')
            command('test -s ' + q(active))
        finally:
            command('kill -CONT ' + active_pid)
        results.mark(current, 'PASS')
        run('stop')
        # Closed synthetic segments are test data, never production video.
        for mode in ('time', 'circular', 'hybrid'):
            current = mode
            command("sed -i '/^NVR_STORAGE_MODE=/d; /^NVR_DISK_FREE_MIN_MB=/d' /opt/etc/nvr-v2.conf")
            command('cat >> /opt/etc/nvr-v2.conf', input_data=
                          "\nNVR_STORAGE_MODE='%s'\nNVR_DISK_FREE_MIN_MB=0\n" % mode)
            command('mkdir -p ' + q(base + '/cam999'))
            command('sed -i ' + q("s/^NVR_CAMERAS=.*/NVR_CAMERAS='101 201 301 999'/") + ' /opt/etc/nvr-v2.conf')
            old = base + '/cam201/cam201_lab_old.mkv'
            middle = base + '/cam101/cam101_lab_middle.mkv'
            young = base + '/cam999/cam999_lab_young.mkv'
            for path, mb, hours in ((old, 5, 80 if mode != 'circular' else 50),
                                    (middle, 40, 20), (young, 1, 2)):
                stamp = time.strftime('%Y%m%d%H%M.%S', time.gmtime(time.time() - hours * 3600))
                command('dd if=/dev/zero of=' + q(path) + ' bs=1048576 count=' + str(mb) +
                              ' 2>/dev/null && touch -t ' + stamp + ' ' + q(path))
            before = command('sha256sum ' + ' '.join(map(q, (old, middle, young))))
            run('cleanup --dry-run')
            assert command('sha256sum ' + ' '.join(map(q, (old, middle, young)))) == before
            if mode in ('circular', 'hybrid'):
                # Derive pressure from the real filesystem, leaving 8 MiB free.
                # Expiring 5 MiB alone is insufficient; capacity must evict 40 MiB
                # too, globally by mtime, and stop before the 2-hour young file.
                free_kb = int(command('df -Pk ' + q(mount) + " | awk 'NR==2{print $4}'"))
                pressure_mb = free_kb // 1024 - 8
                if pressure_mb <= 0:
                    raise RuntimeError('archive unexpectedly full before pressure fixture')
                command('dd if=/dev/zero of=' + q(mount + '/pressure') +
                              ' bs=1048576 count=' + str(pressure_mb) + ' 2>/dev/null; sync')
                # Preview under actual pressure must also leave bytes unchanged.
                run('cleanup --dry-run')
                assert command('sha256sum ' + ' '.join(map(q, (old, middle, young)))) == before
            run('cleanup')
            command('test ! -e ' + q(old) + ' && test -s ' + q(young))
            if mode == 'time':
                command('test -s ' + q(middle))
            else:
                command('test ! -e ' + q(middle))
                command('rm -f ' + q(mount + '/pressure'))
                available = int(command('df -Pk ' + q(mount) + " | awk 'NR==2{print $4}'"))
                if available <= 8 * 1024:
                    raise RuntimeError('real filesystem space was not reclaimed')
            command('rm -f ' + q(young) + ' ' + q(middle))
            results.mark(current, 'PASS')
        current = 'read_only'
        command('sync; mount -o remount,ro ' + q(mount))
        code, _, _ = guest.execute('sh /opt/etc/nvr-v2.sh cleanup')
        if code == 0:
            raise RuntimeError('read-only cleanup did not refuse')
        results.mark(current, 'PASS')
        command('mount -o remount,rw ' + q(mount))
        current = 'unmount'
        dev = command("awk -v m=" + q(mount) + " '$2==m{print $1}' /proc/mounts")
        command('umount ' + q(mount))
        code, _, _ = guest.execute('sh /opt/etc/nvr-v2.sh cleanup')
        if code == 0:
            raise RuntimeError('unmounted cleanup did not refuse')
        command('test ! -d ' + q(base + '/.nvr-v2'))
        results.mark(current, 'PASS')
        command('mount -t ext4 ' + q(dev) + ' ' + q(mount))
        current = 'recovery'
        command('sed -i ' + q("s/^NVR_CAMERAS=.*/NVR_CAMERAS='101 201 301'/") + ' /opt/etc/nvr-v2.conf')
        run('start'); healthy()
        results.mark(current, 'PASS')
        def reboot():
            command('/bin/ndmc -c "system configuration save"; sync')
            guest.close()
            from lab.run import send_monitor
            send_monitor(context['monitor'], 'system_reset')
            time.sleep(5)
            guest.connect(300)
            command('date -s ' + q(time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())))
        current = 'reboot'
        reboot()
        # A real cron tick, not a manual restart, must restore recording.
        time.sleep(15)
        # Cron check is every five minutes; allow the bounded native tick.
        healthy(360)
        results.mark(current, 'PASS')
        current = 'busy_guard'
        upload(guest, args.knvr_dir, args.knvr_ref)
        # Real production lock refusal, held by the native shell invoking removal.
        code, _, _ = guest.execute('mkdir /tmp/nvr-v2/.lock && echo $$ > /tmp/nvr-v2/.lock/pid; '
                                   'sh /tmp/knvr-source/uninstall.sh --yes', timeout=90)
        if code == 0:
            raise RuntimeError('busy recorder removal falsely succeeded')
        command('test -f /opt/etc/nvr-v2.sh; rm -f /tmp/nvr-v2/.lock/pid; rmdir /tmp/nvr-v2/.lock')
        results.mark(current, 'PASS')
        current = 'uninstall'
        upload(guest, args.knvr_dir, args.knvr_ref)
        config_hash = command('sha256sum /opt/etc/nvr-v2.conf')
        files = command('find ' + q(base) + " -name '*.mkv' | sort")
        packages = command('opkg list-installed')
        command('sh /tmp/knvr-source/uninstall.sh --yes', timeout=120)
        command('test ! -e /opt/etc/nvr-v2.sh; test -z "$(pidof ffmpeg)"; crontab -l | grep -q KNVR_FOREIGN_KEEP')
        assert command('sha256sum /opt/etc/nvr-v2.conf') == config_hash
        assert command('find ' + q(base) + " -name '*.mkv' | sort") == files
        assert command('opkg list-installed') == packages
        results.mark(current, 'PASS')
        current = 'repeat'
        command('sh /tmp/knvr-source/uninstall.sh --yes')
        results.mark(current, 'PASS')
        current = 'removed_reboot'
        reboot()
        command('test ! -e /opt/etc/nvr-v2.sh; test -z "$(pidof ffmpeg)"; crontab -l | grep -q KNVR_FOREIGN_KEEP')
        results.mark(current, 'PASS')
        current = 'reinstall'
        upload(guest, args.knvr_dir, args.knvr_ref)
        command('sh /tmp/knvr-source/NVR/v2/fresh-install.sh install-fresh', timeout=120)
        healthy()
        # Flush real MKV trailers then ffprobe confirms actual H264, not a dummy file.
        run('stop')
        command('f=$(ls -t ' + q(base) + '/cam101/*.mkv | head -1); /opt/bin/ffprobe -v error -show_entries stream=codec_name -of csv=p=0 "$f" | grep -qx h264')
        results.mark(current, 'PASS')
        return True
    except Exception as error:
        (Path(private) / 'knvr-error-private.txt').write_text(type(error).__name__ + ": " + str(error))
        print('KNVR failure class: ' + type(error).__name__, flush=True)
        results.mark(current, 'FAIL')
        return False
    finally:
        results.write()

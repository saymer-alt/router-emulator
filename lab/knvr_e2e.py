"""Actual KNVR source in native Entware; synthetic video and disposable EXT4."""
import base64
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import time

CHECKS = ('source', 'fresh_install', 'recording_3', 'watchdog', 'open_segment',
          'time', 'circular', 'hybrid', 'read_only', 'unmount', 'recovery',
          'reboot', 'uninstall', 'repeat', 'removed_reboot', 'reinstall')


class Results:
    def __init__(self, directory, source_ref):
        self.directory = Path(directory)
        self.data = {'platform': 'WR3000P_PORTED_QEMU', 'knvr_commit': source_ref,
                     'hardware_acceptance': 'NOT_TESTED',
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


def lifecycle(guest, args, evidence, context, private, cameras):
    results = Results(args.report_dir, args.knvr_ref)
    current = 'source'
    q = shlex.quote
    try:
        upload(guest, args.knvr_dir, args.knvr_ref)
        guest.command('opkg update && opkg install ffmpeg ffprobe cron curl jq coreutils-stat', timeout=240)
        guest.command('date -s ' + q(time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())))
        mount = guest.command("awk '$3==\"ext4\" && $2 ~ /^\\/tmp\\/mnt\\// && $1 !~ /sda/ {print $2; exit}' /proc/mounts")
        if not mount:
            # Never manufacture a mount record. Require actual native disk automount.
            raise RuntimeError('separate real EXT4 archive was not mounted')
        base = mount + '/cctv'
        results.mark(current, 'PASS')
        current = 'fresh_install'
        guest.command("printf '%s\\n' '11 4 * * * echo KNVR_FOREIGN_KEEP' | crontab -")
        wizard(guest, 'sh /tmp/knvr-source/NVR/v2/fresh-install.sh install-fresh', [
            ('Choose mount', mount), ('RTSP IP/hostname', '192.168.1.100'),
            ('RTSP username:', 'synthetic'), ('RTSP password', 'synthetic-only'),
            ('RTSP channel IDs', ''), ('Choose storage mode', '1'),
            ('how many hours', '72'), ('Configure notifications', 'n')])
        results.mark(current, 'PASS')
        run = lambda cmd: guest.command('sh /opt/etc/nvr-v2.sh ' + cmd, timeout=90)
        def healthy(timeout=90):
            until = time.monotonic() + timeout
            while time.monotonic() < until:
                text = run('status')
                if all('CAM %s: HEALTHY' % c in text for c in ('101', '201', '301')):
                    return
                time.sleep(3)
            raise RuntimeError('fresh recording not observed')
        current = 'recording_3'
        healthy()
        guest.command('for f in ' + q(base) + '/cam*/*.mkv; do test -s "$f" || exit 1; done')
        results.mark(current, 'PASS')
        current = 'watchdog'
        old_pid = guest.command('cat /tmp/nvr-v2/cam101.pid')
        guest.command('kill -KILL ' + old_pid)
        run('check')
        healthy()
        guest.command('test "$(cat /tmp/nvr-v2/cam101.pid)" != ' + old_pid)
        results.mark(current, 'PASS')
        current = 'open_segment'
        # Open MKV cannot be removed even if its mtime is beyond the time horizon.
        active = guest.command('ls ' + q(base) + '/cam101/*.mkv | head -1')
        active_pid = guest.command('cat /tmp/nvr-v2/cam101.pid')
        guest.command('kill -STOP ' + active_pid + '; touch -t 202001010000 ' + q(active))
        run('cleanup')
        guest.command('test -s ' + q(active) + '; kill -CONT ' + active_pid)
        results.mark(current, 'PASS')
        run('stop')
        # Closed synthetic segments are test data, never production video.
        for mode in ('time', 'circular', 'hybrid'):
            current = mode
            guest.command("sed -i '/^NVR_STORAGE_MODE=/d; /^NVR_DISK_FREE_MIN_MB=/d' /opt/etc/nvr-v2.conf; "
                          "printf '\\nNVR_STORAGE_MODE=%s\\nNVR_DISK_FREE_MIN_MB=0\\n' " + mode + ' >> /opt/etc/nvr-v2.conf')
            guest.command('mkdir -p ' + q(base + '/cam999'))
            guest.command("sed -i \"s/^NVR_CAMERAS=.*/NVR_CAMERAS='101 201 301 999'/\" /opt/etc/nvr-v2.conf")
            old, young = base + '/cam999/cam999_old.mkv', base + '/cam999/cam999_young.mkv'
            guest.command('dd if=/dev/zero of=' + q(old) + ' bs=1048576 count=40 2>/dev/null; '
                          'touch -t 202001010000 ' + q(old) + '; echo young > ' + q(young))
            before = guest.command('sha256sum ' + q(old) + ' ' + q(young))
            run('cleanup --dry-run')
            assert guest.command('sha256sum ' + q(old) + ' ' + q(young)) == before
            if mode in ('circular', 'hybrid'):
                # Fill real filesystem to below 10% reserve without touching /opt.
                guest.command('dd if=/dev/zero of=' + q(mount + '/pressure') + ' bs=1048576 count=190 2>/dev/null; sync')
            run('cleanup')
            guest.command('test ! -e ' + q(old) + '; test -s ' + q(young))
            if mode != 'time':
                guest.command('rm -f ' + q(mount + '/pressure'))
            guest.command('rm -f ' + q(young))
            results.mark(current, 'PASS')
        current = 'read_only'
        guest.command('sync; mount -o remount,ro ' + q(mount))
        code, _, _ = guest.execute('sh /opt/etc/nvr-v2.sh cleanup')
        if code == 0:
            raise RuntimeError('read-only cleanup did not refuse')
        results.mark(current, 'PASS')
        guest.command('mount -o remount,rw ' + q(mount))
        current = 'unmount'
        dev = guest.command("awk -v m=" + q(mount) + " '$2==m{print $1}' /proc/mounts")
        guest.command('umount ' + q(mount))
        code, _, _ = guest.execute('sh /opt/etc/nvr-v2.sh cleanup')
        if code == 0:
            raise RuntimeError('unmounted cleanup did not refuse')
        guest.command('test ! -d ' + q(base + '/.nvr-v2'))
        results.mark(current, 'PASS')
        guest.command('mount -t ext4 ' + q(dev) + ' ' + q(mount))
        current = 'recovery'
        guest.command("sed -i \"s/^NVR_CAMERAS=.*/NVR_CAMERAS='101 201 301'/\" /opt/etc/nvr-v2.conf")
        run('start'); healthy()
        results.mark(current, 'PASS')
        def reboot():
            guest.command('/bin/ndmc -c "system configuration save"; sync')
            guest.close()
            from lab.run import send_monitor
            send_monitor(context['monitor'], 'system_reset')
            time.sleep(5)
            guest.connect(300)
            guest.command('date -s ' + q(time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())))
        current = 'reboot'
        reboot()
        # A real cron tick, not a manual restart, must restore recording.
        time.sleep(15)
        # Cron check is every five minutes; allow the bounded native tick.
        healthy(360)
        results.mark(current, 'PASS')
        current = 'uninstall'
        upload(guest, args.knvr_dir, args.knvr_ref)
        config_hash = guest.command('sha256sum /opt/etc/nvr-v2.conf')
        files = guest.command('find ' + q(base) + " -name '*.mkv' | sort")
        packages = guest.command('opkg list-installed')
        guest.command('sh /tmp/knvr-source/uninstall.sh --yes', timeout=120)
        guest.command('test ! -e /opt/etc/nvr-v2.sh; test -z "$(pidof ffmpeg)"; crontab -l | grep -q KNVR_FOREIGN_KEEP')
        assert guest.command('sha256sum /opt/etc/nvr-v2.conf') == config_hash
        assert guest.command('find ' + q(base) + " -name '*.mkv' | sort") == files
        assert guest.command('opkg list-installed') == packages
        results.mark(current, 'PASS')
        current = 'repeat'
        guest.command('sh /tmp/knvr-source/uninstall.sh --yes')
        results.mark(current, 'PASS')
        current = 'removed_reboot'
        reboot()
        guest.command('test ! -e /opt/etc/nvr-v2.sh; test -z "$(pidof ffmpeg)"; crontab -l | grep -q KNVR_FOREIGN_KEEP')
        results.mark(current, 'PASS')
        current = 'reinstall'
        upload(guest, args.knvr_dir, args.knvr_ref)
        guest.command('sh /tmp/knvr-source/NVR/v2/fresh-install.sh install-fresh', timeout=120)
        healthy()
        # Flush real MKV trailers then ffprobe confirms actual H264, not a dummy file.
        run('stop')
        guest.command('f=$(ls -t ' + q(base) + '/cam101/*.mkv | head -1); /opt/bin/ffprobe -v error -show_entries stream=codec_name -of csv=p=0 "$f" | grep -qx h264')
        results.mark(current, 'PASS')
        return True
    except Exception:
        results.mark(current, 'FAIL')
        return False
    finally:
        results.write()

"""Synthetic local cameras. Real MediaMTX + host FFmpeg, never an external feed."""
from pathlib import Path
import subprocess
import time


class Cameras:
    def __init__(self, binary, private):
        self.binary = Path(binary).resolve()
        self.private = Path(private)
        self.publishers = {}
        self.server = None
        self.port = 18554

    def start_camera(self, camera):
        log = (self.private / ('publisher-%s.log' % camera)).open('ab')
        try:
            self.publishers[camera] = subprocess.Popen([
                'ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-re',
                '-f', 'lavfi', '-i', 'testsrc2=size=160x120:rate=5', '-an',
                '-c:v', 'libx264', '-threads', '1', '-preset', 'ultrafast', '-tune', 'zerolatency',
                '-g', '5', '-f', 'rtsp', '-rtsp_transport', 'tcp',
                'rtsp://127.0.0.1:%d/Streaming/Channels/%s' % (self.port, camera)],
                stdin=subprocess.DEVNULL, stdout=log, stderr=log)
        finally:
            log.close()

    def stop_camera(self, camera):
        proc = self.publishers.pop(camera)
        proc.terminate()
        proc.wait(timeout=10)

    def __enter__(self):
        config = self.private / 'mediamtx.yml'
        config.write_text('logLevel: warn\nrtspAddress: 127.0.0.1:18554\nrtspTransports: [tcp]\n'
                          'rtmp: no\nhls: no\nwebrtc: no\nsrt: no\npaths:\n  all_others:\n')
        log = (self.private / 'mediamtx.log').open('wb')
        try:
            self.server = subprocess.Popen([str(self.binary), str(config)], stdout=log, stderr=log)
            time.sleep(2)
            if self.server.poll() is not None:
                raise RuntimeError('local RTSP server did not start')
            for camera in ('101', '201', '301'):
                self.start_camera(camera)
            time.sleep(3)
            if any(p.poll() is not None for p in self.publishers.values()):
                raise RuntimeError('synthetic camera publisher did not start')
            return self
        except Exception:
            self.__exit__()
            raise
        finally:
            log.close()

    def __exit__(self, *args):
        for camera in tuple(self.publishers):
            self.stop_camera(camera)
        if self.server and self.server.poll() is None:
            self.server.terminate()
            self.server.wait(timeout=10)

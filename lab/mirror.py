"""Read-only loopback server for explicitly pinned package files."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import datetime
import threading


class Mirror:
    def __init__(self, directory, extra_routes=None):
        files = {}
        for feed in ('entware', 'keenetic'):
            for path in (directory / feed).iterdir():
                if not path.is_file() or path.is_symlink():
                    raise ValueError('mirror requires regular pinned files')
                files['/' + feed + '/' + path.name] = path

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                path = files.get(self.path)
                host = self.headers.get('Host', '').split(':', 1)[0].lower()
                extra = (extra_routes or {}).get((host, self.path))
                if extra is not None:
                    data = extra
                elif self.path == '/lab/health':
                    data = b'KEENETIC_LAB_HTTP_OK'
                elif self.path == '/lab/clock':
                    data = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S').encode()
                elif path is not None:
                    data = path.read_bytes()
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header('Content-Type', 'application/octet-stream')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self.server.server_port

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

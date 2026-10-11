"""Local browser UI. Mutation requires same-origin token; no game launch endpoints."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import urllib.parse
from . import experiment


class WebServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, service):
        if address[0] not in ("127.0.0.1", "::1", "localhost"):
            raise ValueError("Web UI must bind loopback")
        super().__init__(address, Handler)
        self.service = service
        self.token = secrets.token_urlsafe(32)

    @property
    def origin(self):
        return f"http://127.0.0.1:{self.server_port}"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, value, status=200, mime="application/json"):
        data = (
            json.dumps(value, allow_nan=False).encode()
            if mime == "application/json"
            else value
        )
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'",
        )
        self.end_headers()
        self.wfile.write(data)

    def safe_host(self):
        return self.headers.get("Host") in (
            f"127.0.0.1:{self.server.server_port}",
            f"localhost:{self.server.server_port}",
        )

    def do_GET(self):
        if not self.safe_host():
            return self.send({"error": "Unexpected Host"}, 403)
        parts = urllib.parse.urlparse(self.path)
        path = parts.path
        s = self.server.service
        try:
            if path == "/api/session":
                return self.send(
                    {
                        "token": self.server.token,
                        "doctor": s.doctor(),
                        "presets": experiment.presets(),
                    }
                )
            if path == "/api/jobs":
                return self.send(s.list())
            if path.startswith("/api/jobs/"):
                fields = path.split("/")
                job = fields[3]
                if len(fields) == 4:
                    return self.send(s.status(job))
                if len(fields) == 5 and fields[4] == "report":
                    return self.send(s.result(job))
                if len(fields) == 5 and fields[4] == "capture":
                    file = s.job_path(job) / "capture.nni"
                    if file.is_symlink() or file.stat().st_size > 128 * 1024 * 1024:
                        raise ValueError("Invalid capture file")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Length", str(file.stat().st_size))
                    self.send_header(
                        "Content-Disposition", 'attachment; filename="' + job + '.nni"'
                    )
                    self.end_headers()
                    with file.open("rb") as source:
                        while chunk := source.read(65536):
                            self.wfile.write(chunk)
                    return
            if path in ("/", "/index.html", "/app.js", "/style.css"):
                name = "index.html" if path == "/" else path[1:]
                mime = {
                    "index.html": "text/html; charset=utf-8",
                    "app.js": "text/javascript; charset=utf-8",
                    "style.css": "text/css; charset=utf-8",
                }[name]
                return self.send(
                    (Path(__file__).parent / "web" / name).read_bytes(), mime=mime
                )
            self.send({"error": "Unknown route"}, 404)
        except (ValueError, RuntimeError, OSError, KeyError) as error:
            self.send({"error": str(error)}, 400)

    def do_POST(self):
        if (
            not self.safe_host()
            or self.headers.get("Origin")
            not in (
                None,
                self.server.origin,
                f"http://localhost:{self.server.server_port}",
            )
            or self.headers.get("X-RocketLab-Token") != self.server.token
        ):
            return self.send({"error": "Same-origin session token required"}, 403)
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= experiment.MAX_JSON:
                raise ValueError("Request body size out of bounds")
            body = json.loads(self.rfile.read(size))
            path = urllib.parse.urlparse(self.path).path
            s = self.server.service
            if not isinstance(body, dict):
                raise ValueError("Object required")
            if path == "/api/validate":
                return self.send(
                    {"valid": True, "experiment": experiment.validate(body)}
                )
            if path == "/api/run":
                if set(body) - {"experiment", "backend"}:
                    raise ValueError("Unknown run fields")
                return self.send(
                    s.submit(body["experiment"], body.get("backend", "demo")), 202
                )
            if path == "/api/cancel":
                return self.send(s.cancel(body["job_id"]))
            self.send({"error": "Unknown route"}, 404)
        except (ValueError, RuntimeError, OSError, KeyError, TypeError) as error:
            self.send({"error": str(error)}, 400)

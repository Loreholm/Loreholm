"""Minimal Tailnet ingress for V2; forwards only the browser-chat contract."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
import os
from urllib.parse import urlsplit

UPSTREAM = urlsplit(os.getenv("LOREHOLM_V2_UPSTREAM", "http://instance:8080"))
ALLOWED = ("/api/chat/",)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        if self.path == "/healthz":
            body = b'{"ok":true,"service":"loreholm-v2-endpoint"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404)

    def do_POST(self):
        if not any(self.path.startswith(prefix) for prefix in ALLOWED):
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        connection = http.client.HTTPConnection(UPSTREAM.hostname, UPSTREAM.port or 80, timeout=190)
        headers = {
            "Authorization": self.headers.get("Authorization", ""),
            "Content-Type": self.headers.get("Content-Type", "application/json"),
            "Accept": self.headers.get("Accept", "text/event-stream"),
        }
        try:
            connection.request("POST", self.path, body=body, headers=headers)
            response = connection.getresponse()
            self.send_response(response.status)
            for name in ("Content-Type", "Cache-Control"):
                if value := response.getheader(name):
                    self.send_header(name, value)
            self.send_header("Connection", "close")
            self.end_headers()
            while chunk := response.read(4096):
                self.wfile.write(chunk)
                self.wfile.flush()
        finally:
            connection.close()

    def log_message(self, format, *args):
        return


ThreadingHTTPServer(("0.0.0.0", 8081), Handler).serve_forever()

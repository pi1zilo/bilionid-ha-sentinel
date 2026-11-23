"""Expose only the Docker API requests needed to populate the bot's container list."""

import http.client
import re
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


SOCKET_PATH = "/run/docker.sock"
CONTAINER_LIST = re.compile(r"/v[0-9]+\.[0-9]+/containers/json")


class DockerConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(SOCKET_PATH)


class DockerMonitorHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.partition("?")[0]
        if path != "/version" and not CONTAINER_LIST.fullmatch(path):
            self.send_error(404)
            return
        upstream_path = path if path == "/version" else f"{path}?all=1"

        connection = DockerConnection("localhost", timeout=4)
        try:
            connection.request("GET", upstream_path)
            response = connection.getresponse()
            status = response.status
            body = response.read()
        except (OSError, http.client.HTTPException):
            self.send_error(502, "Docker Engine unavailable")
            return
        finally:
            connection.close()

        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 2375), DockerMonitorHandler).serve_forever()

from contextlib import contextmanager
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
from threading import Thread
from unittest.mock import Mock, call

from app.monitoring.docker import proxy
from app.monitoring.docker.client import DockerMonitor


@contextmanager
def monitor_proxy():
    with ThreadingHTTPServer(("127.0.0.1", 0), proxy.DockerMonitorHandler) as server:
        worker = Thread(target=server.serve_forever)
        worker.start()
        try:
            yield server.server_port
        finally:
            server.shutdown()
            worker.join()


def test_monitor_lists_containers_through_proxy(monkeypatch):
    upstream = Mock()
    upstream.getresponse.side_effect = [
        Mock(status=200, read=Mock(return_value=b'{"ApiVersion":"1.53"}')),
        Mock(status=200, read=Mock(return_value=json.dumps([{
            "Id": "0123456789abcdef", "Names": ["/nginx"], "Image": "nginx:alpine",
            "State": "running", "Status": "Up 2 hours (healthy)",
        }]).encode())),
    ]
    monkeypatch.setattr(proxy, "DockerConnection", Mock(return_value=upstream))

    with monitor_proxy() as port:
        monitor = DockerMonitor(proxy_url=f"http://127.0.0.1:{port}")
        [container] = monitor._list_containers_sync()

    assert upstream.request.call_args_list == [
        call("GET", "/version"), call("GET", "/v1.53/containers/json?all=1"),
    ]
    assert container.name == "nginx"
    assert container.image == "nginx:alpine"
    assert container.state == "running"
    assert container.health == "healthy"


def test_proxy_rejects_other_docker_requests(monkeypatch):
    upstream = Mock()
    monkeypatch.setattr(proxy, "DockerConnection", upstream)

    with monitor_proxy() as port:
        connection = HTTPConnection("127.0.0.1", port, timeout=2)
        try:
            for method, path, status in (
                ("GET", "/v1.53/containers/123/json", 404),
                ("GET", "/v1.53/containers/123/archive", 404),
                ("POST", "/v1.53/containers/json", 501),
            ):
                connection.request(method, path)
                response = connection.getresponse()
                assert response.status == status
                response.read()
        finally:
            connection.close()

    upstream.assert_not_called()

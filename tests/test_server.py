"""HTTP smoke tests using only temporary, synthetic agent state (MIT license)."""

import http.client
import importlib.util
import json
import os
from pathlib import Path
import socketserver
import subprocess
import sys
import threading

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def office(tmp_path, monkeypatch):
    state = tmp_path / "agents.json"
    state.write_text('[{"id": "demo-test", "name": "Demo Test"}]', encoding="utf-8")
    monkeypatch.setenv("AGENTS_JSON_PATH", str(state))
    monkeypatch.setenv("HERMES_AGENT_API", "")
    monkeypatch.setenv("ENABLE_SSE", "true")
    monkeypatch.setenv("POLL_INTERVAL_SECONDS", "5")
    spec = importlib.util.spec_from_file_location("isolated_server", ROOT / "server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.refresh_agents()
    with socketserver.ThreadingTCPServer(("127.0.0.1", 0), module.Handler) as httpd:
        httpd.daemon_threads = True
        worker = threading.Thread(target=httpd.serve_forever, daemon=True)
        worker.start()
        yield module, httpd.server_address[1], state
        httpd.shutdown()
        worker.join(timeout=5)


def request(port, path, method="GET", body=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request(method, path, body, {"Content-Type": "application/json"})
        response = connection.getresponse()
        return response.status, response.getheader("Content-Type"), response.read()
    finally:
        connection.close()


@pytest.mark.parametrize("path,title", [
    ("/", "Offivo — 3D Virtual Agent Office"),
    ("/office.html", "Offivo — Living Office Floor Plan"),
])
def test_static_pages(office, path, title):
    status, content_type, body = request(office[1], path)
    assert status == 200
    assert content_type.startswith("text/html")
    assert f"<title>{title}</title>".encode() in body


def test_config_and_normalized_agents(office):
    status, content_type, body = request(office[1], "/api/config")
    assert status == 200
    assert content_type == "application/json"
    assert json.loads(body) == {
        "mode": "json_file", "poll_interval_seconds": 5,
        "enable_sse": True, "hermes_agent_api": None,
    }
    agents = json.loads(request(office[1], "/api/agents")[2])
    assert len(agents) == 1
    assert agents[0]["id"] == "demo-test"
    assert agents[0]["status"] == "idle"


@pytest.mark.parametrize("wrapped", [False, True])
def test_webhook_updates_get_and_sse(office, wrapped):
    connection = http.client.HTTPConnection("127.0.0.1", office[1], timeout=5)
    try:
        connection.request("GET", "/events")
        stream = connection.getresponse()
        assert stream.status == 200
        assert stream.getheader("Content-Type") == "text/event-stream"
        assert json.loads(stream.readline()[6:])["agents"][0]["id"] == "demo-test"
        assert stream.readline() == b"\n"
        agents = [{"id": "pushed-demo", "status": "working"}]
        payload = {"agents": agents} if wrapped else agents
        status, _, body = request(office[1], "/webhook/agents", "POST", json.dumps(payload))
        assert status == 200
        assert json.loads(body) == {"ok": True, "count": 1}
        current = json.loads(request(office[1], "/api/agents")[2])
        assert current[0]["id"] == "pushed-demo"
        assert json.loads(stream.readline()[6:])["agents"] == current
    finally:
        connection.close()


def test_bad_json_and_missing_route(office):
    status, _, body = request(office[1], "/webhook/agents", "POST", "{")
    assert status == 400
    assert json.loads(body)["ok"] is False
    assert json.loads(request(office[1], "/api/agents")[2])[0]["id"] == "demo-test"
    assert request(office[1], "/missing")[0] == 404


def test_empty_file_is_empty_state(office):
    office[2].write_text("[]", encoding="utf-8")
    office[0].refresh_agents()
    assert json.loads(request(office[1], "/api/agents")[2]) == []


def test_missing_file_uses_demo(office):
    office[2].unlink()
    office[0].refresh_agents()
    assert json.loads(request(office[1], "/api/config")[2])["mode"] == "demo"
    assert len(json.loads(request(office[1], "/api/agents")[2])) == 5


def test_readme_startup(tmp_path):
    # Run the real entry point with a clean environment and no host agent data.
    env = {key: value for key, value in os.environ.items() if not key.startswith(
        ("HERMES_", "AGENTS_", "POLL_", "ENABLE_SSE", "HOST", "PORT")
    )}
    env.update(HOST="127.0.0.1", PORT="0", AGENTS_JSON_PATH=str(tmp_path / "missing.json"))
    process = subprocess.Popen(
        [sys.executable, "-u", str(ROOT / "server.py")], cwd=tmp_path,
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        # Bound the startup wait even if the child never prints a URL.
        import queue
        lines = queue.Queue()
        threading.Thread(target=lambda: lines.put(process.stdout.readline()), daemon=True).start()
        line = lines.get(timeout=10)
        port = int(line.strip().rsplit(":", 1)[1])
        assert port > 0
        assert request(port, "/api/config")[0] == 200
        assert request(port, "/office.html")[0] == 200
    finally:
        process.terminate()
        process.communicate(timeout=10)

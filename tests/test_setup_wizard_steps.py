"""tests.test_setup_wizard_steps — the checks behind the wizard's steps (setup v2), without the wizard's own packages:
Redis PING, the ChromaDB folder check, the start commands, writing config/memory.toml, and the live key/token tests."""
from __future__ import annotations

import json
import socket
import threading
import tomllib
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from setup import live_checks, memory_toml
from setup.service_steps import chroma_check, redis_ping, redis_start_commands

ROOT = Path(__file__).resolve().parent.parent


def _tcp_server(reply: bytes):
    """A one-connection TCP server that answers ``reply`` to whatever it gets; returns its port."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def serve():
        conn, _ = srv.accept()
        with conn:
            conn.recv(64)
            conn.sendall(reply)
        srv.close()
    threading.Thread(target=serve, daemon=True).start()
    return srv.getsockname()[1]


def test_redis_ping_needs_a_real_pong_and_explains_what_is_wrong():
    assert redis_ping(f"redis://127.0.0.1:{_tcp_server(b'+PONG' + bytes([13, 10]))}/0").ok
    nothing = redis_ping("redis://127.0.0.1:1/0")
    assert not nothing.ok and "nothing answers" in nothing.message and "Start Redis" in nothing.fix
    not_redis = redis_ping(f"redis://127.0.0.1:{_tcp_server(b'HTTP/1.1 400')}/0")
    assert not not_redis.ok and "not like Redis" in not_redis.message
    assert not redis_ping("http://localhost:6379").ok  # not a Redis address


def test_the_start_commands_put_what_is_installed_first():
    linux = redis_start_commands(lambda tool: "/usr/bin/apt-get" if tool == "apt-get" else None, platform="linux")
    assert linux[0].startswith("sudo apt-get install -y redis-server") and any(c.startswith("docker run") for c in linux)
    mac = redis_start_commands(lambda tool: "/x" if tool in ("docker", "brew") else None, platform="darwin")
    assert mac[0].startswith("docker run") and mac[1].startswith("brew install redis")


def test_chroma_is_checked_as_the_local_folders_it_is_not_a_server(tmp_path):
    ok = chroma_check(tmp_path / "data", tmp_path / "chunks")
    assert ok.ok and (tmp_path / "data").is_dir() and not list((tmp_path / "data").iterdir())  # probe cleaned up
    blocker = tmp_path / "file"
    blocker.write_text("x")
    bad = chroma_check(blocker / "data")  # its parent is a file: cannot be created
    assert not bad.ok and "cannot write" in bad.message
    missing = chroma_check(tmp_path / "d", find_spec=lambda name: None)
    assert not missing.ok and "pip install -r requirements.txt" in missing.fix


def test_the_wizard_writes_the_addresses_into_memory_toml_in_place_with_a_backup(tmp_path):
    copy = tmp_path / "memory.toml"
    original = (ROOT / "config" / "memory.toml").read_text()
    copy.write_text(original)
    assert memory_toml.read_values(copy)["redis_url"] == "redis://localhost:6379/0"
    backup = memory_toml.write_values({"redis_url": "redis://10.0.0.5:6380/1", "chroma_path": "/data/goat/chroma",
                                       "chroma_chunks_path": "/data/goat/chunks"}, copy)
    assert backup.read_text() == original
    new = copy.read_text()
    cfg = tomllib.loads(new)
    assert cfg["working"]["storage_url"] == "redis://10.0.0.5:6380/1"
    assert (cfg["episodic"]["storage_path"], cfg["episodic"]["chunk_storage_path"]) == ("/data/goat/chroma", "/data/goat/chunks")
    # nothing else moved: same number of lines, every other line identical
    old_lines, new_lines = original.splitlines(), new.splitlines()
    assert len(old_lines) == len(new_lines) and sum(a != b for a, b in zip(old_lines, new_lines)) == 3


class _Api(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/v1/models":
            ok = self.headers.get("Authorization") == "Bearer good-key"
            self._send(200 if ok else 401, {"data": []} if ok else {"error": "bad key"})
        elif self.path == "/botgood:token/getMe":
            self._send(200, {"ok": True, "result": {"username": "goat_test_bot", "first_name": "GOAT"}})
        else:
            self._send(401, {"ok": False})

    def _send(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


@pytest.fixture
def api():
    srv = HTTPServer(("127.0.0.1", 0), _Api)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_an_api_key_is_tested_live_and_a_refused_one_says_so(api):
    assert live_checks.check_api_key(f"{api}/v1", "good-key").ok
    bad = live_checks.check_api_key(f"{api}/v1", "wrong")
    assert not bad.ok and "does not answer" in bad.message and "re-enter" in bad.fix
    assert not live_checks.check_api_key(f"{api}/v1", "  ").ok
    down = live_checks.check_api_key("http://127.0.0.1:1/v1", "good-key")
    assert not down.ok and "could not be reached" in down.message


def test_the_telegram_token_is_tested_with_getme_and_names_the_bot(api):
    good = live_checks.check_telegram_token("good:token", api=api)
    assert good.ok and "@goat_test_bot" in good.message
    assert not live_checks.check_telegram_token("bad:token", api=api).ok
    assert not live_checks.check_telegram_token("no-colon", api=api).ok

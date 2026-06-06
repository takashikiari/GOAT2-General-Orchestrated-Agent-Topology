"""FileToolExecutor tests: workspace safety, sensitive-file blocking, atomic writes."""
from __future__ import annotations
import importlib
from pathlib import Path
import pytest


@pytest.fixture
def ws(tmp_path): return tmp_path


def _reload(ws, monkeypatch, **extra_env):
    """Reload executor modules with a fresh workspace env var.

    Both helpers (which owns _WS/MAX_*) and executor must be reloaded so
    module-level constants pick up the monkeypatched GOAT_WORKSPACE value.
    """
    monkeypatch.setenv("GOAT_WORKSPACE", str(ws))
    monkeypatch.setenv("GOAT_ALLOW_OUTSIDE_WORKSPACE", "false")
    for k, v in extra_env.items():
        monkeypatch.setenv(k, str(v))
    import tools.file_executor_helpers as h; importlib.reload(h)
    import tools.file_executor as m; importlib.reload(m)
    return m.FileToolExecutor()


@pytest.fixture
def ex(ws, monkeypatch):
    return _reload(ws, monkeypatch)


def _w(ws: Path, rel: str, body: str = "hi") -> Path:
    p = ws / rel; p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8"); return p


def _fresh(ws: Path, mp, **env):
    return _reload(ws, mp, **env)


def test_read_valid(ws, ex):
    _w(ws, "notes/a.txt", "hello world"); assert ex.read("notes/a.txt") == "hello world"
def test_read_rejects_dotdot(ws, ex): assert ex.read("../out.txt").startswith("ERROR")
def test_read_rejects_absolute(ws, ex): assert ex.read("/etc/passwd").startswith("ERROR")
def test_read_rejects_sensitive_name(ws, ex):
    _w(ws, ".env", "S=x"); assert ex.read(".env").startswith("ERROR")
def test_read_rejects_sensitive_ext(ws, ex):
    _w(ws, "k.pem", "K"); assert ex.read("k.pem").startswith("ERROR")
def test_read_rejects_pycache(ws, ex):
    _w(ws, "__pycache__/c.pyc", ""); assert ex.read("__pycache__/c.pyc").startswith("ERROR")
def test_read_rejects_oversized(ws, monkeypatch):
    e = _fresh(ws, monkeypatch, FILE_READ_MAX_BYTES=10)
    (ws / "big.txt").write_bytes(b"x" * 100); assert e.read("big.txt").startswith("ERROR")
def test_read_rejects_symlink_escape(ws, ex):
    out = ws.parent / "_sym_secret.txt"; out.write_text("secret")
    (ws / "link.txt").symlink_to(out)
    try: assert ex.read("link.txt").startswith("ERROR")
    finally: out.unlink(missing_ok=True)


def test_write_valid(ws, ex):
    assert ex.write("out.txt", "world").startswith("OK")
    assert (ws / "out.txt").read_text() == "world"
def test_write_creates_parents(ws, ex):
    assert ex.write("a/b/c.txt", "data").startswith("OK"); assert (ws / "a/b/c.txt").exists()
def test_write_rejects_dotdot(ws, ex): assert ex.write("../esc.txt", "x").startswith("ERROR")
def test_write_rejects_absolute(ws, ex): assert ex.write("/tmp/evil.txt", "x").startswith("ERROR")
def test_write_rejects_sensitive(ws, ex): assert ex.write(".env", "x=1").startswith("ERROR")
def test_write_rejects_oversized(ws, monkeypatch):
    e = _fresh(ws, monkeypatch, FILE_WRITE_MAX_BYTES=5)
    assert e.write("out.txt", "x" * 100).startswith("ERROR")
def test_atomic_write(ws, ex):
    ex.write("a.txt", "complete")
    assert (ws / "a.txt").read_text() == "complete"; assert not list(ws.glob("*.tmp"))


def test_list_valid(ws, ex):
    _w(ws, "d/a.txt"); _w(ws, "d/b.txt")
    out = ex.list_dir("d"); assert "a.txt" in out and "b.txt" in out
def test_list_marks_dirs(ws, ex):
    (ws / "sub").mkdir(); out = ex.list_dir(".")
    assert any(l.startswith("d ") and "sub" in l for l in out.splitlines())
def test_list_rejects_dotdot(ws, ex): assert ex.list_dir("../").startswith("ERROR")
def test_list_rejects_absolute(ws, ex): assert ex.list_dir("/tmp").startswith("ERROR")

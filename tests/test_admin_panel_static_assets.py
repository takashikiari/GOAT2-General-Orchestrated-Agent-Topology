"""tests.test_admin_panel_static_assets — /assets/* serves the built Vite bundle."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from admin_panel.server import create_app

_ASSETS_DIR = Path(__file__).parent.parent / "admin_panel" / "static" / "assets"


class _FakeRegistry:
    pass


def test_assets_directory_serves_built_files():
    asset_files = [f for f in _ASSETS_DIR.glob("*") if f.is_file()]
    assert asset_files, "expected admin_panel/static/assets/ to contain a built Vite bundle (run npm run build first)"
    client = TestClient(create_app(_FakeRegistry()))
    resp = client.get(f"/assets/{asset_files[0].name}")
    assert resp.status_code == 200


def test_assets_missing_file_returns_404():
    client = TestClient(create_app(_FakeRegistry()))
    assert client.get("/assets/does-not-exist.js").status_code == 404

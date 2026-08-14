"""tests.test_admin_panel_static_assets — /assets/* serves the built Vite bundle."""
from __future__ import annotations

import re
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


def test_bundle_does_not_leak_dev_auth_fallback():
    """The VITE_DEV_INIT_DATA dev-mode auth fallback must never ship in the
    production bundle. It's currently kept out only by esbuild's dead-code
    elimination around `import.meta.env.DEV` — this is a regression test for
    that, scanning the actual built assets rather than trusting the build
    config.
    """
    asset_files = [f for f in _ASSETS_DIR.glob("*") if f.is_file()]
    assert asset_files, "expected admin_panel/static/assets/ to contain a built Vite bundle (run npm run build first)"

    hash_pattern = re.compile(r"hash=[0-9a-f]{64}")

    for asset_file in asset_files:
        content = asset_file.read_text(encoding="utf-8", errors="ignore")
        assert "auth_date=" not in content, f"{asset_file.name} leaks a Telegram auth_date literal"
        assert "VITE_DEV_INIT_DATA" not in content, f"{asset_file.name} leaks the VITE_DEV_INIT_DATA env var name"
        assert not hash_pattern.search(content), f"{asset_file.name} leaks a hash=<64-hex> Telegram init-data value"

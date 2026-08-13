"""tests.test_tunnel_extract_url — admin_panel.tunnel.extract_tunnel_url."""
from __future__ import annotations

from admin_panel.tunnel import extract_tunnel_url


def test_extracts_url_from_boxed_cloudflared_output():
    line = "|  https://random-example-words-1234.trycloudflare.com  |"
    assert extract_tunnel_url(line) == "https://random-example-words-1234.trycloudflare.com"


def test_extracts_url_from_plain_log_line():
    line = "2024-01-15T10:23:45Z INF https://foo-bar-baz.trycloudflare.com"
    assert extract_tunnel_url(line) == "https://foo-bar-baz.trycloudflare.com"


def test_returns_none_when_no_url_present():
    assert extract_tunnel_url("2024-01-15T10:23:45Z INF Starting tunnel") is None


def test_ignores_non_trycloudflare_https_urls():
    assert extract_tunnel_url("see https://example.com for docs") is None

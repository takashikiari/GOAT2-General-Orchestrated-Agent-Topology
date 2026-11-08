"""browse_page: wait for the DOM, settle best-effort, always close the browser (2026-11-08 Deepgram timeout)."""
from __future__ import annotations

import asyncio
import sys
import types

from tools.goat_skills import browse_page as bp


class _Page:
    def __init__(self, log, settle_raises=False, goto_raises=False):
        self.log, self.settle_raises, self.goto_raises = log, settle_raises, goto_raises

    async def goto(self, url, timeout, wait_until):
        self.log.append(("goto", wait_until))
        if self.goto_raises:
            raise TimeoutError("Page.goto: Timeout")

    async def wait_for_load_state(self, state, timeout):
        self.log.append(("settle", state, timeout))
        if self.settle_raises:
            raise TimeoutError("still busy")

    async def screenshot(self, path, full_page):
        self.log.append(("screenshot",))

    async def inner_text(self, sel):
        return "page text"


class _Browser:
    def __init__(self, log, page):
        self.log, self.page = log, page

    async def new_page(self):
        return self.page

    async def close(self):
        self.log.append(("close",))


def _fake_playwright(monkeypatch, **page_kw):
    log = []
    page = _Page(log, **page_kw)

    class _Ctx:
        async def __aenter__(self):
            async def launch(headless):
                return _Browser(log, page)
            return types.SimpleNamespace(chromium=types.SimpleNamespace(launch=launch))

        async def __aexit__(self, *a):
            return False

    mod = types.ModuleType("playwright.async_api")
    mod.async_playwright = lambda: _Ctx()
    monkeypatch.setitem(sys.modules, "playwright.async_api", mod)
    return log


def _run(**kw):
    return asyncio.run(bp.build(object())[0].handler(url="https://x.test", **kw))


def test_waits_for_dom_not_networkidle(monkeypatch):
    log = _fake_playwright(monkeypatch)
    assert _run() == "page text"
    assert ("goto", "domcontentloaded") in log
    assert ("settle", "networkidle", bp._SETTLE_MS) in log and log[-1] == ("close",)


def test_busy_network_after_settle_window_still_returns_text(monkeypatch):
    log = _fake_playwright(monkeypatch, settle_raises=True)
    assert _run(max_chars=4) == "page"
    assert ("screenshot",) in log and log[-1] == ("close",)


def test_browser_closed_even_when_navigation_fails(monkeypatch):
    log = _fake_playwright(monkeypatch, goto_raises=True)
    out = _run()
    assert out.startswith("(error browsing https://x.test") and ("close",) in log

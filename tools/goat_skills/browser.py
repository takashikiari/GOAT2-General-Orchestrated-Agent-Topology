"""Browser automation using Selenium WebDriver.

Provides a ``BrowserAutomation`` class with methods for:
  - Opening URLs
  - Clicking elements by CSS selector
  - Typing text into elements
  - Retrieving page source

Also exposes the original GOAT-only tools (BROWSER_OPEN, APP_LIST, APP_FOCUS)
for backward compatibility with the supervisor's direct_response().

Dependencies (optional, graceful fallback):
  - selenium (for BrowserAutomation)
  - webdriver-manager (auto-manages chromedriver/geckodriver)
  - psutil (for APP_LIST)
  - xdg-open, wmctrl, xdotool (binaries for browser/focus)
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from typing import TYPE_CHECKING, Any

from tools._make_tool import make_tool
from tools.goat_skills._common import safe_import

if TYPE_CHECKING:
    from agents.base_agent import ToolDefinition

log = logging.getLogger("goat2.tools.goat_skills.browser")

__all__ = [
    "BrowserAutomation",
    "BROWSER_OPEN",
    "APP_LIST",
    "APP_FOCUS",
]

# ── Lazy-loaded optional dependencies ──────────────────────────────────

_SELENIUM = safe_import("selenium.webdriver")
_WEBDRIVER_MANAGER = safe_import("webdriver_manager.chrome")
_PSUTIL = safe_import("psutil")

# ── URL validation (shared with BROWSER_OPEN) ──────────────────────────

_ALLOWED_URL_SCHEMES: tuple[str, ...] = ("http://", "https://", "file://")


def _validate_url(url: str) -> str | None:
    """Return None if ``url`` is acceptable, else an error message."""
    if not isinstance(url, str) or not url.strip():
        return "ERROR: empty url"
    if not url.lower().startswith(_ALLOWED_URL_SCHEMES):
        return (
            f"ERROR: url must start with one of "
            f"{', '.join(_ALLOWED_URL_SCHEMES)} (got {url[:32]!r})"
        )
    return None


# ═══════════════════════════════════════════════════════════════════════
#  BrowserAutomation class
# ═══════════════════════════════════════════════════════════════════════


class BrowserAutomation:
    """A Selenium-based browser automation wrapper.

    Manages a single WebDriver instance.  The driver is started lazily on
    the first method call that needs it, or explicitly via ``start()``.

    Usage::

        browser = BrowserAutomation(browser="chrome")
        browser.open_url("https://example.com")
        browser.type_into("#search", "hello world")
        browser.click_element("#search-button")
        source = browser.get_page_source()
        browser.quit()

    Args:
        browser: ``"chrome"`` (default) or ``"firefox"``.
        headless: If True, run in headless mode (no visible window).
        implicit_wait: Seconds for Selenium's implicit wait (default 10).
    """

    def __init__(
        self,
        browser: str = "chrome",
        headless: bool = False,
        implicit_wait: int = 10,
    ) -> None:
        self._browser_name = browser.lower().strip()
        self._headless = headless
        self._implicit_wait = implicit_wait
        self._driver: Any = None  # WebDriver instance

    # ── Public helpers ────────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        """True if the driver has been started and is still alive."""
        if self._driver is None:
            return False
        try:
            # A lightweight no-op to check the session is alive.
            self._driver.current_url  # noqa: B018
            return True
        except Exception:
            return False

    def start(self) -> str:
        """Start the WebDriver explicitly.

        Returns a status message.  Safe to call multiple times — if the
        driver is already running it returns immediately.
        """
        if self.is_running:
            return "BrowserAutomation: driver already running"

        if _SELENIUM is None:
            return "ERROR: selenium is not installed — cannot start browser"

        try:
            if self._browser_name == "firefox":
                self._driver = self._start_firefox()
            else:
                self._driver = self._start_chrome()

            self._driver.implicitly_wait(self._implicit_wait)
            log.info(
                "BrowserAutomation: started %s (headless=%s)",
                self._browser_name,
                self._headless,
            )
            return (
                f"BrowserAutomation: {self._browser_name} driver started "
                f"(headless={self._headless})"
            )
        except Exception as exc:
            log.error("BrowserAutomation: failed to start driver: %s", exc)
            return f"ERROR: could not start {self._browser_name} driver: {exc}"

    def quit(self) -> str:
        """Close the browser and stop the driver.

        Returns a status message.  Safe to call even if not running.
        """
        if self._driver is None:
            return "BrowserAutomation: no driver to quit"
        try:
            self._driver.quit()
        except Exception as exc:
            log.warning("BrowserAutomation: error during quit: %s", exc)
        self._driver = None
        return "BrowserAutomation: driver quit"

    # ── Core automation methods ───────────────────────────────────────

    def open_url(self, url: str) -> str:
        """Navigate to ``url``.

        Args:
            url: A ``http://``, ``https://``, or ``file://`` URL.

        Returns:
            ``"opened: <url>"`` on success, or an ``ERROR:`` string.
        """
        err = _validate_url(url)
        if err is not None:
            return err

        status = self.start()
        if status.startswith("ERROR"):
            return status

        try:
            self._driver.get(url)
            log.info("BrowserAutomation: navigated to %s", url)
            return f"opened: {url}"
        except Exception as exc:
            log.error("BrowserAutomation: open_url failed: %s", exc)
            return f"ERROR: open_url failed: {exc}"

    def click_element(self, selector: str) -> str:
        """Click the first element matching a CSS selector.

        Args:
            selector: A CSS selector string (e.g. ``"#submit-btn"``,
                      ``".btn-primary"``, ``"button[type=submit]"``).

        Returns:
            ``"clicked: <selector>"`` on success, or an ``ERROR:`` string.
        """
        if not selector or not isinstance(selector, str):
            return "ERROR: selector must be a non-empty string"

        status = self.start()
        if status.startswith("ERROR"):
            return status

        try:
            element = self._driver.find_element("css selector", selector)
            element.click()
            log.info("BrowserAutomation: clicked %s", selector)
            return f"clicked: {selector}"
        except Exception as exc:
            log.error("BrowserAutomation: click_element failed: %s", exc)
            return f"ERROR: click_element failed for {selector!r}: {exc}"

    def type_into(self, selector: str, text: str) -> str:
        """Clear an element and type text into it.

        Args:
            selector: A CSS selector for the target element.
            text: The text to type.

        Returns:
            ``"typed into: <selector>"`` on success, or an ``ERROR:`` string.
        """
        if not selector or not isinstance(selector, str):
            return "ERROR: selector must be a non-empty string"
        if not isinstance(text, str):
            return "ERROR: text must be a string"

        status = self.start()
        if status.startswith("ERROR"):
            return status

        try:
            element = self._driver.find_element("css selector", selector)
            element.clear()
            element.send_keys(text)
            log.info("BrowserAutomation: typed %d chars into %s", len(text), selector)
            return f"typed into: {selector}"
        except Exception as exc:
            log.error("BrowserAutomation: type_into failed: %s", exc)
            return f"ERROR: type_into failed for {selector!r}: {exc}"

    def get_page_source(self) -> str:
        """Return the current page HTML source.

        Returns:
            The full page source as a string, or an ``ERROR:`` string.
        """
        if not self.is_running:
            return "ERROR: browser is not running — call open_url() first"

        try:
            source = self._driver.page_source
            log.info("BrowserAutomation: retrieved page source (%d chars)", len(source))
            return source
        except Exception as exc:
            log.error("BrowserAutomation: get_page_source failed: %s", exc)
            return f"ERROR: get_page_source failed: {exc}"

    # ── Internal driver factories ─────────────────────────────────────

    def _start_chrome(self) -> Any:
        """Start a Chrome/Chromium WebDriver."""
        options = _SELENIUM.ChromeOptions()
        if self._headless:
            options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1280,720")

        if _WEBDRIVER_MANAGER is not None:
            from webdriver_manager.chrome import ChromeDriverManager  # type: ignore[import-untyped]

            service = _SELENIUM.ChromeService(
                ChromeDriverManager().install(),
            )
            return _SELENIUM.Chrome(service=service, options=options)

        # Fallback: assume chromedriver is on PATH.
        return _SELENIUM.Chrome(options=options)

    def _start_firefox(self) -> Any:
        """Start a Firefox GeckoDriver."""
        options = _SELENIUM.FirefoxOptions()
        if self._headless:
            options.add_argument("--headless")

        if _WEBDRIVER_MANAGER is not None:
            from webdriver_manager.firefox import GeckoDriverManager  # type: ignore[import-untyped]

            service = _SELENIUM.FirefoxService(
                GeckoDriverManager().install(),
            )
            return _SELENIUM.Firefox(service=service, options=options)

        return _SELENIUM.Firefox(options=options)


# ═══════════════════════════════════════════════════════════════════════
#  Original GOAT-only tools (preserved for backward compatibility)
# ═══════════════════════════════════════════════════════════════════════

# ── TOOL 8: browser_open(url) ───────────────────────────────────────────

async def _browser_open_handler(url: str) -> str:
    """Open ``url`` in the system default browser via ``xdg-open``.

    Args:
        url: A ``http://``, ``https://``, or ``file://`` URL.

    Returns:
        ``"opened: <url>"`` on success, or an ``ERROR:`` string.
    """
    err = _validate_url(url)
    if err is not None:
        log.warning("browser_open: rejected url=%r", url[:64])
        return err
    if shutil.which("xdg-open") is None:
        log.warning("browser_open: xdg-open not on PATH")
        return "ERROR: xdg-open not found on PATH — cannot open browser"
    try:
        subprocess.Popen(  # noqa: S603 — args are validated above
            ["xdg-open", url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
    except Exception as exc:
        log.warning("browser_open: xdg-open failed for %r: %s", url[:64], exc)
        return f"ERROR: browser_open failed: {exc}"
    log.info("browser_open: opened %s", url)
    return f"opened: {url}"


BROWSER_OPEN = make_tool(
    name="browser_open",
    description=(
        "Open a URL in the system default browser via xdg-open. "
        "URL must start with http://, https://, or file://. "
        "Returns 'opened: <url>' on success. "
        "GOAT-only — not available to DAG agents."
    ),
    parameters={
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "URL to open (http://, https://, or file://).",
            },
        },
        "required": ["url"],
    },
    handler=_browser_open_handler,
)


# ── TOOL 11: app_list() ─────────────────────────────────────────────────

def _list_processes_psutil() -> str | None:
    """Build a process listing using psutil, or None if psutil is missing."""
    if _PSUTIL is None:
        return None
    try:
        names: set[str] = set()
        rows: list[tuple[str, int]] = []
        for proc in _PSUTIL.process_iter(attrs=["name", "pid"]):  # type: ignore[attr-defined]
            info = proc.info
            name = (info.get("name") or "").strip()
            pid = info.get("pid")
            if not name or pid is None:
                continue
            if name in names:
                continue
            names.add(name)
            rows.append((name, int(pid)))
        rows.sort(key=lambda r: r[0].lower())
        if not rows:
            return "(no processes found)"
        lines = [f"{name} (pid {pid})" for name, pid in rows]
        return "\n".join(lines)
    except Exception as exc:
        log.warning("app_list: psutil iter failed: %s", exc)
        return None


def _list_processes_ps_fallback() -> str:
    """POSIX ``ps -eo comm`` fallback when psutil is unavailable."""
    if shutil.which("ps") is None:
        return "ERROR: psutil not installed and 'ps' binary not found"
    try:
        result = subprocess.run(  # noqa: S603
            ["ps", "-eo", "comm"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception as exc:
        log.warning("app_list: ps fallback failed: %s", exc)
        return f"ERROR: app_list failed: {exc}"
    lines = [ln.strip() for ln in (result.stdout or "").splitlines() if ln.strip()]
    lines = lines[1:]  # drop the header "COMMAND"
    if not lines:
        return "(no processes found)"
    # De-dup but keep order.
    seen: set[str] = set()
    deduped: list[str] = []
    for name in lines:
        if name in seen:
            continue
        seen.add(name)
        deduped.append(name)
    return "\n".join(deduped)


async def _app_list_handler() -> str:
    """List running applications/processes.

    Prefers psutil; falls back to ``ps -eo comm`` when psutil is missing.
    """
    listing = _list_processes_psutil()
    if listing is not None:
        return listing
    log.debug("app_list: psutil unavailable, using ps fallback")
    return _list_processes_ps_fallback()


APP_LIST = make_tool(
    name="app_list",
    description=(
        "List running applications/processes. Uses psutil when available, "
        "falls back to 'ps -eo comm' otherwise. Returns a sorted, "
        "de-duplicated list of process names. "
        "GOAT-only — not available to DAG agents."
    ),
    parameters={
        "type": "object",
        "properties": {},
        "required": [],
    },
    handler=_app_list_handler,
)


# ── TOOL 12: app_focus(name) ────────────────────────────────────────────

def _focus_with_wmctrl(name: str) -> str | None:
    """Try ``wmctrl -a <name>``; return None if the binary is missing."""
    if shutil.which("wmctrl") is None:
        return None
    try:
        result = subprocess.run(  # noqa: S603
            ["wmctrl", "-a", name],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception as exc:
        log.warning("app_focus: wmctrl raised: %s", exc)
        return None
    if result.returncode == 0:
        return f"focused: {name}"
    return f"ERROR: wmctrl could not find window {name!r} (rc={result.returncode})"


def _focus_with_xdotool(name: str) -> str | None:
    """Try ``xdotool search --name <name> windowactivate``; None if missing."""
    if shutil.which("xdotool") is None:
        return None
    try:
        result = subprocess.run(  # noqa: S603
            ["xdotool", "search", "--name", name, "windowactivate"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception as exc:
        log.warning("app_focus: xdotool raised: %s", exc)
        return None
    if result.returncode == 0:
        return f"focused: {name}"
    return f"ERROR: xdotool could not focus window {name!r} (rc={result.returncode})"


async def _app_focus_handler(name: str) -> str:
    """Bring a named application window to the foreground.

    Tries ``wmctrl -a <name>`` first, then ``xdotool search --name
    <name> windowactivate``. If neither binary is on PATH, returns a
    single ``ERROR:`` string — never raises.
    """
    if not isinstance(name, str) or not name.strip():
        log.warning("app_focus: empty name")
        return "ERROR: empty name"
    wm = _focus_with_wmctrl(name)
    if wm is not None:
        log.info("app_focus: wmctrl focused %s", name)
        return wm
    log.debug("app_focus: wmctrl unavailable/failed, trying xdotool")
    xd = _focus_with_xdotool(name)
    if xd is not None:
        log.info("app_focus: xdotool focused %s", name)
        return xd
    return (
        "ERROR: neither wmctrl nor xdotool is on PATH — cannot focus a window"
    )


APP_FOCUS = make_tool(
    name="app_focus",
    description=(
        "Bring an application window to the foreground. Tries wmctrl first, "
        "then xdotool. Returns 'focused: <name>' on success. "
        "GOAT-only — not available to DAG agents."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Substring of the window title to focus.",
            },
        },
        "required": ["name"],
    },
    handler=_app_focus_handler,
)


# ═══════════════════════════════════════════════════════════════════════
#  Main test (runs when the file is executed directly)
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s [%(name)s] %(message)s",
        stream=sys.stdout,
    )

    print("=" * 60)
    print("BrowserAutomation — self-test")
    print("=" * 60)

    # 1. Test without selenium installed (graceful fallback)
    print("\n--- Test 1: Instantiate BrowserAutomation ---")
    browser = BrowserAutomation(browser="chrome", headless=True)
    print(f"  is_running (before start): {browser.is_running}")

    # 2. Try opening a URL (will start the driver)
    print("\n--- Test 2: open_url ---")
    result = browser.open_url("https://example.com")
    print(f"  Result: {result}")

    if result.startswith("ERROR"):
        print("\n  Selenium not available — skipping remaining tests.")
        print("  Install with: pip install selenium webdriver-manager")
        sys.exit(0)

    # 3. Get page source
    print("\n--- Test 3: get_page_source ---")
    source = browser.get_page_source()
    if source.startswith("ERROR"):
        print(f"  {source}")
    else:
        print(f"  Page source length: {len(source)} chars")
        print(f"  Contains '<html>': {'<html>' in source.lower()}")
        print(f"  Contains 'Example Domain': {'Example Domain' in source}")

    # 4. Test click_element (on a non-existent element to verify error handling)
    print("\n--- Test 4: click_element (error case) ---")
    result = browser.click_element("#nonexistent-element-xyz")
    print(f"  Result: {result}")

    # 5. Test type_into (error case)
    print("\n--- Test 5: type_into (error case) ---")
    result = browser.type_into("#nonexistent-input", "hello")
    print(f"  Result: {result}")

    # 6. Test invalid URL
    print("\n--- Test 6: open_url with invalid URL ---")
    result = browser.open_url("ftp://bad-scheme.com")
    print(f"  Result: {result}")

    # 7. Quit
    print("\n--- Test 7: quit ---")
    result = browser.quit()
    print(f"  Result: {result}")
    print(f"  is_running (after quit): {browser.is_running}")

    print("\n" + "=" * 60)
    print("Self-test complete.")
    print("=" * 60)

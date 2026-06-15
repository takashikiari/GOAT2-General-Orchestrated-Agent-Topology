"""Screen capture ToolDefinitions — TOOLS 1, 2 (goat_skills).

Wraps the ScreenCapture utility class (in screen.py) with two
ToolDefinitions: SCREEN_CAPTURE (full screen + OCR) and
SCREEN_READ_REGION (cropped region + OCR).

LAZY INSTANTIATION:
===================
The ScreenCapture class is constructed inside each handler, not at
import time. pyautogui (one of its backends) reads $DISPLAY at import
time and raises if no X display is available — we want a clean
``ERROR:`` string in that case, not a hard import failure.

GOAT-ONLY: wired only into direct_response(). DAG agents don't see these.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from tools._make_tool import make_tool

if TYPE_CHECKING:
    from agents.base_agent import ToolDefinition

log = logging.getLogger("goat2.tools.goat_skills.screen_tools")

__all__ = ["SCREEN_CAPTURE", "SCREEN_READ_REGION"]

# Default output path for the full-screen capture.
_DEFAULT_SCREENSHOT_PATH: str = "/tmp/goat_screenshot.png"


def _make_screen_capture() -> Any:
    """Construct a ScreenCapture; return the instance."""
    from tools.goat_skills.screen import ScreenCapture

    return ScreenCapture()


# ── TOOL 1: screen_capture() ────────────────────────────────────────────

async def _screen_capture_handler() -> str:
    """Capture the full screen, OCR it, return the recognized text.

    Falls back to a path string if OCR is unavailable, or to an
    ``ERROR:`` string if the screenshot itself fails.
    """
    try:
        sc = _make_screen_capture()
    except Exception as exc:  # noqa: BLE001
        log.warning("screen_capture: ScreenCapture() failed: %s", exc)
        return f"ERROR: screen_capture init failed: {exc}"
    try:
        path = sc.capture(_DEFAULT_SCREENSHOT_PATH)
    except Exception as exc:  # noqa: BLE001
        log.warning("screen_capture: capture raised: %s", exc)
        return f"ERROR: screen_capture failed: {exc}"
    # Optional OCR pass — do not require pytesseract.
    try:
        import pytesseract  # type: ignore[import-untyped]

        text = pytesseract.image_to_string(path)  # type: ignore[attr-defined]
        text = (text or "").strip()
        if text:
            return text
        return f"screenshot saved to {path} (no text detected)"
    except ImportError:
        return f"screenshot saved to {path}"
    except Exception as exc:  # noqa: BLE001
        log.warning("screen_capture: OCR failed: %s", exc)
        return f"screenshot saved to {path} (OCR unavailable: {exc})"


SCREEN_CAPTURE = make_tool(
    name="screen_capture",
    description=(
        "Capture the full screen, OCR it, return the recognized text. "
        "Saves a PNG to /tmp/goat_screenshot.png. Returns 'screenshot "
        "saved to /tmp/goat_screenshot.png' when OCR is unavailable. "
        "GOAT-only — not available to DAG agents."
    ),
    parameters={"type": "object", "properties": {}, "required": []},
    handler=_screen_capture_handler,
)


# ── TOOL 2: screen_read_region(x, y, width, height) ────────────────────

async def _screen_read_region_handler(
    x: int, y: int, width: int, height: int,
) -> str:
    """Capture a rectangular region and OCR it.

    Args:
        x: Top-left X coordinate in screen pixels.
        y: Top-left Y coordinate in screen pixels.
        width: Region width in pixels (must be > 0).
        height: Region height in pixels (must be > 0).
    """
    if width <= 0 or height <= 0:
        log.warning("screen_read_region: invalid size w=%d h=%d", width, height)
        return f"ERROR: width and height must be positive (got {width}x{height})"
    path = f"/tmp/goat_region_{x}_{y}_{width}_{height}.png"
    # Try pyautogui.screenshot(region=...) first (more efficient), then
    # fall back to ScreenCapture + PIL crop.
    saved = False
    try:
        import pyautogui  # type: ignore[import-untyped]

        img = pyautogui.screenshot(region=(int(x), int(y), int(width), int(height)))
        img.save(path, "PNG")
        saved = True
    except ImportError:
        pass
    except Exception as exc:  # noqa: BLE001
        log.debug("screen_read_region: pyautogui.region failed: %s", exc)
    if not saved:
        try:
            sc = _make_screen_capture()
            full = sc.capture("/tmp/goat_screenshot.png")
            from PIL import Image  # type: ignore[import-untyped]

            with Image.open(full) as im:
                im.crop((int(x), int(y), int(x) + int(width), int(y) + int(height))).save(
                    path, "PNG",
                )
            saved = True
        except Exception as exc:  # noqa: BLE001
            log.warning("screen_read_region: capture failed: %s", exc)
            return f"ERROR: screen_read_region failed: {exc}"
    try:
        import pytesseract  # type: ignore[import-untyped]

        text = pytesseract.image_to_string(path)  # type: ignore[attr-defined]
        text = (text or "").strip()
        if text:
            return text
        return f"screenshot saved to {path} (no text detected)"
    except ImportError:
        return f"screenshot saved to {path}"
    except Exception as exc:  # noqa: BLE001
        log.warning("screen_read_region: OCR failed: %s", exc)
        return f"screenshot saved to {path} (OCR unavailable: {exc})"


SCREEN_READ_REGION = make_tool(
    name="screen_read_region",
    description=(
        "Capture a rectangular region of the screen and OCR it. "
        "(x, y) is the top-left corner; width and height in pixels. "
        "Returns the recognized text, or 'screenshot saved to <path>' "
        "when OCR is unavailable. GOAT-only — not available to DAG agents."
    ),
    parameters={
        "type": "object",
        "properties": {
            "x": {"type": "integer", "description": "Top-left X (pixels)."},
            "y": {"type": "integer", "description": "Top-left Y (pixels)."},
            "width": {"type": "integer", "description": "Width (pixels, > 0)."},
            "height": {"type": "integer", "description": "Height (pixels, > 0)."},
        },
        "required": ["x", "y", "width", "height"],
    },
    handler=_screen_read_region_handler,
)

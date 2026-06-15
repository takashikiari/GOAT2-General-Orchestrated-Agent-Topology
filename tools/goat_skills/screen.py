"""Screen capture utility — ScreenCapture class.

Provides a ScreenCapture class with a capture(filename) method that takes
a screenshot using pyautogui (primary) or mss (fallback). Includes a main
function for testing.
"""
from __future__ import annotations

import logging
import os
import sys
from typing import Optional

log = logging.getLogger("goat2.tools.goat_skills.screen")

__all__ = ["ScreenCapture"]


class ScreenCapture:
    """Capture screenshots using pyautogui (primary) or mss (fallback).

    Usage:
        sc = ScreenCapture()
        sc.capture("screenshot.png")
    """

    def __init__(self) -> None:
        self._backend: str | None = None  # 'pyautogui', 'mss', or None

    def capture(self, filename: str) -> str:
        """Take a screenshot and save it to ``filename``.

        Uses pyautogui as the primary backend. Falls back to mss if
        pyautogui is not available. Returns the absolute path of the
        saved file on success, or raises an ImportError / RuntimeError
        on failure.

        Args:
            filename: Path where the screenshot PNG will be saved.
                      Parent directories are created automatically.

        Returns:
            Absolute path to the saved screenshot file.

        Raises:
            ImportError: If neither pyautogui nor mss is installed.
            RuntimeError: If the screenshot capture fails.
        """
        # Try pyautogui first
        if self._backend is None or self._backend == "pyautogui":
            try:
                return self._capture_pyautogui(filename)
            except ImportError:
                log.debug("pyautogui not available, trying mss")
                self._backend = "mss"
            except Exception as exc:
                raise RuntimeError(f"pyautogui screenshot failed: {exc}") from exc

        # Fallback to mss
        if self._backend is None or self._backend == "mss":
            try:
                return self._capture_mss(filename)
            except ImportError:
                self._backend = None
                raise ImportError(
                    "Neither pyautogui nor mss is installed. "
                    "Install one with: pip install pyautogui  or  pip install mss"
                ) from None
            except Exception as exc:
                raise RuntimeError(f"mss screenshot failed: {exc}") from exc

        raise ImportError("No screenshot backend available.")

    def _capture_pyautogui(self, filename: str) -> str:
        """Capture screenshot using pyautogui."""
        import pyautogui  # type: ignore[import-untyped]

        self._backend = "pyautogui"
        abs_path = os.path.abspath(filename)
        os.makedirs(os.path.dirname(abs_path) or ".", exist_ok=True)
        screenshot = pyautogui.screenshot()
        screenshot.save(abs_path)
        log.debug("ScreenCapture (pyautogui): saved to %s", abs_path)
        return abs_path

    def _capture_mss(self, filename: str) -> str:
        """Capture screenshot using mss."""
        import mss  # type: ignore[import-untyped]

        self._backend = "mss"
        abs_path = os.path.abspath(filename)
        os.makedirs(os.path.dirname(abs_path) or ".", exist_ok=True)
        with mss.mss() as sct:
            sct.shot(output=abs_path)
        log.debug("ScreenCapture (mss): saved to %s", abs_path)
        return abs_path


def main() -> None:
    """Test function: capture a screenshot and print the path."""
    logging.basicConfig(level=logging.DEBUG)
    output = os.path.join(os.path.dirname(__file__) or ".", "test_screenshot.png")
    try:
        sc = ScreenCapture()
        path = sc.capture(output)
        print(f"Screenshot saved to: {path}")
    except ImportError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

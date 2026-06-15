"""InputControl class — wraps pyautogui for keyboard and mouse operations.

Provides a simple object-oriented interface for automating input:
  - type_text(text)   — type a string at the current cursor position
  - press_key(key)    — press and release a single key (or hotkey combo)
  - move_mouse(x, y)  — move the mouse to screen coordinates
  - click(x, y)       — click at screen coordinates

Dependencies (optional, graceful fallback):
  - pyautogui
"""
from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger("goat2.tools.goat_skills.input_control")


def _import_pyautogui():
    """Lazy-import pyautogui; return the module or None if unavailable."""
    try:
        import pyautogui
        return pyautogui
    except ImportError:
        log.warning("pyautogui is not installed — input methods will raise RuntimeError")
        return None


class InputControl:
    """Control keyboard and mouse input via pyautogui.

    All methods raise ``RuntimeError`` if pyautogui is not installed.
    """

    def __init__(self) -> None:
        self._pyautogui = _import_pyautogui()

    # ── helpers ──────────────────────────────────────────────────────────

    def _require(self) -> None:
        """Raise RuntimeError if pyautogui is not available."""
        if self._pyautogui is None:
            raise RuntimeError(
                "pyautogui is not installed. Install it with: pip install pyautogui"
            )

    # ── keyboard ─────────────────────────────────────────────────────────

    def type_text(self, text: str) -> None:
        """Type *text* at the current cursor position.

        Supports unicode characters via ``pyautogui.write()`` (modern API)
        or falls back to ``pyautogui.typewrite()`` for older versions.

        Args:
            text: The string to type.

        Raises:
            RuntimeError: If pyautogui is not installed.
        """
        self._require()
        if not isinstance(text, str):
            text = str(text)
        try:
            if hasattr(self._pyautogui, "write"):
                self._pyautogui.write(text)
            else:
                # Older pyautogui: typewrite expects a list of characters.
                self._pyautogui.typewrite(list(text))
        except Exception as exc:
            log.error("type_text failed: %s", exc)
            raise RuntimeError(f"type_text failed: {exc}") from exc

    def press_key(self, key: str) -> None:
        """Press and release a single key (or a hotkey combination).

        For a single key name (e.g. ``"enter"``, ``"tab"``, ``"ctrl"``),
        uses ``pyautogui.press()``.

        For a combination separated by ``"+"`` (e.g. ``"ctrl+c"``,
        ``"ctrl+shift+esc"``), uses ``pyautogui.hotkey()`` to press them
        in sequence.

        Args:
            key: A key name (e.g. ``"enter"``) or a ``"+"``-separated
                 combination (e.g. ``"ctrl+c"``).

        Raises:
            RuntimeError: If pyautogui is not installed.
        """
        self._require()
        if not isinstance(key, str) or not key.strip():
            raise ValueError(f"Invalid key: {key!r}")

        parts = [k.strip() for k in key.split("+") if k.strip()]

        try:
            if len(parts) == 1:
                self._pyautogui.press(parts[0])
            else:
                self._pyautogui.hotkey(*parts)
        except Exception as exc:
            log.error("press_key failed for %r: %s", key, exc)
            raise RuntimeError(f"press_key failed for {key!r}: {exc}") from exc

    # ── mouse ────────────────────────────────────────────────────────────

    def move_mouse(self, x: int, y: int) -> None:
        """Move the mouse cursor to screen coordinates *(x, y)*.

        No click is performed — only a move.

        Args:
            x: Screen X coordinate in pixels.
            y: Screen Y coordinate in pixels.

        Raises:
            RuntimeError: If pyautogui is not installed.
        """
        self._require()
        try:
            self._pyautogui.moveTo(int(x), int(y))
        except Exception as exc:
            log.error("move_mouse failed to (%d, %d): %s", x, y, exc)
            raise RuntimeError(f"move_mouse failed to ({x}, {y}): {exc}") from exc

    def click(self, x: int, y: int, button: str = "left") -> None:
        """Click at screen coordinates *(x, y)*.

        Args:
            x: Screen X coordinate in pixels.
            y: Screen Y coordinate in pixels.
            button: ``"left"`` (default), ``"right"``, or ``"middle"``.

        Raises:
            RuntimeError: If pyautogui is not installed.
            ValueError: If *button* is not one of ``left``, ``right``, ``middle``.
        """
        self._require()
        if button not in {"left", "right", "middle"}:
            raise ValueError(
                f"Invalid button {button!r}; expected 'left', 'right', or 'middle'"
            )
        try:
            self._pyautogui.click(int(x), int(y), button=button)
        except Exception as exc:
            log.error("click failed at (%d, %d): %s", x, y, exc)
            raise RuntimeError(f"click failed at ({x}, {y}): {exc}") from exc


# ── main test ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s | %(name)s | %(message)s",
    )

    ctrl = InputControl()

    # If pyautogui is not installed, the test will simply report that fact.
    if ctrl._pyautogui is None:
        print("SKIP: pyautogui not installed — cannot run interactive test.")
        print("Install with: pip install pyautogui")
        sys.exit(0)

    print("InputControl interactive test")
    print("=============================")
    print()
    print("1. move_mouse(500, 300)")
    ctrl.move_mouse(500, 300)
    print("   -> moved to (500, 300)")
    print()
    print("2. click(500, 300)")
    ctrl.click(500, 300)
    print("   -> clicked at (500, 300)")
    print()
    print("3. type_text('Hello from InputControl!')")
    ctrl.type_text("Hello from InputControl!")
    print("   -> typed text")
    print()
    print("4. press_key('enter')")
    ctrl.press_key("enter")
    print("   -> pressed enter")
    print()
    print("5. press_key('ctrl+a')  (select all)")
    ctrl.press_key("ctrl+a")
    print("   -> pressed ctrl+a")
    print()
    print("All tests passed.")

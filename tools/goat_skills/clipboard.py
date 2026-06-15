"""ClipboardTool — read/write system clipboard with multiple backends.

Provides a ``ClipboardTool`` class with ``read()`` and ``write(text)``
methods. Tries backends in order:

1. **pyperclip** (Python library, cross-platform)
2. **xclip** (Linux binary)
3. **xsel** (Linux binary)
4. **tkinter** (built-in Python, fallback on X11/Wayland)

Usage::

    from tools.goat_skills.clipboard import ClipboardTool

    cb = ClipboardTool()
    text = cb.read()
    cb.write("Hello, world!")

If run as ``__main__``, the module exercises both methods and prints
the result.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import tkinter
from typing import Optional

log = logging.getLogger("goat2.tools.goat_skills.clipboard")

__all__ = ["ClipboardTool"]


# ── Lazy imports ────────────────────────────────────────────────────────

def _import_pyperclip():
    """Try to import pyperclip; return the module or None."""
    try:
        import pyperclip  # type: ignore[import-untyped]

        return pyperclip
    except ImportError:
        return None


# ── ClipboardTool class ─────────────────────────────────────────────────

class ClipboardTool:
    """Read and write the system clipboard with automatic backend selection.

    Backend priority:
        1. pyperclip (cross-platform Python library)
        2. xclip (Linux binary)
        3. xsel (Linux binary)
        4. tkinter (built-in, X11/Wayland)

    The first available backend is cached and reused for subsequent calls.
    """

    def __init__(self) -> None:
        self._backend: Optional[str] = None
        self._pyperclip = None

    # ── public API ───────────────────────────────────────────────────

    def read(self) -> str:
        """Return the current clipboard contents as a string.

        Raises:
            RuntimeError: if no clipboard backend is available.
        """
        return self._dispatch("read")

    def write(self, text: str) -> None:
        """Write *text* to the system clipboard.

        Raises:
            RuntimeError: if no clipboard backend is available.
        """
        if not isinstance(text, str):
            text = str(text)
        self._dispatch("write", text)

    # ── dispatch ─────────────────────────────────────────────────────

    def _dispatch(self, operation: str, text: str = "") -> str:
        """Try each backend in priority order for *operation*.

        Returns the result string (for ``read``) or ``""`` (for ``write``).
        """
        # If we already found a working backend, try it first.
        if self._backend is not None:
            result = self._try_backend(self._backend, operation, text)
            if result is not None:
                return result
            # Backend stopped working — reset and re-probe.
            self._backend = None

        backends = ["pyperclip", "xclip", "xsel", "tkinter"]
        for name in backends:
            result = self._try_backend(name, operation, text)
            if result is not None:
                self._backend = name
                log.debug("ClipboardTool using backend '%s'", name)
                return result

        raise RuntimeError(
            "No clipboard backend available. Install pyperclip, xclip, "
            "xsel, or ensure tkinter is available."
        )

    def _try_backend(
        self, name: str, operation: str, text: str = ""
    ) -> Optional[str]:
        """Try *operation* on backend *name*. Return result or None on failure."""
        try:
            if name == "pyperclip":
                return self._run_pyperclip(operation, text)
            elif name == "xclip":
                return self._run_xclip(operation, text)
            elif name == "xsel":
                return self._run_xsel(operation, text)
            elif name == "tkinter":
                return self._run_tkinter(operation, text)
        except Exception as exc:
            log.warning("ClipboardTool backend '%s' failed: %s", name, exc)
            return None
        return None

    # ── pyperclip ────────────────────────────────────────────────────

    def _run_pyperclip(self, operation: str, text: str) -> Optional[str]:
        if self._pyperclip is None:
            self._pyperclip = _import_pyperclip()
        if self._pyperclip is None:
            return None

        if operation == "read":
            result = self._pyperclip.paste()
            return str(result) if result is not None else ""
        else:  # write
            self._pyperclip.copy(text)
            return ""

    # ── xclip ────────────────────────────────────────────────────────

    def _run_xclip(self, operation: str, text: str) -> Optional[str]:
        if shutil.which("xclip") is None:
            return None

        if operation == "read":
            result = subprocess.run(
                ["xclip", "-selection", "clipboard", "-o"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                return None
            return result.stdout or ""
        else:  # write
            result = subprocess.run(
                ["xclip", "-selection", "clipboard"],
                input=text,
                capture_output=True,
                text=True,
                timeout=10,
            )
            return "" if result.returncode == 0 else None

    # ── xsel ─────────────────────────────────────────────────────────

    def _run_xsel(self, operation: str, text: str) -> Optional[str]:
        if shutil.which("xsel") is None:
            return None

        if operation == "read":
            result = subprocess.run(
                ["xsel", "--clipboard", "--output"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                return None
            return result.stdout or ""
        else:  # write
            result = subprocess.run(
                ["xsel", "--clipboard", "--input"],
                input=text,
                capture_output=True,
                text=True,
                timeout=10,
            )
            return "" if result.returncode == 0 else None

    # ── tkinter ──────────────────────────────────────────────────────

    def _run_tkinter(self, operation: str, text: str) -> Optional[str]:
        """Use tkinter's clipboard interface (X11/Wayland)."""
        root = tkinter.Tk()
        root.withdraw()  # hide the window
        try:
            if operation == "read":
                try:
                    return root.clipboard_get()
                except tkinter.TclError:
                    # Clipboard empty or unavailable
                    return ""
            else:  # write
                root.clipboard_clear()
                root.clipboard_append(text)
                root.update()  # required for the clipboard to persist
                return ""
        finally:
            try:
                root.destroy()
            except tkinter.TclError:
                pass


# ── Main test ───────────────────────────────────────────────────────────

def main() -> None:
    """Exercise ClipboardTool: write a test string, read it back, print."""
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(levelname)s %(name)s %(message)s",
    )

    cb = ClipboardTool()

    # Write
    test_text = "Hello from ClipboardTool!"
    print(f"Writing: {test_text!r}")
    cb.write(test_text)
    print("Write succeeded.")

    # Read
    result = cb.read()
    print(f"Read back: {result!r}")

    if result == test_text:
        print("SUCCESS: readback matches written text.")
    else:
        print(f"NOTE: readback differs (may be due to other clipboard activity).")

    # Read without prior write (should return empty or current clipboard)
    print(f"Current clipboard contents: {cb.read()!r}")


if __name__ == "__main__":
    sys.exit(main())

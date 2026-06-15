"""Input + clipboard ToolDefinitions — TOOLS 3-6, 9, 10 (goat_skills).

Wraps the InputControl and ClipboardTool utility classes (in
input_control.py and clipboard.py) with 6 ToolDefinitions:
MOUSE_CLICK, MOUSE_MOVE, KEYBOARD_TYPE, KEYBOARD_HOTKEY,
CLIPBOARD_GET, CLIPBOARD_SET.

LAZY INSTANTIATION:
===================
The InputControl class is constructed inside each handler, not at
import time. pyautogui (its single backend) reads $DISPLAY at import
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

log = logging.getLogger("goat2.tools.goat_skills.host_tools")

__all__ = [
    "MOUSE_CLICK",
    "MOUSE_MOVE",
    "KEYBOARD_TYPE",
    "KEYBOARD_HOTKEY",
    "CLIPBOARD_GET",
    "CLIPBOARD_SET",
]


def _make_input_control() -> Any:
    """Construct an InputControl; return the instance."""
    from tools.goat_skills.input_control import InputControl

    return InputControl()


def _make_clipboard_tool() -> Any:
    """Construct a ClipboardTool; return the instance."""
    from tools.goat_skills.clipboard import ClipboardTool

    return ClipboardTool()


# ── TOOL 3: mouse_click(x, y, button="left") ────────────────────────────

async def _mouse_click_handler(x: int, y: int, button: str = "left") -> str:
    """Click at screen coordinates. InputControl.click raises on failure."""
    try:
        ic = _make_input_control()
        ic.click(int(x), int(y), button=button)
    except ValueError as exc:
        log.warning("mouse_click: invalid arg: %s", exc)
        return f"ERROR: {exc}"
    except RuntimeError as exc:
        return f"ERROR: {exc}"
    except Exception as exc:  # noqa: BLE001
        log.warning("mouse_click: unexpected: %s", exc)
        return f"ERROR: mouse_click failed: {exc}"
    return f"clicked at ({x}, {y})"


MOUSE_CLICK = make_tool(
    name="mouse_click",
    description=(
        "Click at the given screen coordinates. button is 'left' (default), "
        "'right', or 'middle'. Returns 'clicked at (x, y)'. GOAT-only."
    ),
    parameters={
        "type": "object",
        "properties": {
            "x": {"type": "integer", "description": "Screen X coordinate."},
            "y": {"type": "integer", "description": "Screen Y coordinate."},
            "button": {
                "type": "string",
                "enum": ["left", "right", "middle"],
                "description": "Mouse button (default 'left').",
                "default": "left",
            },
        },
        "required": ["x", "y"],
    },
    handler=_mouse_click_handler,
)


# ── TOOL 4: mouse_move(x, y) ────────────────────────────────────────────

async def _mouse_move_handler(x: int, y: int) -> str:
    """Move the mouse to (x, y) without clicking."""
    try:
        ic = _make_input_control()
        ic.move_mouse(int(x), int(y))
    except RuntimeError as exc:
        return f"ERROR: {exc}"
    except Exception as exc:  # noqa: BLE001
        log.warning("mouse_move: unexpected: %s", exc)
        return f"ERROR: mouse_move failed: {exc}"
    return f"moved to ({x}, {y})"


MOUSE_MOVE = make_tool(
    name="mouse_move",
    description=(
        "Move the mouse to (x, y) without clicking. Returns 'moved to (x, y)'. GOAT-only."
    ),
    parameters={
        "type": "object",
        "properties": {
            "x": {"type": "integer", "description": "Screen X coordinate."},
            "y": {"type": "integer", "description": "Screen Y coordinate."},
        },
        "required": ["x", "y"],
    },
    handler=_mouse_move_handler,
)


# ── TOOL 5: keyboard_type(text) ─────────────────────────────────────────

async def _keyboard_type_handler(text: str) -> str:
    """Type text at the current cursor position."""
    if not isinstance(text, str):
        text = str(text)
    try:
        ic = _make_input_control()
        ic.type_text(text)
    except RuntimeError as exc:
        return f"ERROR: {exc}"
    except Exception as exc:  # noqa: BLE001
        log.warning("keyboard_type: unexpected: %s", exc)
        return f"ERROR: keyboard_type failed: {exc}"
    return f"typed: {text[:50]}"


KEYBOARD_TYPE = make_tool(
    name="keyboard_type",
    description=(
        "Type text at the current cursor position. Returns 'typed: <text>' "
        "truncated to 50 chars. GOAT-only."
    ),
    parameters={
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "Text to type at the current cursor position.",
            },
        },
        "required": ["text"],
    },
    handler=_keyboard_type_handler,
)


# ── TOOL 6: keyboard_hotkey(*keys) ──────────────────────────────────────

async def _keyboard_hotkey_handler(*keys: str) -> str:
    """Press a key combination. InputControl.press_key supports '+' syntax."""
    if not keys:
        log.warning("keyboard_hotkey: no keys provided")
        return "ERROR: keyboard_hotkey requires at least one key"
    try:
        ic = _make_input_control()
        ic.press_key("+".join(keys))
    except (ValueError, RuntimeError) as exc:
        return f"ERROR: {exc}"
    except Exception as exc:  # noqa: BLE001
        log.warning("keyboard_hotkey: unexpected: %s", exc)
        return f"ERROR: keyboard_hotkey failed: {exc}"
    return f"pressed: {'+'.join(keys)}"


KEYBOARD_HOTKEY = make_tool(
    name="keyboard_hotkey",
    description=(
        "Press a key combination at the current focus. Example: "
        "keyboard_hotkey('ctrl', 'c') to copy. Returns 'pressed: ctrl+c'. GOAT-only."
    ),
    parameters={
        "type": "object",
        "properties": {
            "keys": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of key names (e.g. ['ctrl', 'c']).",
            },
        },
        "required": ["keys"],
    },
    # Tool runner will pass the 'keys' array as *positional* args.
    handler=lambda keys: _keyboard_hotkey_handler(*(keys or [])),
)


# ── TOOL 9: clipboard_get() ─────────────────────────────────────────────

async def _clipboard_get_handler() -> str:
    """Read the current clipboard contents."""
    try:
        cb = _make_clipboard_tool()
        return cb.read()
    except RuntimeError as exc:
        return f"ERROR: {exc}"
    except Exception as exc:  # noqa: BLE001
        log.warning("clipboard_get: unexpected: %s", exc)
        return f"ERROR: clipboard_get failed: {exc}"


CLIPBOARD_GET = make_tool(
    name="clipboard_get",
    description=(
        "Read the current clipboard contents. Tries pyperclip, xclip, xsel, "
        "and tkinter in order. GOAT-only."
    ),
    parameters={"type": "object", "properties": {}, "required": []},
    handler=_clipboard_get_handler,
)


# ── TOOL 10: clipboard_set(text) ────────────────────────────────────────

async def _clipboard_set_handler(text: str) -> str:
    """Write text to the clipboard."""
    if not isinstance(text, str):
        text = str(text)
    try:
        cb = _make_clipboard_tool()
        cb.write(text)
    except RuntimeError as exc:
        return f"ERROR: {exc}"
    except Exception as exc:  # noqa: BLE001
        log.warning("clipboard_set: unexpected: %s", exc)
        return f"ERROR: clipboard_set failed: {exc}"
    return "clipboard set"


CLIPBOARD_SET = make_tool(
    name="clipboard_set",
    description=(
        "Write text to the clipboard. Returns 'clipboard set' on success, or "
        "an 'ERROR:' string when no backend is available. GOAT-only."
    ),
    parameters={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Text to place on the clipboard."},
        },
        "required": ["text"],
    },
    handler=_clipboard_set_handler,
)

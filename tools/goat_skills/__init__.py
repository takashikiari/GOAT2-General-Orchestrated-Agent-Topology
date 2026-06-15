"""GOAT computer-control skills — direct host access for GOAT only.

This package exposes 12 tools that drive the host machine directly:
screen capture + OCR, mouse and keyboard input, an unrestricted shell,
browser open, app list/focus, and clipboard read/write. They are
**only** wired into ``supervisor.identity.direct_response()`` — DAG
agents do NOT have access to these tools.

DIRECTORY STRUCTURE:
====================
tools/goat_skills/
├── __init__.py         — this file, re-exports all tools + GOAT_SKILLS_TOOLS
├── _common.py          — safe_import() helper for optional dependencies
├── screen.py           — ScreenCapture class (lazy)
├── input_control.py    — InputControl class (lazy)
├── shell.py            — TOOL 7  (shell_run, unrestricted)
├── browser.py          — TOOLS 8, 11, 12
├── clipboard.py        — ClipboardTool class (lazy)
├── screen_tools.py     — TOOLS 1, 2 (screen_capture, screen_read_region)
└── host_tools.py       — TOOLS 3-6, 9, 10 (mouse/keyboard/clipboard wrappers)

The 4 utility classes (``ScreenCapture``, ``InputControl``,
``ClipboardTool``, ``BrowserAutomation``) live in their respective
modules. They are lazy-instantiated inside ``screen_tools.py`` and
``host_tools.py`` handlers because some of them raise at construction
time when optional dependencies (pyautogui, selenium) are missing or
no X display is available.

GRACEFUL FALLBACKS:
===================
Every tool that depends on an optional third-party library returns an
``ERROR: ...`` string when the dependency is missing or no display is
available — it never raises. The only non-optional tool is
``shell_run`` (stdlib only).
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

# Re-export utility classes (callers can use the raw class if they
# want to bypass the tool layer).
from tools.goat_skills.screen import ScreenCapture
from tools.goat_skills.input_control import InputControl
from tools.goat_skills.clipboard import ClipboardTool
from tools.goat_skills.browser import BrowserAutomation

# ToolDefinition-based modules (pre-existing).
from tools.goat_skills.shell import SHELL_RUN
from tools.goat_skills.browser import APP_FOCUS, APP_LIST, BROWSER_OPEN

# Wrappers around the class-based tools (split across two files to
# keep each under the 260-line cap).
from tools.goat_skills.screen_tools import (
    SCREEN_CAPTURE,
    SCREEN_READ_REGION,
)
from tools.goat_skills.host_tools import (
    CLIPBOARD_GET,
    CLIPBOARD_SET,
    KEYBOARD_HOTKEY,
    KEYBOARD_TYPE,
    MOUSE_CLICK,
    MOUSE_MOVE,
)

if TYPE_CHECKING:
    from agents.base_agent import ToolDefinition

log = logging.getLogger("goat2.tools.goat_skills")

# Aggregated list — passed to _call_with_tools in direct_response().
GOAT_SKILLS_TOOLS: list["ToolDefinition"] = [
    SCREEN_CAPTURE,        # 1
    SCREEN_READ_REGION,    # 2
    MOUSE_CLICK,           # 3
    MOUSE_MOVE,            # 4
    KEYBOARD_TYPE,         # 5
    KEYBOARD_HOTKEY,       # 6
    SHELL_RUN,             # 7
    BROWSER_OPEN,          # 8
    CLIPBOARD_GET,         # 9
    CLIPBOARD_SET,         # 10
    APP_LIST,              # 11
    APP_FOCUS,             # 12
]

__all__ = [
    # Class-based utility re-exports.
    "ScreenCapture",
    "InputControl",
    "ClipboardTool",
    "BrowserAutomation",
    # ToolDefinition constants.
    "SCREEN_CAPTURE",
    "SCREEN_READ_REGION",
    "MOUSE_CLICK",
    "MOUSE_MOVE",
    "KEYBOARD_TYPE",
    "KEYBOARD_HOTKEY",
    "SHELL_RUN",
    "BROWSER_OPEN",
    "CLIPBOARD_GET",
    "CLIPBOARD_SET",
    "APP_LIST",
    "APP_FOCUS",
    # Aggregated list.
    "GOAT_SKILLS_TOOLS",
]

log.debug(
    "goat_skills: package loaded (%d tools wired for GOAT conversational mode)",
    len(GOAT_SKILLS_TOOLS),
)

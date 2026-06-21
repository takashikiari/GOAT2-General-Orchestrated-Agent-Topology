"""
utils.logging.symbols — cross-module symbol-conflict detection.

Usage:
    from utils.logging.symbols import register_symbols
    register_symbols(__name__, "MyClass", "MY_CONSTANT")

register_symbols() maintains a global dict of symbol_name → first-registering
module.  A WARNING is emitted whenever the same name is claimed by a second
module, catching duplicate-definition bugs (two TaskStatus enums, two
AsyncOpenAI client caches, etc.) at import time rather than at runtime.

Concrete example:
    # agents/types.py
    register_symbols("agents.types", "TaskStatus")    # registered OK

    # supervisor/types.py
    register_symbols("supervisor.types", "TaskStatus")
    # → WARNING: SYMBOL CONFLICT: 'TaskStatus' defined in both
    #            'agents.types' and 'supervisor.types'
"""
from __future__ import annotations

from utils.logging.setup import get_logger

_symbol_registry: dict[str, str] = {}  # symbol_name → first-registering module


def register_symbols(module_name: str, *names: str) -> None:
    """
    Register symbol names as belonging to ``module_name``.

    Emits a WARNING if a name was already claimed by a different module.
    Re-registering the same name from the same module is a no-op (idempotent).

    Args:
        module_name: The defining module — pass ``__name__``.
        *names:      Symbol names to register (class names, constants, …).
    """
    log = get_logger("goat2.symbol_registry")
    for name in names:
        existing = _symbol_registry.get(name)
        if existing and existing != module_name:
            log.warning(
                "SYMBOL CONFLICT: %r defined in both %r and %r",
                name, existing, module_name,
            )
        else:
            _symbol_registry[name] = module_name

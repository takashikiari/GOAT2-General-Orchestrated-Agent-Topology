"""Global registry accessor for tool handlers.

Provides a module-level get_registry() function that returns the global
ServiceRegistry instance. The registry is initialized by the entry point
(cli.py, telegram_bot.py, etc.) and accessed here.

USAGE:
    from tools.registry_accessor import get_registry
    registry = get_registry()
    mm = registry.memory_manager
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from config.registry import ServiceRegistry

_global_registry: ServiceRegistry | None = None


def set_registry(registry: "ServiceRegistry") -> None:
    """Set the global ServiceRegistry instance.

    Args:
        registry: ServiceRegistry instance to use globally
    """
    global _global_registry
    _global_registry = registry


def get_registry() -> "ServiceRegistry":
    """Get the global ServiceRegistry instance.

    Returns:
        ServiceRegistry instance

    Raises:
        RuntimeError: If registry has not been set via set_registry()
    """
    if _global_registry is None:
        raise RuntimeError(
            "ServiceRegistry not initialized. "
            "Call set_registry() from your entry point before using tools."
        )
    return _global_registry


def reset_registry() -> None:
    """Reset the global registry to None (for testing)."""
    global _global_registry
    _global_registry = None
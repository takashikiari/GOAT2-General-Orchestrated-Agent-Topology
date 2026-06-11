"""Public re-exports for the config/ package.

All files in this package use the ``goat2.config.<module>`` logger
namespace. This __init__ declares a parent logger so DEBUG filters
can match the whole subtree at once.
"""
from __future__ import annotations

import logging

log = logging.getLogger("goat2.config")

from .settings import Settings, Provider, ModelSpec, get_model

__all__ = ["Settings", "Provider", "ModelSpec", "get_model"]

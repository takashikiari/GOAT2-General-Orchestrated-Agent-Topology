"""config.timeouts — shared timeout constants for the pipeline."""
from __future__ import annotations

import os

TURN_TIMEOUT: float = float(os.environ.get("GOAT_TURN_TIMEOUT", "120.0"))

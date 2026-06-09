"""Central timeout registry for GOAT 2.0 async operations.

This module defines all timeout values used throughout GOAT 2.0
for HTTP requests, Redis operations, and task execution.

TIMEOUT CATEGORIES:
===================
CONVERSATION TIMEOUTS:
    - TURN_TIMEOUT: Maximum seconds per conversation turn
    - Prevents hung tasks from blocking the supervisor

TOOL TIMEOUTS:
    - TOOL_TIMEOUT: Maximum seconds for tool execution
    - Ensures tools don't hang indefinitely

EXTERNAL SERVICE TIMEOUTS:
    - LETTA_TIMEOUT: HTTP timeout for Letta server requests
    - REDIS_TIMEOUT: Connection timeout for Redis operations

TIMEOUT PHILOSOPHY:
===================
- Short timeouts for external services (fail fast)
- Longer timeouts for user-facing operations
- All timeouts should be configurable via environment variables
- Timeouts prevent resource exhaustion from hung operations

All files should import from this module instead of hardcoding timeout values.
"""
from __future__ import annotations

from typing import Final

__all__ = ["TURN_TIMEOUT", "TOOL_TIMEOUT", "LETTA_TIMEOUT", "REDIS_TIMEOUT"]

TURN_TIMEOUT: Final[int] = 120
"""Maximum seconds per conversation turn.

Prevents hung tasks from blocking the supervisor.
Applied to DAG task execution and LLM calls.
Configured in config/settings.py (SupervisorConfig.turn_timeout).
"""

TOOL_TIMEOUT: Final[int] = 30
"""Maximum seconds for individual tool execution.

Ensures tools don't hang indefinitely. Applied to
file operations, web searches, and memory queries.
Shorter than TURN_TIMEOUT to allow retry or fallback.
"""

LETTA_TIMEOUT: Final[int] = 8
"""HTTP timeout for Letta server requests (seconds).

Short timeout for fail-fast behavior. Letta operations
should complete quickly or be considered unavailable.
Applied to all Letta REST API calls.
"""

REDIS_TIMEOUT: Final[int] = 5
"""Connection timeout for Redis operations (seconds).

Very short timeout since Redis should respond instantly.
Used for health checks and connection pooling.
Long timeouts indicate Redis is unavailable.
"""

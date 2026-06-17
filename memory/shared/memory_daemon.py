"""Memory daemon — silent three-tier background promotion.

Runs as a detached ``asyncio`` task. Every ``interval_s`` seconds it walks
the three memory tiers and applies the promotion/sliding-window rules
(Tier 1: working → episodic when near capacity, Tier 2: episodic
sliding window when at capacity, Tier 3: permanent entries never
touched). Never blocks GOAT; never uses DAG; pure Python; zero
singletons; loop catches ALL exceptions.

CONFIG:
    All thresholds default from ``config/memory.toml`` at import time
    (sections ``[daemon]``, ``[working]``, ``[episodic]``) via the
    ``memory_daemon_config`` helper module. Falls back to
    ``config.fallbacks`` constants when the toml is missing.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from memory.shared.memory_daemon_config import MEMORY_DAEMON_DEFAULTS

if TYPE_CHECKING:
    from config.registry import ServiceRegistry
    from memory.shared.memory_manager import MemoryManager

log = logging.getLogger("goat2.memory.shared.daemon")

__all__ = ["MemoryDaemon"]


class MemoryDaemon:
    """Silent three-tier background promotion daemon.

    Owns nothing at construction; ``start`` takes the live MemoryManager
    + ServiceRegistry and creates one background task. ``stop`` cancels
    it cleanly. All thresholds default from ``config/memory.toml``
    and are overridable for tests and tuning.
    """

    def __init__(
        self,
        interval_s: float = MEMORY_DAEMON_DEFAULTS["interval_s"],
        working_soft: int = MEMORY_DAEMON_DEFAULTS["working_soft"],
        working_max: int = MEMORY_DAEMON_DEFAULTS["working_max"],
        episodic_soft: int = MEMORY_DAEMON_DEFAULTS["episodic_soft"],
        episodic_max: int = MEMORY_DAEMON_DEFAULTS["episodic_max"],
        working_age_s: float = MEMORY_DAEMON_DEFAULTS["tier1_age_hours"] * 3600.0,
        agent_role: str = "user_session",
    ) -> None:
        """Configure the daemon. No I/O happens here.

        Defaults are sourced from ``config/memory.toml``; fall back to
        ``config.fallbacks`` when the toml is missing.

        Args:
            interval_s: Sleep between promotion/sliding-window sweeps.
            working_soft: When working memory reaches this, Tier 1 fires.
            working_max: Hard cap reported in DEBUG logs.
            episodic_soft: When episodic memory reaches this, Tier 2 fires.
            episodic_max: Hard cap reported in DEBUG logs.
            working_age_s: Only promote working entries older than this.
            agent_role: Namespace the daemon operates on.
        """
        self.interval_s = interval_s
        self.working_soft = working_soft
        self.working_max = working_max
        self.episodic_soft = episodic_soft
        self.episodic_max = episodic_max
        self.working_age_s = working_age_s
        self.agent_role = agent_role

        self._task: asyncio.Task | None = None
        self._stop = False
        self._manager: "MemoryManager | None" = None
        self._registry: "ServiceRegistry | None" = None

        log.debug(
            "MemoryDaemon: configured (interval=%.1fs working=%d/%d episodic=%d/%d age=%.0fs)",
            self.interval_s, self.working_soft, self.working_max,
            self.episodic_soft, self.episodic_max, self.working_age_s,
        )

    # ------------------------------------------------------------------ start/stop

    async def start(
        self,
        memory_manager: "MemoryManager",
        registry: "ServiceRegistry",
    ) -> None:
        """Begin the background sweep loop. Idempotent.

        Args:
            memory_manager: The live MemoryManager (provides working + episodic).
            registry: The live ServiceRegistry — kept for future hooks.
        """
        if self._task is not None and not self._task.done():
            log.debug("MemoryDaemon: start() called but already running — ignoring")
            return
        self._manager = memory_manager
        self._registry = registry
        self._stop = False
        self._task = asyncio.create_task(self._run_loop(), name="memory-daemon")
        log.info("MemoryDaemon: started (interval=%.1fs)", self.interval_s)

    async def stop(self) -> None:
        """Cancel the sweep loop and wait briefly for clean shutdown."""
        self._stop = True
        task = self._task
        if task is None:
            log.debug("MemoryDaemon: stop() called but never started")
            return
        if not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception) as exc:  # noqa: BLE001
                log.debug("MemoryDaemon: stop() task exit (%s)", type(exc).__name__)
        self._task = None
        self._manager = None
        self._registry = None
        log.info("MemoryDaemon: stopped")

    # ------------------------------------------------------------------ loop

    async def _run_loop(self) -> None:
        """Forever loop: sleep, then sweep tiers. Never raises."""
        log.debug("MemoryDaemon: loop entered")
        while not self._stop:
            try:
                await self._check_working()
            except Exception as exc:  # noqa: BLE001
                log.warning("MemoryDaemon: _check_working crashed: %s", exc)
            try:
                await self._check_episodic()
            except Exception as exc:  # noqa: BLE001
                log.warning("MemoryDaemon: _check_episodic crashed: %s", exc)
            try:
                await asyncio.sleep(self.interval_s)
            except asyncio.CancelledError:
                log.debug("MemoryDaemon: sleep cancelled — exiting")
                return
            except Exception as exc:  # noqa: BLE001
                log.warning("MemoryDaemon: sleep failed: %s", exc)
        log.debug("MemoryDaemon: loop exited")

    # ------------------------------------------------------------------ tiers

    async def _check_working(self) -> None:
        """Tier 1: working → episodic promotion when near capacity.

        Delegates to ``check_and_promote`` (excludes dag:*, LLM-scores,
        no-op below soft). The age filter is enforced here at the
        candidate-set level because ``check_and_promote`` does not
        currently gate on age. No-op silently when the backend is
        unavailable.
        """
        if self._manager is None:
            return
        try:
            working = getattr(self._manager, "working", None)
            backend = getattr(working, "backend", None) if working else None
            if backend is None:
                log.debug("MemoryDaemon: no working backend — Tier 1 skipped")
                return
            keys = await backend.keys(self.agent_role)
            count = len(keys)
            log.debug("MemoryDaemon: working %d/%d (soft=%d)", count, self.working_max, self.working_soft)
            if count < self.working_soft:
                return

            now = time.time()
            eligible = []
            for key in keys:
                key_str = str(key)
                if "dag:" in key_str:
                    continue
                rec = await backend.get(self.agent_role, key)
                if not rec:
                    continue
                created_ts = float(rec.get("created_at_ts") or 0)
                if created_ts and (now - created_ts) < self.working_age_s:
                    continue
                eligible.append(key_str)
            if not eligible:
                log.debug("MemoryDaemon: at soft but no entries older than %.0fs", self.working_age_s)
                return

            log.info(
                "MemoryDaemon: Tier 1 firing (%d/%d, %d eligible)",
                count, self.working_max, len(eligible),
            )
            from memory.working.capacity import check_and_promote
            episodic = getattr(self._manager, "episodic", None)
            await check_and_promote(
                backend, episodic, self.agent_role, max_entries=self.working_max,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("MemoryDaemon: Tier 1 failed: %s", exc)

    async def _check_episodic(self) -> None:
        """Tier 2: episodic sliding window when near capacity.

        Delegates to ``check_and_slide`` (skips permanent, LLM-scores,
        falls back to deleting the oldest batch on error, no-op below
        soft). No-op silently when the episodic backend is unavailable.
        """
        if self._manager is None:
            return
        try:
            episodic = getattr(self._manager, "episodic", None)
            if episodic is None:
                log.debug("MemoryDaemon: no episodic backend — Tier 2 skipped")
                return
            try:
                count = await episodic.count(self.agent_role)
            except Exception as exc:  # noqa: BLE001
                log.debug("MemoryDaemon: episodic.count failed: %s", exc)
                return
            log.debug("MemoryDaemon: episodic %d/%d (soft=%d)", count, self.episodic_max, self.episodic_soft)
            if count < self.episodic_soft:
                return

            log.info("MemoryDaemon: Tier 2 firing (%d/%d)", count, self.episodic_max)
            from memory.episodic.sliding_window import check_and_slide
            await check_and_slide(episodic, self.agent_role, max_entries=self.episodic_max)
        except Exception as exc:  # noqa: BLE001
            log.warning("MemoryDaemon: Tier 2 failed: %s", exc)

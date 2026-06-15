"""DAG control public API — pause/resume/stop/updates.

Extracted from ``supervisor.py`` to keep that file under the 260-line
ceiling. Each method is a one-line delegation to the existing
``supervisor.pipeline.dag_control`` / ``dag_awareness`` helpers — the
extraction is purely mechanical and preserves every behavior and
signature, so callers (``supervisor.interfaces.telegram_bot``,
``tools.goat_skills``) are unaffected.

No module-level state; everything operates on the live supervisor.
"""
from __future__ import annotations

__all__ = ["pause_dag", "resume_dag", "stop_dag", "get_dag_updates"]


async def pause_dag(supervisor, session_id: str) -> None:
    """Pause a running DAG after its current wave."""
    from supervisor.pipeline.dag_control import write_dag_control
    await write_dag_control(supervisor.memory_manager, session_id, "pause")


async def resume_dag(supervisor, session_id: str) -> None:
    """Resume a paused DAG."""
    from supervisor.pipeline.dag_control import write_dag_control
    await write_dag_control(supervisor.memory_manager, session_id, "run")


async def stop_dag(supervisor, session_id: str) -> None:
    """Stop a running DAG after its current wave."""
    from supervisor.pipeline.dag_control import write_dag_control
    await write_dag_control(supervisor.memory_manager, session_id, "stop")


async def get_dag_updates(supervisor, session_id: str) -> dict | None:
    """Read ``dag:<session_id>:progress`` from working memory.

    Returns the progress dict or ``None`` if no DAG with that id is known.
    """
    from supervisor.pipeline.dag_awareness import read_dag_progress
    return await read_dag_progress(supervisor.registry, session_id)

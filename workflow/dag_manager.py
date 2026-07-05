"""workflow.dag_manager — lifecycle manager for background DAG runs.

``DagManager`` owns the set of currently running ``asyncio.Task`` s, one
per active DAG.  It bridges ``WorkflowRunner`` (pure execution) with
``DagChannel`` (Redis state/comms) and ``AgentRouter`` (agent resolution).

Design contract:
- No singleton.  One ``DagManager`` per application scope.
- The manager never touches the LLM directly — that is the agents' job.
- On completion or failure the manager writes final state to ``DagChannel``
  and closes the channel connection.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from typing import Any

from workflow.agent_node import make_runner, make_stub_runner
from workflow.dag_channel import DagChannel
from workflow.errors import WorkflowNotFound
from workflow.models import DAGGraph, TaskNode
from workflow.routing import AgentRouter
from workflow.runner import WorkflowRunner

log = logging.getLogger("goat2.workflow.dag_manager")

ChannelFactory = Callable[[str], DagChannel]


class DagManager:
    """Manages the lifecycle of background DAG ``asyncio.Task`` s.

    Args:
        runner: Shared ``WorkflowRunner`` instance (stateless, reusable).
        channel_factory: Callable ``(dag_id: str) -> DagChannel``.
        router: ``AgentRouter`` for resolving agent roles to instances.
            If ``None``, stub runners are used (useful for testing).
    """

    def __init__(
        self,
        runner: WorkflowRunner,
        channel_factory: ChannelFactory,
        router: AgentRouter | None = None,
    ) -> None:
        self._runner = runner
        self._channel_factory = channel_factory
        self._router = router
        self._tasks: dict[str, asyncio.Task] = {}
        self._channels: dict[str, DagChannel] = {}

    # ── public API ────────────────────────────────────────────────────────────

    def start(
        self,
        graph: DAGGraph,
        initial_context: dict[str, Any] | None = None,
        *,
        dag_id: str | None = None,
    ) -> str:
        """Schedule ``graph`` as a background asyncio.Task.

        Args:
            graph: The DAG to run.
            initial_context: Optional seed data passed to the runner.
            dag_id: Override the auto-generated UUID dag_id.

        Returns:
            The ``dag_id`` string that identifies this run.

        Raises:
            ValueError: If a DAG with the same ``dag_id`` is already active.
        """
        effective_id = dag_id or str(uuid.uuid4())[:8]
        if effective_id in self._tasks:
            raise ValueError(f"DAG '{effective_id}' is already running")

        channel = self._channel_factory(effective_id)
        self._channels[effective_id] = channel

        task = asyncio.create_task(
            self._run(effective_id, graph, channel, initial_context or {}),
            name=f"dag:{effective_id}",
        )
        self._tasks[effective_id] = task
        task.add_done_callback(lambda _: self._cleanup(effective_id))
        log.info("dag_manager: started dag_id=%s nodes=%d", effective_id, len(graph.nodes))
        return effective_id

    async def stop(self, dag_id: str) -> None:
        """Cancel a running DAG and wait for it to terminate.

        Raises:
            WorkflowNotFound: If ``dag_id`` is not currently active.
        """
        task = self._tasks.get(dag_id)
        if task is None:
            raise WorkflowNotFound(dag_id, registered=list(self._tasks))
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        log.info("dag_manager: stopped dag_id=%s", dag_id)

    def active(self) -> list[str]:
        """Return dag_ids of currently running DAGs."""
        return [did for did, t in self._tasks.items() if not t.done()]

    def get_channel(self, dag_id: str) -> DagChannel | None:
        """Return the ``DagChannel`` for a given ``dag_id``, or ``None``."""
        return self._channels.get(dag_id)

    # ── graph construction ────────────────────────────────────────────────────

    def build_graph(
        self,
        node_specs: list[dict[str, Any]],
        dag_id: str,
        *,
        use_stubs: bool = False,
    ) -> DAGGraph:
        """Build a ``DAGGraph`` from a list of node specification dicts.

        Each spec must have:
            ``id``    — unique node identifier
            ``role``  — agent role (e.g. ``"planner"``)
            ``task``  — natural-language task description
            ``deps``  — list of dependency node ids (may be empty)

        Args:
            node_specs: List of node spec dicts (see above).
            dag_id: ID assigned to the resulting ``DAGGraph``.
            use_stubs: If ``True``, use stub runners instead of real agents.

        Raises:
            ValueError: On missing/invalid fields or unknown roles.
        """
        nodes: list[TaskNode] = []
        for spec in node_specs:
            nid = spec.get("id") or spec.get("task_id")
            role = spec.get("role")
            task_desc = spec.get("task") or spec.get("task_description", "")
            deps = tuple(spec.get("deps") or spec.get("dependencies") or [])

            if not nid:
                raise ValueError(f"Node spec missing 'id': {spec}")
            if not role:
                raise ValueError(f"Node '{nid}' missing 'role'")

            if use_stubs or self._router is None:
                runner = make_stub_runner(task_desc)
            else:
                runner = make_runner(role, task_desc, self._router)

            nodes.append(TaskNode(task_id=nid, dependencies=deps, runner=runner))

        return DAGGraph(nodes=tuple(nodes), dag_id=dag_id)

    # ── internal ──────────────────────────────────────────────────────────────

    async def _run(
        self,
        dag_id: str,
        graph: DAGGraph,
        channel: DagChannel,
        initial_context: dict[str, Any],
    ) -> None:
        await channel.set_status("running")
        try:
            result = await self._runner.run(graph, initial_context)
            state = "done" if result.success else "failed"
            await channel.set_status(
                state,
                node_states={
                    nid: ("skipped" if nid in result.skipped else
                          "error" if nid in result.errors else "done")
                    for nid in result.execution_order
                },
            )
            await channel.set_result(
                results={k: str(v) for k, v in result.results.items()},
                errors={k: str(v) for k, v in result.errors.items()},
            )
            log.info("dag_manager: dag_id=%s finished state=%s", dag_id, state)
        except asyncio.CancelledError:
            await channel.set_status("cancelled")
            log.info("dag_manager: dag_id=%s cancelled", dag_id)
            raise
        except Exception as exc:
            await channel.set_status("failed")
            log.exception("dag_manager: dag_id=%s unhandled error: %s", dag_id, exc)

    def _cleanup(self, dag_id: str) -> None:
        self._tasks.pop(dag_id, None)
        log.debug("dag_manager: cleaned up dag_id=%s", dag_id)

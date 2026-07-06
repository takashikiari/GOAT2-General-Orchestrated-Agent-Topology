"""workflow.dag_manager — lifecycle manager for background DAG runs.

``DagManager`` owns the set of currently running ``asyncio.Task`` s, one
per active DAG.  It bridges ``WorkflowRunner`` (pure execution) with
``DagChannel`` (Redis state/comms) and ``AgentRouter`` (agent resolution).

Design contract:
- No singleton.  One ``DagManager`` per application scope.
- The manager never touches the LLM directly — that is the agents' job.
- On completion or failure the manager writes final state to ``DagChannel``
  and closes the channel connection.

Restart capability
------------------
``DagManager.restart_failed(dag_id)`` re-runs only the nodes that failed
plus their descendants, seeding the new run with outputs from nodes that
already completed successfully.  It requires the original node specs to have
been persisted (automatic when the DAG was started via ``build_graph`` +
``start``).  The subgraph is computed by topological reachability from failed
nodes; cross-boundary dependency edges are removed and their results injected
into the initial context so the subgraph is self-contained.

Streaming
---------
``on_node_change`` (optional constructor arg) is an async callback fired on
every node state transition.  It is the in-process counterpart to the Redis
pub/sub events published by ``DagChannel.publish_event``.  Use it so the
Telegram bot or orchestrator can push "researcher done, coder running" updates
to the user without polling ``workflow_status``.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from workflow.agent_node import make_runner, make_stub_runner
from workflow.dag_channel import DagChannel
from workflow.errors import WorkflowError, WorkflowNotFound
from workflow.models import DAGGraph, TaskNode
from workflow.routing import AgentRouter
from workflow.runner import WorkflowRunner

log = logging.getLogger("goat2.workflow.dag_manager")

ChannelFactory = Callable[[str], DagChannel]
CompletionCallback = Callable[[str, str, str, dict], Awaitable[None]]
"""Signature: async (dag_id, chat_id, state, result) -> None"""

NodeChangeCallback = Callable[[str, str, str, dict[str, str]], Awaitable[None]]
"""Signature: async (dag_id, node_id, new_state, all_node_states) -> None"""


class DagManager:
    """Manages the lifecycle of background DAG ``asyncio.Task`` s.

    Args:
        runner: Shared ``WorkflowRunner`` instance (stateless, reusable).
        channel_factory: Callable ``(dag_id: str) -> DagChannel``.
        router: ``AgentRouter`` for resolving agent roles to instances.
            If ``None``, stub runners are used (useful for testing).
        on_complete: Optional async callback invoked when a DAG finishes.
            Signature: ``async (dag_id, chat_id, state, result) -> None``.
            ``state`` is ``"done"`` or ``"failed"``.
        on_node_change: Optional async callback fired on every node state
            transition *during* a run.
            Signature: ``async (dag_id, node_id, new_state, all_node_states) -> None``.
            States: ``running``, ``retrying``, ``done``, ``error``, ``skipped``.
            Exceptions are swallowed; the DAG run is never affected.
    """

    def __init__(
        self,
        runner: WorkflowRunner,
        channel_factory: ChannelFactory,
        router: AgentRouter | None = None,
        on_complete: CompletionCallback | None = None,
        on_node_change: NodeChangeCallback | None = None,
    ) -> None:
        self._runner = runner
        self._channel_factory = channel_factory
        self._router = router
        self._on_complete = on_complete
        self._on_node_change = on_node_change
        self._tasks: dict[str, asyncio.Task] = {}
        self._channels: dict[str, DagChannel] = {}
        # Temporary store: id(graph) -> raw node_specs, consumed by start().
        # Keyed by object identity so different graphs with the same dag_id
        # don't collide.  Entries are always consumed (pop'd) in start().
        self._pending_specs: dict[int, list[dict[str, Any]]] = {}

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
            graph: The DAG to run.  Build it with ``build_graph`` so node
                specs are automatically persisted for restart capability.
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

        # Consume specs stored by build_graph (if any).
        node_specs = self._pending_specs.pop(id(graph), None)

        task = asyncio.create_task(
            self._run(effective_id, graph, channel, initial_context or {}, node_specs),
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

    async def restart_failed(self, dag_id: str) -> str:
        """Re-run only the failed nodes and their descendants.

        Reads the last known state from Redis, identifies nodes that ended in
        ``error``, computes their reachable descendants (the *affected
        subgraph*), and launches a new DAG run with:

        - Only the affected nodes (cross-boundary deps removed).
        - Completed node results injected as initial context so the
          subgraph's boundary nodes receive the same inputs they would have
          had in the original run.

        Returns:
            The new ``dag_id`` for the restarted subgraph run.

        Raises:
            WorkflowNotFound: If ``dag_id`` has no persisted state in Redis.
            WorkflowError: If no failed nodes exist or graph spec is missing.
        """
        channel = self._channel_factory(dag_id)
        try:
            status = await channel.get_status()
            if status is None:
                raise WorkflowNotFound(dag_id, registered=list(self._tasks))

            node_states: dict[str, str] = status.get("node_states", {})
            failed_ids = {nid for nid, st in node_states.items() if st == "error"}
            if not failed_ids:
                raise WorkflowError(
                    f"No failed nodes in DAG '{dag_id}'; nothing to restart.",
                    context={"node_states": node_states},
                )

            node_specs = await channel.get_graph_spec()
            if node_specs is None:
                raise WorkflowError(
                    f"Graph spec not persisted for DAG '{dag_id}'. "
                    "Restart requires the DAG to have been started via build_graph().",
                )

            result = await channel.get_result() or {}
            completed_results: dict[str, Any] = {
                nid: val
                for nid, val in result.get("results", {}).items()
            }

            affected = self._compute_affected_subgraph(node_specs, failed_ids)
            subgraph_specs = self._build_subgraph_specs(node_specs, affected)

            # Seed context: completed nodes whose results the subgraph depends on.
            seed_context = {
                nid: val
                for nid, val in completed_results.items()
                if nid not in affected
            }

            suffix = str(int(time.time()) % 100000)
            new_dag_id = f"{dag_id}-r{suffix}"
            graph = self.build_graph(subgraph_specs, dag_id=new_dag_id)
            launched = self.start(graph, seed_context, dag_id=new_dag_id)
            log.info(
                "dag_manager: restarted dag_id=%s as dag_id=%s failed=%s affected=%s",
                dag_id, launched, sorted(failed_ids), sorted(affected),
            )
            return launched
        finally:
            await channel.close()

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

        Per-node execution options (all optional):
            ``timeout``     — float, per-node timeout in seconds
            ``max_retries`` — int, extra attempts on failure (default 0)
            ``retry_delay`` — float, seconds between retries (default 0.0)

        The raw ``node_specs`` list is stashed by object identity so that
        ``start()`` can persist it to Redis for restart capability.

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

            nodes.append(TaskNode(
                task_id=nid,
                dependencies=deps,
                runner=runner,
                timeout=spec.get("timeout"),
                max_retries=int(spec.get("max_retries", 0)),
                retry_delay=float(spec.get("retry_delay", 0.0)),
            ))

        graph = DAGGraph(nodes=tuple(nodes), dag_id=dag_id)
        # Stash specs by object identity; consumed (pop'd) in start().
        self._pending_specs[id(graph)] = node_specs
        return graph

    # ── subgraph helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _compute_affected_subgraph(
        node_specs: list[dict[str, Any]],
        failed_ids: set[str],
    ) -> set[str]:
        """BFS from failed nodes through the forward adjacency to find descendants.

        Returns the set of nodes that must be re-run: every failed node and
        every node that (transitively) depends on at least one failed node.
        """
        children: dict[str, list[str]] = {spec["id"]: [] for spec in node_specs}
        for spec in node_specs:
            for dep in spec.get("deps") or []:
                if dep in children:
                    children[dep].append(spec["id"])

        affected: set[str] = set(failed_ids)
        queue = list(failed_ids)
        while queue:
            nid = queue.pop()
            for child in children.get(nid, []):
                if child not in affected:
                    affected.add(child)
                    queue.append(child)
        return affected

    @staticmethod
    def _build_subgraph_specs(
        node_specs: list[dict[str, Any]],
        subgraph_ids: set[str],
    ) -> list[dict[str, Any]]:
        """Return specs for nodes in ``subgraph_ids`` with cross-boundary deps removed.

        Dependencies pointing to nodes outside the subgraph are dropped
        because those results are provided via the seed initial_context.
        """
        result: list[dict[str, Any]] = []
        for spec in node_specs:
            if spec["id"] not in subgraph_ids:
                continue
            original_deps = list(spec.get("deps") or [])
            intra_deps = [d for d in original_deps if d in subgraph_ids]
            result.append({**spec, "deps": intra_deps})
        return result

    # ── internal ──────────────────────────────────────────────────────────────

    def _make_node_change_handler(
        self,
        dag_id: str,
        channel: DagChannel,
    ) -> "Callable[[str, str, dict[str, str]], Awaitable[None]]":
        """Build the per-DAG node-change callback threaded into WorkflowRunner.

        On each node transition the handler:
        1. Publishes a Redis pub/sub event (``channel.publish_event``).
        2. Calls the manager-level ``on_node_change`` callback if registered.
        """
        async def _handler(
            node_id: str,
            state: str,
            node_states: dict[str, str],
        ) -> None:
            # pub/sub already called by runner._notify; this is the manager-level hook
            if self._on_node_change is not None:
                try:
                    await self._on_node_change(dag_id, node_id, state, node_states)
                except Exception as exc:
                    log.debug(
                        "dag_manager: on_node_change callback error dag=%s node=%s: %s",
                        dag_id, node_id, exc,
                    )

        return _handler

    async def _run(
        self,
        dag_id: str,
        graph: DAGGraph,
        channel: DagChannel,
        initial_context: dict[str, Any],
        node_specs: list[dict[str, Any]] | None,
    ) -> None:
        from config.settings import DAG_WORKSPACE
        DAG_WORKSPACE.mkdir(parents=True, exist_ok=True)
        log.debug("dag_manager: sandbox root=%s", DAG_WORKSPACE)

        # Persist graph spec to Redis before running so restart_failed() works
        # even after a process restart (the spec survives in Redis with TTL).
        if node_specs is not None:
            try:
                await channel.set_graph_spec(node_specs)
            except Exception as exc:
                log.warning("dag_manager: could not persist graph spec: %s", exc)

        ctx = dict(initial_context)
        ctx["__dag_channel__"] = channel
        ctx["__dag_workspace__"] = DAG_WORKSPACE

        node_change_cb = self._make_node_change_handler(dag_id, channel)

        await channel.set_status("running")
        try:
            result = await self._runner.run(graph, ctx, on_node_change=node_change_cb)
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
            log.info(
                "dag_manager: dag_id=%s finished state=%s confidence=%.4f",
                dag_id, state, result.confidence_score,
            )
        except asyncio.CancelledError:
            await channel.set_status("cancelled")
            log.info("dag_manager: dag_id=%s cancelled", dag_id)
            raise
        except Exception as exc:
            await channel.set_status("failed")
            log.exception("dag_manager: dag_id=%s unhandled error: %s", dag_id, exc)
        finally:
            if self._on_complete is not None:
                chat_id = str(initial_context.get("chat_id", ""))
                status = await channel.get_status() or {}
                state = status.get("state", "unknown")
                res = await channel.get_result() or {}
                try:
                    await self._on_complete(dag_id, chat_id, state, res)
                except Exception as cb_exc:
                    log.warning("dag_manager: on_complete callback error: %s", cb_exc)

    def _cleanup(self, dag_id: str) -> None:
        self._tasks.pop(dag_id, None)
        log.debug("dag_manager: cleaned up dag_id=%s", dag_id)

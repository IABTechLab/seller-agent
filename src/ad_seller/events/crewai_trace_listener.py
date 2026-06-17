# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""CrewAI execution-trace listener for the buyer agent.

Subscribes to crewai_event_bus (the same global bus that powers verbose=True
console output) and forwards structured JSON payloads to a caller-supplied
sink so they can be streamed to the playground dashboard in real time.

The sink is called synchronously from whatever thread CrewAI is running in,
so it must be thread-safe. Typical usage:

    loop = asyncio.get_running_loop()
    listener = CrewAITraceListener(
        sink=lambda d: loop.call_soon_threadsafe(queue.put_nowait, d)
    )
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from crewai.events.base_event_listener import BaseEventListener
from crewai.events.event_bus import CrewAIEventsBus
from crewai.events.types.agent_events import (
    AgentExecutionCompletedEvent,
    AgentExecutionStartedEvent,
)
from crewai.events.types.crew_events import (
    CrewKickoffCompletedEvent,
    CrewKickoffFailedEvent,
    CrewKickoffStartedEvent,
)
from crewai.events.types.flow_events import (
    FlowFinishedEvent,
    FlowStartedEvent,
    MethodExecutionFailedEvent,
    MethodExecutionFinishedEvent,
    MethodExecutionStartedEvent,
)
from crewai.events.types.llm_events import (
    LLMCallCompletedEvent,
    LLMCallStartedEvent,
    LLMStreamChunkEvent,
)
from crewai.events.types.task_events import (
    TaskCompletedEvent,
    TaskFailedEvent,
    TaskStartedEvent,
)
from crewai.events.types.tool_usage_events import (
    ToolUsageFinishedEvent,
    ToolUsageStartedEvent,
)

logger = logging.getLogger(__name__)

Sink = Callable[[dict[str, Any]], None]


def _ts(event: Any) -> str:
    try:
        return event.timestamp.isoformat()
    except Exception:
        return ""


class CrewAITraceListener(BaseEventListener):
    """Bridges crewai_event_bus events to a thread-safe sink.

    Instantiate once at app startup — the constructor calls setup_listeners()
    which registers all handlers on the global crewai_event_bus singleton.

    LLM stream chunks (token-by-token) are suppressed by default to avoid
    flooding the stream; set include_llm_chunks=True to enable.
    """

    def __init__(self, sink: Sink, *, include_llm_chunks: bool = False) -> None:
        self._sink = sink
        self._include_llm_chunks = include_llm_chunks
        super().__init__()

    def _emit(
        self,
        category: str,
        type_: str,
        summary: str,
        detail: dict[str, Any],
        ts: str = "",
    ) -> None:
        try:
            self._sink({
                "layer": "crewai",
                "category": category,
                "type": type_,
                "ts": ts,
                "summary": summary,
                "detail": detail,
            })
        except Exception:
            logger.debug("CrewAITraceListener sink error", exc_info=True)

    def setup_listeners(self, crewai_event_bus: CrewAIEventsBus) -> None:  # noqa: D401

        # ── Flow ──────────────────────────────────────────────────────────
        @crewai_event_bus.on(FlowStartedEvent)
        def on_flow_started(source: Any, event: FlowStartedEvent) -> None:
            self._emit("flow", event.type, f"Flow: {event.flow_name} started", {
                "flow_name": event.flow_name,
            }, _ts(event))

        @crewai_event_bus.on(FlowFinishedEvent)
        def on_flow_finished(source: Any, event: FlowFinishedEvent) -> None:
            self._emit("flow", event.type, f"Flow: {event.flow_name} finished", {
                "flow_name": event.flow_name,
            }, _ts(event))

        @crewai_event_bus.on(MethodExecutionStartedEvent)
        def on_method_started(source: Any, event: MethodExecutionStartedEvent) -> None:
            self._emit("flow_step", event.type, f"▶ {event.method_name}", {
                "flow_name": event.flow_name,
                "method_name": event.method_name,
            }, _ts(event))

        @crewai_event_bus.on(MethodExecutionFinishedEvent)
        def on_method_finished(source: Any, event: MethodExecutionFinishedEvent) -> None:
            self._emit("flow_step", event.type, f"✓ {event.method_name}", {
                "flow_name": event.flow_name,
                "method_name": event.method_name,
            }, _ts(event))

        @crewai_event_bus.on(MethodExecutionFailedEvent)
        def on_method_failed(source: Any, event: MethodExecutionFailedEvent) -> None:
            self._emit("flow_step", event.type, f"✗ {event.method_name}: {event.error}", {
                "flow_name": event.flow_name,
                "method_name": event.method_name,
                "error": str(event.error),
            }, _ts(event))

        # ── Crew ──────────────────────────────────────────────────────────
        @crewai_event_bus.on(CrewKickoffStartedEvent)
        def on_crew_started(source: Any, event: CrewKickoffStartedEvent) -> None:
            self._emit("crew", event.type, f"Crew: {event.crew_name} kicked off", {
                "crew_name": event.crew_name,
            }, _ts(event))

        @crewai_event_bus.on(CrewKickoffCompletedEvent)
        def on_crew_completed(source: Any, event: CrewKickoffCompletedEvent) -> None:
            self._emit("crew", event.type, f"Crew: {event.crew_name} completed", {
                "crew_name": event.crew_name,
                "total_tokens": event.total_tokens,
            }, _ts(event))

        @crewai_event_bus.on(CrewKickoffFailedEvent)
        def on_crew_failed(source: Any, event: CrewKickoffFailedEvent) -> None:
            self._emit("crew", event.type, f"Crew: {event.crew_name} failed", {
                "crew_name": event.crew_name,
            }, _ts(event))

        # ── Task ──────────────────────────────────────────────────────────
        @crewai_event_bus.on(TaskStartedEvent)
        def on_task_started(source: Any, event: TaskStartedEvent) -> None:
            self._emit("task", event.type, f"Task started ({event.agent_role or ''})", {
                "task_id": event.task_id,
                "task_name": event.task_name,
                "agent_role": event.agent_role,
            }, _ts(event))

        @crewai_event_bus.on(TaskCompletedEvent)
        def on_task_completed(source: Any, event: TaskCompletedEvent) -> None:
            output_preview = ""
            try:
                output_preview = str(event.output.raw)[:500] if event.output else ""
            except Exception:
                pass
            self._emit("task", event.type, f"Task completed ({event.agent_role or ''})", {
                "task_id": event.task_id,
                "task_name": event.task_name,
                "agent_role": event.agent_role,
                "output_preview": output_preview,
            }, _ts(event))

        @crewai_event_bus.on(TaskFailedEvent)
        def on_task_failed(source: Any, event: TaskFailedEvent) -> None:
            self._emit("task", event.type, f"Task failed ({event.agent_role or ''})", {
                "task_id": event.task_id,
                "task_name": event.task_name,
                "agent_role": event.agent_role,
            }, _ts(event))

        # ── Agent ─────────────────────────────────────────────────────────
        @crewai_event_bus.on(AgentExecutionStartedEvent)
        def on_agent_started(source: Any, event: AgentExecutionStartedEvent) -> None:
            role = event.agent_role or ""
            try:
                role = role or (event.agent.role if event.agent else "")
            except Exception:
                pass
            self._emit("agent", event.type, f"Agent thinking: {role}", {
                "agent_role": role,
                "agent_id": event.agent_id,
            }, _ts(event))

        @crewai_event_bus.on(AgentExecutionCompletedEvent)
        def on_agent_completed(source: Any, event: AgentExecutionCompletedEvent) -> None:
            role = event.agent_role or ""
            try:
                role = role or (event.agent.role if event.agent else "")
            except Exception:
                pass
            self._emit("agent", event.type, f"Agent done: {role}", {
                "agent_role": role,
                "agent_id": event.agent_id,
                "output_preview": str(event.output)[:500] if event.output else "",
            }, _ts(event))

        # ── Tool ──────────────────────────────────────────────────────────
        @crewai_event_bus.on(ToolUsageStartedEvent)
        def on_tool_started(source: Any, event: ToolUsageStartedEvent) -> None:
            args_preview = str(event.tool_args)[:300] if event.tool_args else ""
            self._emit("tool", event.type, f"Tool: {event.tool_name}", {
                "tool_name": event.tool_name,
                "agent_role": event.agent_role,
                "args_preview": args_preview,
            }, _ts(event))

        @crewai_event_bus.on(ToolUsageFinishedEvent)
        def on_tool_finished(source: Any, event: ToolUsageFinishedEvent) -> None:
            self._emit("tool", event.type, f"Tool done: {event.tool_name}", {
                "tool_name": event.tool_name,
                "agent_role": event.agent_role,
                "from_cache": event.from_cache,
                "output_preview": str(event.output)[:500] if event.output else "",
            }, _ts(event))

        # ── LLM ───────────────────────────────────────────────────────────
        @crewai_event_bus.on(LLMCallStartedEvent)
        def on_llm_started(source: Any, event: LLMCallStartedEvent) -> None:
            self._emit("llm", event.type, "LLM call started", {
                "agent_role": event.agent_role,
                "agent_id": event.agent_id,
            }, _ts(event))

        @crewai_event_bus.on(LLMCallCompletedEvent)
        def on_llm_completed(source: Any, event: LLMCallCompletedEvent) -> None:
            self._emit("llm", event.type, "LLM call completed", {
                "agent_role": event.agent_role,
                "agent_id": event.agent_id,
                "call_type": str(event.call_type) if event.call_type else "",
            }, _ts(event))

        if self._include_llm_chunks:
            @crewai_event_bus.on(LLMStreamChunkEvent)
            def on_llm_chunk(source: Any, event: LLMStreamChunkEvent) -> None:
                self._emit("llm_chunk", event.type, event.chunk, {
                    "chunk": event.chunk,
                    "agent_role": event.agent_role,
                }, _ts(event))

"""Trace collector for the Otic IT Support Agent.

Records every agent step — thoughts, tool calls, observations, reflections,
errors, and final responses — as structured TraceStep objects. The Streamlit
UI reads these traces to render a real-time observability panel.
"""

import time
import logging
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class StepType(str, Enum):
    """Classification for each step in the agent trace."""

    THOUGHT = "thought"
    ACTION = "action"
    OBSERVATION = "observation"
    REFLECTION = "reflection"
    ERROR = "error"
    SAFETY_CHECK = "safety_check"
    RAG_RETRIEVAL = "rag_retrieval"
    MEMORY_LOAD = "memory_load"
    FINAL_RESPONSE = "final_response"


@dataclass
class TraceStep:
    """A single recorded step in the agent's execution.

    Attributes:
        step_type: Classification of the step (thought, action, etc.).
        content: Human-readable description of what happened.
        timestamp: Unix timestamp when the step was recorded.
        duration_ms: Duration of the step in milliseconds (for tool calls).
        tool_name: Name of the tool called (only for ACTION/OBSERVATION steps).
        tool_input: Arguments passed to the tool (only for ACTION steps).
        tool_output: Result returned by the tool (only for OBSERVATION steps).
        iteration: Which ReAct loop iteration this step belongs to (1-based).
        metadata: Arbitrary extra data (error details, risk scores, etc.).
    """

    step_type: StepType
    content: str
    timestamp: float = field(default_factory=time.time)
    duration_ms: float | None = None
    tool_name: str | None = None
    tool_input: dict[str, Any] | None = None
    tool_output: dict[str, Any] | None = None
    iteration: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to a plain dict for JSON serialisation and Streamlit rendering.

        Returns:
            Dictionary with all fields. The step_type is converted to its
            string value for readability.
        """
        data = asdict(self)
        data["step_type"] = self.step_type.value
        return data


class Tracer:
    """Collects and manages trace steps for a single agent invocation.

    Usage in the agent loop::

        tracer = Tracer()
        tracer.thought("User reports VPN issues...", iteration=1)
        tracer.action("status_check", {"service_name": "vpn-gateway"}, iteration=1)
        # ... execute tool ...
        tracer.observation("status_check", result, duration_ms=45.2, iteration=1)
        tracer.reflection("VPN gateway is healthy, checking logs next...", iteration=1)
        # ... more iterations ...
        tracer.final_response("Based on my investigation...")

    The Streamlit UI calls ``tracer.export()`` to get all steps as dicts.
    """

    def __init__(self, session_id: str | None = None) -> None:
        """Initialise a new tracer.

        Args:
            session_id: Optional session identifier for correlating traces.
        """
        self._steps: list[TraceStep] = []
        self.session_id = session_id
        self._start_time: float = time.time()
        self._pending_action_start: float | None = None

    @property
    def steps(self) -> list[TraceStep]:
        """Read-only access to the collected trace steps."""
        return list(self._steps)

    @property
    def step_count(self) -> int:
        """Number of steps recorded so far."""
        return len(self._steps)

    def _add(self, step: TraceStep) -> None:
        """Append a step and log it."""
        self._steps.append(step)
        logger.debug(
            "Trace [%s] iter=%s: %s",
            step.step_type.value,
            step.iteration,
            step.content[:120],
        )

    # ----- Convenience recorders for each step type -----

    def thought(self, content: str, iteration: int) -> None:
        """Record a THOUGHT step — the agent's reasoning.

        Args:
            content: The agent's reasoning text.
            iteration: Current ReAct loop iteration (1-based).
        """
        self._add(TraceStep(
            step_type=StepType.THOUGHT,
            content=content,
            iteration=iteration,
        ))

    def action(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        iteration: int,
    ) -> None:
        """Record an ACTION step — a tool call about to be executed.

        Also starts a timer so the subsequent observation can report duration.

        Args:
            tool_name: Name of the tool being called.
            tool_input: Arguments being passed to the tool.
            iteration: Current ReAct loop iteration (1-based).
        """
        self._pending_action_start = time.time()
        self._add(TraceStep(
            step_type=StepType.ACTION,
            content=f"Calling tool: {tool_name}",
            tool_name=tool_name,
            tool_input=tool_input,
            iteration=iteration,
        ))

    def observation(
        self,
        tool_name: str,
        tool_output: dict[str, Any],
        iteration: int,
        duration_ms: float | None = None,
    ) -> None:
        """Record an OBSERVATION step — the result of a tool call.

        If ``duration_ms`` is not provided and an action timer is running,
        the duration is calculated automatically.

        Args:
            tool_name: Name of the tool that returned results.
            tool_output: The structured dict returned by the tool.
            iteration: Current ReAct loop iteration (1-based).
            duration_ms: Explicit duration override in milliseconds.
        """
        if duration_ms is None and self._pending_action_start is not None:
            duration_ms = (time.time() - self._pending_action_start) * 1000
        self._pending_action_start = None

        # Build a short summary of the output for the content field
        summary = _summarise_tool_output(tool_name, tool_output)

        self._add(TraceStep(
            step_type=StepType.OBSERVATION,
            content=summary,
            tool_name=tool_name,
            tool_output=tool_output,
            duration_ms=duration_ms,
            iteration=iteration,
        ))

    def reflection(self, content: str, iteration: int) -> None:
        """Record a REFLECTION step — the agent's assessment after observing.

        Args:
            content: The agent's reflection text.
            iteration: Current ReAct loop iteration (1-based).
        """
        self._add(TraceStep(
            step_type=StepType.REFLECTION,
            content=content,
            iteration=iteration,
        ))

    def error(
        self,
        content: str,
        iteration: int | None = None,
        error_type: str | None = None,
        tool_name: str | None = None,
    ) -> None:
        """Record an ERROR step — a tool failure or unexpected exception.

        Args:
            content: Description of the error.
            iteration: Current ReAct loop iteration (None if outside the loop).
            error_type: Exception class name (e.g., "LogSearchError").
            tool_name: Tool that caused the error, if applicable.
        """
        metadata: dict[str, Any] = {}
        if error_type:
            metadata["error_type"] = error_type
        self._add(TraceStep(
            step_type=StepType.ERROR,
            content=content,
            iteration=iteration,
            tool_name=tool_name,
            metadata=metadata,
        ))

    def safety_check(self, content: str, result: dict[str, Any]) -> None:
        """Record a SAFETY_CHECK step — prompt injection or data filtering.

        Args:
            content: Description of what was checked.
            result: The safety check result dict (risk_score, is_safe, etc.).
        """
        self._add(TraceStep(
            step_type=StepType.SAFETY_CHECK,
            content=content,
            metadata=result,
        ))

    def rag_retrieval(
        self,
        query: str,
        num_results: int,
        sources: list[str],
    ) -> None:
        """Record a RAG_RETRIEVAL step — knowledge base context retrieval.

        Args:
            query: The search query sent to the RAG retriever.
            num_results: Number of chunks returned.
            sources: List of runbook source filenames.
        """
        self._add(TraceStep(
            step_type=StepType.RAG_RETRIEVAL,
            content=f"RAG query: '{query}' — {num_results} results from {sources}",
            metadata={"query": query, "num_results": num_results, "sources": sources},
        ))

    def memory_load(self, session_id: str, turns_loaded: int, incidents: int) -> None:
        """Record a MEMORY_LOAD step — conversation context retrieval.

        Args:
            session_id: The session whose context was loaded.
            turns_loaded: Number of conversation turns loaded.
            incidents: Number of active incidents loaded.
        """
        self._add(TraceStep(
            step_type=StepType.MEMORY_LOAD,
            content=(
                f"Loaded context for session {session_id}: "
                f"{turns_loaded} turns, {incidents} active incidents"
            ),
            metadata={
                "session_id": session_id,
                "turns_loaded": turns_loaded,
                "incidents": incidents,
            },
        ))

    def final_response(self, content: str) -> None:
        """Record the FINAL_RESPONSE step — the agent's answer to the user.

        Args:
            content: The final response text (or a truncated preview of it).
        """
        total_ms = (time.time() - self._start_time) * 1000
        self._add(TraceStep(
            step_type=StepType.FINAL_RESPONSE,
            content=content[:500],
            duration_ms=total_ms,
            metadata={"total_agent_duration_ms": total_ms},
        ))

    # ----- Export methods -----

    def export(self) -> list[dict[str, Any]]:
        """Export all trace steps as a list of dicts for the Streamlit UI.

        Returns:
            List of step dicts, each with step_type as a string value.
        """
        return [step.to_dict() for step in self._steps]

    def export_summary(self) -> dict[str, Any]:
        """Export a high-level summary of the trace.

        Returns:
            Dict with session_id, total_steps, step_type_counts,
            tool_calls (names and durations), total_duration_ms,
            had_errors, and iterations_used.
        """
        type_counts: dict[str, int] = {}
        tool_calls: list[dict[str, Any]] = []
        max_iteration = 0
        had_errors = False

        for step in self._steps:
            type_name = step.step_type.value
            type_counts[type_name] = type_counts.get(type_name, 0) + 1

            if step.step_type == StepType.ACTION and step.tool_name:
                tool_calls.append({
                    "tool": step.tool_name,
                    "iteration": step.iteration,
                })

            if step.step_type == StepType.OBSERVATION and step.duration_ms is not None:
                # Attach duration to the matching tool call
                for tc in reversed(tool_calls):
                    if tc["tool"] == step.tool_name and "duration_ms" not in tc:
                        tc["duration_ms"] = round(step.duration_ms, 2)
                        break

            if step.step_type == StepType.ERROR:
                had_errors = True

            if step.iteration is not None and step.iteration > max_iteration:
                max_iteration = step.iteration

        total_ms = (time.time() - self._start_time) * 1000

        return {
            "session_id": self.session_id,
            "total_steps": len(self._steps),
            "step_type_counts": type_counts,
            "tool_calls": tool_calls,
            "total_duration_ms": round(total_ms, 2),
            "had_errors": had_errors,
            "iterations_used": max_iteration,
        }

    def clear(self) -> None:
        """Reset the tracer for a new invocation within the same session."""
        self._steps.clear()
        self._start_time = time.time()
        self._pending_action_start = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _summarise_tool_output(tool_name: str, output: dict[str, Any]) -> str:
    """Create a short human-readable summary of a tool's output.

    Args:
        tool_name: Name of the tool.
        output: The structured dict returned by the tool.

    Returns:
        A concise one-line summary.
    """
    if tool_name == "status_check":
        summary = output.get("summary", {})
        return (
            f"Status check: {summary.get('healthy', 0)} healthy, "
            f"{summary.get('degraded', 0)} degraded, "
            f"{summary.get('down', 0)} down"
        )

    if tool_name == "server_metrics":
        alerts = output.get("alerts", [])
        hostname = output.get("metrics", {}).get("hostname", "unknown")
        if isinstance(output.get("metrics"), dict) and "hostname" not in output.get("metrics", {}):
            # All-server query
            return f"Metrics: {len(alerts)} alert(s) across servers"
        return f"Metrics for {hostname}: {len(alerts)} alert(s)"

    if tool_name == "log_search":
        total = output.get("total_results", 0)
        return f"Log search: {total} matching entries found"

    if tool_name == "kb_search":
        total = output.get("total_results", 0)
        query = output.get("query", "")
        return f"KB search for '{query}': {total} results"

    if tool_name == "create_ticket":
        status = output.get("status", "unknown")
        if status == "confirmation_required":
            preview = output.get("ticket_preview", {})
            return f"Ticket preview: {preview.get('title', 'untitled')} [{preview.get('priority', '?')}]"
        ticket_id = output.get("ticket", {}).get("ticket_id", "unknown")
        return f"Ticket created: {ticket_id}"

    # Fallback for unknown tools
    return f"Tool '{tool_name}' returned {len(output)} field(s)"

"""Core ReAct agent loop for the Otic IT Support Agent.

This module orchestrates one full user turn:
1) input safety checks
2) memory context loading
3) RAG retrieval grounding
4) ReAct loop with Anthropic tool_use blocks
5) tool dispatch + retry/fallback handling
6) full trace emission (with optional live callback)
7) output filtering
8) memory persistence
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Callable

from anthropic import Anthropic
from anthropic.types import ToolUseBlock

from agent.prompts import get_system_prompt, get_tool_schemas_for_api
from agent.safety import filter_sensitive_output, run_safety_checks
from memory.store import MemoryStore
from observability.tracer import Tracer
from tools.registry import execute_tool

logger = logging.getLogger(__name__)

MAX_REACT_ITERATIONS = 5
DEFAULT_MODEL = "claude-sonnet-4-5"
DEFAULT_TEMPERATURE = 0.3
DEFAULT_MAX_TOKENS = 1200

StepCallback = Callable[[dict[str, Any]], None]

_CONFIRMATION_PATTERN = re.compile(
    r"\b(yes|confirm|confirmed|approved|proceed|go ahead|create ticket)\b",
    re.IGNORECASE,
)

try:
    from rag.retriever import RetrievalError, retrieve, setup_rag

    _RAG_IMPORT_ERROR: Exception | None = None
except Exception as exc:  # pragma: no cover - environment-specific dependency path
    RetrievalError = Exception  # type: ignore[assignment]
    retrieve = None
    setup_rag = None
    _RAG_IMPORT_ERROR = exc


@dataclass
class AgentTurnResult:
    """Output payload for one user turn processed by the agent."""

    session_id: str
    response_text: str
    traces: list[dict[str, Any]]
    blocked: bool
    iterations_used: int
    tool_failures: list[dict[str, Any]]
    safety: dict[str, Any]


class AgentCore:
    """ReAct orchestrator for IT incident diagnosis and resolution."""

    def __init__(
        self,
        client: Anthropic | None = None,
        memory_store: MemoryStore | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_iterations: int | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        initialize_rag: bool = True,
    ) -> None:
        self.client = client or Anthropic()
        self.memory_store = memory_store or MemoryStore()
        self.model = model or os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL)

        raw_temp = temperature
        if raw_temp is None:
            raw_temp = float(os.environ.get("AGENT_TEMPERATURE", str(DEFAULT_TEMPERATURE)))
        self.temperature = raw_temp

        raw_iterations = max_iterations
        if raw_iterations is None:
            raw_iterations = int(os.environ.get("AGENT_MAX_ITERATIONS", str(MAX_REACT_ITERATIONS)))
        self.max_iterations = max(1, min(raw_iterations, MAX_REACT_ITERATIONS))
        self.max_tokens = max_tokens

        self._tool_schemas = get_tool_schemas_for_api()
        self._system_prompt = get_system_prompt()

        if initialize_rag:
            if setup_rag is None:
                logger.warning("RAG setup unavailable: %s", _RAG_IMPORT_ERROR)
            else:
                try:
                    setup_rag()
                except Exception as exc:  # pragma: no cover - non-fatal startup path
                    logger.warning("RAG setup failed at startup: %s", exc)

    def run_turn(
        self,
        session_id: str,
        user_input: str,
        on_step: StepCallback | None = None,
    ) -> AgentTurnResult:
        """Execute one full user turn through the ReAct loop."""
        tracer = Tracer(session_id=session_id)
        tool_failures: list[dict[str, Any]] = []
        iterations_used = 0

        safety_result = run_safety_checks(user_input)
        tracer.safety_check("Input safety checks completed", safety_result)
        self._emit_step(tracer, on_step)

        filtered_user_input = safety_result["filtered_input"]["filtered_text"]

        if safety_result.get("should_block"):
            block_text = safety_result.get("block_reason", "Request blocked for safety reasons.")
            output_filter = filter_sensitive_output(block_text)
            tracer.safety_check("Output filtering completed", output_filter)
            self._emit_step(tracer, on_step)

            final_text = output_filter["filtered_text"]
            tracer.final_response(final_text)
            self._emit_step(tracer, on_step)
            self._persist_turns(session_id, filtered_user_input, final_text, tracer, on_step)

            return AgentTurnResult(
                session_id=session_id,
                response_text=final_text,
                traces=tracer.export(),
                blocked=True,
                iterations_used=0,
                tool_failures=tool_failures,
                safety=safety_result,
            )

        context = self._load_memory_context(session_id, tracer, on_step)
        rag_context = self._retrieve_rag_context(filtered_user_input, tracer, on_step)
        system_prompt = self._build_runtime_context(context, rag_context)
        messages = self._build_messages(context, filtered_user_input)

        final_response_text = ""

        for iteration in range(1, self.max_iterations + 1):
            iterations_used = iteration
            try:
                response = self.client.messages.create(
                    model=self.model,
                    system=system_prompt,
                    messages=messages,
                    tools=self._tool_schemas,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                )
            except Exception as exc:
                tracer.error(
                    content=f"Anthropic API call failed: {exc}",
                    iteration=iteration,
                    error_type=type(exc).__name__,
                )
                self._emit_step(tracer, on_step)
                final_response_text = (
                    "I hit an internal AI service error while investigating this incident. "
                    "Please retry in a moment."
                )
                break

            text_chunks: list[str] = []
            tool_blocks: list[ToolUseBlock] = []
            for block in response.content:
                block_type = getattr(block, "type", "")
                if block_type == "text":
                    text_value = getattr(block, "text", "")
                    if text_value:
                        text_chunks.append(text_value)
                elif block_type == "tool_use":
                    tool_blocks.append(block)

            cycle_text = "\n".join(text_chunks).strip()
            if cycle_text:
                tracer.thought(cycle_text, iteration=iteration)
                self._emit_step(tracer, on_step)

            if not tool_blocks:
                if cycle_text:
                    final_response_text = cycle_text
                else:
                    stop_reason = getattr(response, "stop_reason", None)
                    if stop_reason == "refusal":
                        final_response_text = (
                            "I cannot comply with that request. Please share a standard IT issue "
                            "and I can help diagnose it."
                        )
                    else:
                        final_response_text = (
                            "I completed my investigation but did not produce a textual final answer. "
                            "Please rephrase the incident and I will continue."
                        )
                break

            tool_result_blocks: list[dict[str, Any]] = []
            for block in tool_blocks:
                tool_result, is_error = self._execute_tool_with_retry_fallback(
                    tool_name=block.name,
                    arguments=dict(block.input),
                    user_input=filtered_user_input,
                    iteration=iteration,
                    tracer=tracer,
                    on_step=on_step,
                    tool_failures=tool_failures,
                )
                tool_result_blocks.append(
                    self._to_tool_result_block(
                        tool_use_id=block.id,
                        result=tool_result,
                        is_error=is_error,
                    )
                )

            messages.append(
                {
                    "role": "assistant",
                    "content": self._to_assistant_blocks(response.content),
                }
            )
            messages.append({"role": "user", "content": tool_result_blocks})

            tracer.reflection(
                "Tool results returned; continuing investigation.",
                iteration=iteration,
            )
            self._emit_step(tracer, on_step)

        if not final_response_text:
            final_response_text = self._build_iteration_cap_response(tool_failures)

        output_filter = filter_sensitive_output(final_response_text)
        tracer.safety_check("Output filtering completed", output_filter)
        self._emit_step(tracer, on_step)

        sanitized_response = output_filter["filtered_text"]
        tracer.final_response(sanitized_response)
        self._emit_step(tracer, on_step)

        self._persist_turns(session_id, filtered_user_input, sanitized_response, tracer, on_step)

        return AgentTurnResult(
            session_id=session_id,
            response_text=sanitized_response,
            traces=tracer.export(),
            blocked=False,
            iterations_used=iterations_used,
            tool_failures=tool_failures,
            safety=safety_result,
        )

    def _emit_step(self, tracer: Tracer, on_step: StepCallback | None) -> None:
        """Emit the latest trace step to the optional callback."""
        if on_step is None or tracer.step_count == 0:
            return
        try:
            on_step(tracer.steps[-1].to_dict())
        except Exception as exc:  # pragma: no cover - callback should never break agent flow
            logger.warning("Trace callback failed: %s", exc)

    def _build_runtime_context(
        self,
        memory_context: dict[str, Any],
        rag_context: dict[str, Any],
    ) -> str:
        """Append runtime context (memory + RAG) to the base system prompt."""
        incidents = memory_context.get("active_incidents", [])
        rag_results = rag_context.get("results", [])

        lines: list[str] = [self._system_prompt, "", "## Runtime Context", ""]

        if incidents:
            lines.append("### Active Incidents")
            for incident in incidents:
                lines.append(
                    "- {incident_id} [{status}] {summary}".format(
                        incident_id=incident.get("incident_id", "INC-UNKNOWN"),
                        status=incident.get("status", "unknown"),
                        summary=incident.get("summary") or "No summary",
                    )
                )
        else:
            lines.append("### Active Incidents")
            lines.append("- None")

        lines.append("")
        lines.append("### Retrieved Runbook Context")
        if rag_results:
            for idx, item in enumerate(rag_results, start=1):
                excerpt = (item.get("content", "") or "").strip().replace("\n", " ")
                excerpt = excerpt[:350]
                lines.append(
                    (
                        f"{idx}. {item.get('runbook', 'Unknown')} | "
                        f"{item.get('section', 'Unknown')} | "
                        f"{item.get('source_file', 'Unknown')} | "
                        f"score={item.get('relevance_score', 0)}"
                    )
                )
                lines.append(f"   Excerpt: {excerpt}")
        else:
            lines.append("- No relevant runbook context retrieved.")

        return "\n".join(lines)

    def _build_messages(
        self,
        memory_context: dict[str, Any],
        filtered_user_input: str,
    ) -> list[dict[str, Any]]:
        """Build Anthropic messages from memory turns plus current user turn."""
        messages: list[dict[str, Any]] = []
        for turn in memory_context.get("recent_turns", []):
            role = turn.get("role")
            content = turn.get("content", "")
            if role in {"user", "assistant"} and isinstance(content, str) and content:
                messages.append({"role": role, "content": content})

        messages.append({"role": "user", "content": filtered_user_input})
        return messages

    def _load_memory_context(
        self,
        session_id: str,
        tracer: Tracer,
        on_step: StepCallback | None,
    ) -> dict[str, Any]:
        """Load recent turns + active incidents from memory."""
        try:
            context = self.memory_store.load_context(session_id)
        except Exception as exc:
            tracer.error(
                content=f"Memory load failed: {exc}",
                error_type=type(exc).__name__,
            )
            self._emit_step(tracer, on_step)
            return {"recent_turns": [], "active_incidents": []}

        tracer.memory_load(
            session_id=session_id,
            turns_loaded=len(context.get("recent_turns", [])),
            incidents=len(context.get("active_incidents", [])),
        )
        self._emit_step(tracer, on_step)
        return context

    def _retrieve_rag_context(
        self,
        query: str,
        tracer: Tracer,
        on_step: StepCallback | None,
    ) -> dict[str, Any]:
        """Retrieve semantic runbook context, gracefully handling failures."""
        rag_result: dict[str, Any]
        if retrieve is None:
            # Runtime fallback for environments where ChromaDB is unavailable:
            # use kb_search tool to keep runbook grounding functional.
            try:
                kb_result = execute_tool("kb_search", {"query": query, "top_k": 3})
                rag_results = [
                    {
                        "content": item.get("content", ""),
                        "runbook": item.get("runbook", "Unknown"),
                        "section": item.get("section", "Unknown"),
                        "source_file": item.get("runbook_file", "Unknown"),
                        "relevance_score": item.get("relevance_score", 0.0),
                    }
                    for item in kb_result.get("results", [])
                ]
                rag_result = {
                    "results": rag_results,
                    "total_results": len(rag_results),
                    "query": query,
                }
                sources = [item.get("source_file", "Unknown") for item in rag_results]
                tracer.rag_retrieval(
                    query=query,
                    num_results=rag_result["total_results"],
                    sources=sources,
                )
                self._emit_step(tracer, on_step)
                return rag_result
            except Exception as exc:
                message = (
                    "RAG retrieval unavailable and kb_search fallback failed: "
                    f"{exc}"
                )
                tracer.error(content=message, error_type="RAGUnavailable")
                self._emit_step(tracer, on_step)
                rag_result = {"results": [], "total_results": 0, "query": query}
                tracer.rag_retrieval(query=query, num_results=0, sources=[])
                self._emit_step(tracer, on_step)
                return rag_result

        try:
            rag_result = retrieve(query, top_k=3)
        except (RetrievalError, ValueError, Exception) as exc:
            tracer.error(
                content=f"RAG retrieval failed: {exc}",
                error_type=type(exc).__name__,
            )
            self._emit_step(tracer, on_step)
            rag_result = {"results": [], "total_results": 0, "query": query}

        sources = [item.get("source_file", "Unknown") for item in rag_result.get("results", [])]
        tracer.rag_retrieval(
            query=query,
            num_results=rag_result.get("total_results", 0),
            sources=sources,
        )
        self._emit_step(tracer, on_step)
        return rag_result

    def _execute_tool_with_retry_fallback(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        user_input: str,
        iteration: int,
        tracer: Tracer,
        on_step: StepCallback | None,
        tool_failures: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], bool]:
        """Execute tool with one retry and deterministic fallback map."""

        if tool_name == "create_ticket" and arguments.get("confirmed") is True:
            if not self._is_explicit_confirmation(user_input):
                message = (
                    "Ticket creation blocked: explicit user confirmation is required "
                    "before calling create_ticket with confirmed=true."
                )
                tracer.error(
                    content=message,
                    iteration=iteration,
                    error_type="ActionGateError",
                    tool_name=tool_name,
                )
                self._emit_step(tracer, on_step)
                failure = {
                    "tool": tool_name,
                    "attempt": "gate",
                    "error_type": "ActionGateError",
                    "error": message,
                }
                tool_failures.append(failure)
                return {"status": "blocked", "error": message, "requires_confirmation": True}, True

        for attempt in (1, 2):
            tracer.action(tool_name=tool_name, tool_input=arguments, iteration=iteration)
            self._emit_step(tracer, on_step)
            try:
                result = execute_tool(tool_name, arguments)
                tracer.observation(
                    tool_name=tool_name,
                    tool_output=result,
                    iteration=iteration,
                )
                self._emit_step(tracer, on_step)
                return result, False
            except Exception as exc:
                error_msg = f"Tool '{tool_name}' failed on attempt {attempt}: {exc}"
                tracer.error(
                    content=error_msg,
                    iteration=iteration,
                    error_type=type(exc).__name__,
                    tool_name=tool_name,
                )
                self._emit_step(tracer, on_step)
                tool_failures.append(
                    {
                        "tool": tool_name,
                        "attempt": attempt,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    }
                )
                if attempt == 1:
                    continue
                break

        fallback = self._fallback_for_tool(tool_name, arguments)
        if fallback is None:
            return {
                "status": "error",
                "error": f"Tool '{tool_name}' failed after retry; no fallback available.",
                "original_tool": tool_name,
            }, True

        fallback_tool, fallback_args = fallback
        tracer.action(tool_name=fallback_tool, tool_input=fallback_args, iteration=iteration)
        self._emit_step(tracer, on_step)
        try:
            fallback_result = execute_tool(fallback_tool, fallback_args)
            wrapped = {
                "status": "fallback_success",
                "original_tool": tool_name,
                "fallback_tool": fallback_tool,
                "fallback_result": fallback_result,
            }
            tracer.observation(
                tool_name=fallback_tool,
                tool_output=wrapped,
                iteration=iteration,
            )
            self._emit_step(tracer, on_step)
            return wrapped, False
        except Exception as exc:
            error_msg = f"Fallback tool '{fallback_tool}' failed: {exc}"
            tracer.error(
                content=error_msg,
                iteration=iteration,
                error_type=type(exc).__name__,
                tool_name=fallback_tool,
            )
            self._emit_step(tracer, on_step)
            tool_failures.append(
                {
                    "tool": fallback_tool,
                    "attempt": "fallback",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "from": tool_name,
                }
            )
            return {
                "status": "error",
                "error": error_msg,
                "original_tool": tool_name,
                "fallback_tool": fallback_tool,
            }, True

    def _fallback_for_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> tuple[str, dict[str, Any]] | None:
        """Return deterministic fallback tool+args for the given tool call."""
        if tool_name == "log_search":
            service_name = arguments.get("service")
            fallback_args = {"service_name": service_name} if service_name else {}
            return "status_check", fallback_args

        if tool_name == "server_metrics":
            service_name = arguments.get("hostname")
            fallback_args = {"service_name": service_name} if service_name else {}
            return "status_check", fallback_args

        return None

    def _is_explicit_confirmation(self, text: str) -> bool:
        """Check whether user text contains explicit ticket confirmation."""
        return bool(_CONFIRMATION_PATTERN.search(text or ""))

    def _to_assistant_blocks(self, blocks: list[Any]) -> list[dict[str, Any]]:
        """Convert Anthropic content blocks into serializable dicts."""
        normalized: list[dict[str, Any]] = []
        for block in blocks:
            if hasattr(block, "model_dump"):
                normalized.append(block.model_dump(exclude_none=True))
            elif isinstance(block, dict):
                normalized.append(block)
            else:
                normalized.append({"type": "text", "text": str(block)})
        return normalized

    def _to_tool_result_block(
        self,
        tool_use_id: str,
        result: dict[str, Any],
        is_error: bool,
    ) -> dict[str, Any]:
        """Build a tool_result block for Anthropic follow-up call."""
        serialized = json.dumps(result, ensure_ascii=True, indent=2, default=str)
        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": serialized,
            "is_error": is_error,
        }

    def _build_iteration_cap_response(self, tool_failures: list[dict[str, Any]]) -> str:
        """Generate deterministic response when loop reaches iteration cap."""
        lines = [
            (
                f"I reached the maximum investigation depth ({self.max_iterations} iterations) "
                "before producing a final answer."
            )
        ]
        if tool_failures:
            lines.append("Tool issues encountered:")
            for failure in tool_failures[:5]:
                attempt = failure.get("attempt")
                lines.append(
                    f"- {failure.get('tool')} (attempt={attempt}): {failure.get('error_type')}"
                )
        lines.append(
            "Please provide additional incident details (affected service, error text, timeframe) "
            "and I will continue."
        )
        return "\n".join(lines)

    def _persist_turns(
        self,
        session_id: str,
        user_text: str,
        assistant_text: str,
        tracer: Tracer,
        on_step: StepCallback | None,
    ) -> None:
        """Persist user/assistant turns, tracing persistence failures only."""
        try:
            self.memory_store.save_turn(session_id=session_id, role="user", content=user_text)
            self.memory_store.save_turn(
                session_id=session_id,
                role="assistant",
                content=assistant_text,
            )
        except Exception as exc:
            tracer.error(
                content=f"Memory save failed: {exc}",
                error_type=type(exc).__name__,
            )
            self._emit_step(tracer, on_step)

"""Streamlit UI for the Otic IT Support Agent."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any

import streamlit as st
from dotenv import load_dotenv

from agent.core import AgentCore


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@st.cache_resource
def _get_agent() -> AgentCore:
    return AgentCore()


def _init_session_state() -> None:
    if "session_id" not in st.session_state:
        st.session_state.session_id = str(uuid.uuid4())
    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []
    if "latest_traces" not in st.session_state:
        st.session_state.latest_traces = []
    if "agent" not in st.session_state:
        st.session_state.agent = None


def _reset_incident() -> None:
    st.session_state.session_id = str(uuid.uuid4())
    st.session_state.chat_history = []
    st.session_state.latest_traces = []


def _format_trace_title(step: dict[str, Any]) -> str:
    raw_ts = step.get("timestamp")
    if isinstance(raw_ts, (int, float)):
        ts = datetime.fromtimestamp(raw_ts).strftime("%H:%M:%S")
    else:
        ts = "--:--:--"

    step_type = str(step.get("step_type", "unknown")).upper()
    iteration = step.get("iteration")
    iteration_label = str(iteration) if iteration is not None else "-"
    return f"[{ts}] ITER {iteration_label} | {step_type}"


def _render_trace_panel(
    steps: list[dict[str, Any]],
    container: Any,
    in_progress: bool,
) -> None:
    with container.container():
        st.subheader("Observability")
        status = "Running..." if in_progress else "Idle"
        st.caption(f"{status} | Steps: {len(steps)}")

        if not steps:
            st.info("No traces yet. Submit an incident message to start.")
            return

        for index, step in enumerate(steps):
            expanded = in_progress and index == len(steps) - 1
            with st.expander(_format_trace_title(step), expanded=expanded):
                content = step.get("content")
                if content:
                    st.write(content)

                if step.get("tool_name"):
                    st.markdown(f"**Tool:** `{step['tool_name']}`")
                if step.get("tool_input") is not None:
                    st.markdown("**Tool Input**")
                    st.json(step["tool_input"])
                if step.get("tool_output") is not None:
                    st.markdown("**Tool Output**")
                    st.json(step["tool_output"])
                if step.get("duration_ms") is not None:
                    st.markdown(f"**Duration:** `{round(step['duration_ms'], 2)} ms`")
                if step.get("metadata"):
                    st.markdown("**Metadata**")
                    st.json(step["metadata"])


def main() -> None:
    st.set_page_config(page_title="Otic IT Support Agent", layout="wide")
    load_dotenv()
    _init_session_state()

    st.title("Otic IT Support Agent")

    header_left, header_right = st.columns([3, 1])
    with header_left:
        st.caption(f"Session: `{st.session_state.session_id}`")
    with header_right:
        if st.button("New Incident", use_container_width=True):
            _reset_incident()
            st.rerun()

    left_col, right_col = st.columns([2, 1])

    with right_col:
        trace_placeholder = st.empty()
        _render_trace_panel(
            steps=st.session_state.latest_traces,
            container=trace_placeholder,
            in_progress=False,
        )

    with left_col:
        st.subheader("Incident Chat")
        for item in st.session_state.chat_history:
            with st.chat_message(item["role"]):
                st.markdown(item["content"])

        has_api_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
        if not has_api_key:
            st.error(
                "Missing ANTHROPIC_API_KEY. Set it in your environment before sending messages."
            )

        if has_api_key and st.session_state.agent is None:
            try:
                st.session_state.agent = _get_agent()
            except Exception as exc:  # pragma: no cover - startup safety path
                st.error(f"Failed to initialize agent: {exc}")
                has_api_key = False

        user_text = st.chat_input(
            "Describe the incident...",
            disabled=not has_api_key,
        )

        if not user_text:
            return

        user_msg = {"role": "user", "content": user_text, "timestamp": _utc_now_iso()}
        st.session_state.chat_history.append(user_msg)
        with st.chat_message("user"):
            st.markdown(user_text)

        live_steps: list[dict[str, Any]] = []

        def on_step(step: dict[str, Any]) -> None:
            live_steps.append(step)
            _render_trace_panel(
                steps=live_steps,
                container=trace_placeholder,
                in_progress=True,
            )

        try:
            result = st.session_state.agent.run_turn(
                session_id=st.session_state.session_id,
                user_input=user_text,
                on_step=on_step,
            )
            assistant_text = result.response_text
            st.session_state.latest_traces = result.traces
        except Exception as exc:  # pragma: no cover - UI safety path
            assistant_text = (
                "I hit an internal error while processing this incident. "
                "Please check logs and try again."
            )
            error_step = {
                "step_type": "error",
                "content": f"Unhandled UI/core exception: {exc}",
                "timestamp": datetime.now(timezone.utc).timestamp(),
                "iteration": None,
                "metadata": {"error_type": type(exc).__name__},
            }
            live_steps.append(error_step)
            st.session_state.latest_traces = live_steps
            _render_trace_panel(
                steps=st.session_state.latest_traces,
                container=trace_placeholder,
                in_progress=False,
            )

        assistant_msg = {
            "role": "assistant",
            "content": assistant_text,
            "timestamp": _utc_now_iso(),
        }
        st.session_state.chat_history.append(assistant_msg)
        with st.chat_message("assistant"):
            st.markdown(assistant_text)

        _render_trace_panel(
            steps=st.session_state.latest_traces,
            container=trace_placeholder,
            in_progress=False,
        )


if __name__ == "__main__":
    main()


"""Log search tool — searches application and system logs for matching entries.

Reads from dummy_logs.json and filters by query text, service name, log level,
and time window. Has a 10% chance of simulated failure to test agent retry behavior.
"""

import json
import logging
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_LOGS_FILE = _DATA_DIR / "dummy_logs.json"

FAILURE_RATE = 0.10  # 10% chance of simulated failure

VALID_LEVELS = {"INFO", "WARNING", "ERROR", "CRITICAL"}


class LogSearchError(Exception):
    """Raised when log search encounters a simulated or real failure."""


def log_search(
    query: str,
    service: str | None = None,
    level: str | None = None,
    hours: int = 24,
) -> dict[str, Any]:
    """Search application and system logs for matching entries.

    Filters dummy log data by query text, optional service name, optional
    severity level, and a time window. Has a 10% chance of simulated failure
    to test the agent's retry and fallback behavior.

    Args:
        query: Text to search for in log messages (case-insensitive).
        service: Optional service name filter (e.g., "vpn-gateway").
        level: Optional severity filter ("INFO", "WARNING", "ERROR", "CRITICAL").
        hours: How many hours back to search from now (default 24).

    Returns:
        A dict with keys:
            - entries: list of matching log entry dicts
            - total_matches: count of matching entries
            - filters_applied: dict describing the active filters
            - query: the original query string

    Raises:
        LogSearchError: On simulated failure (10% chance) or data loading errors.
        ValueError: If level is provided but not a valid severity.
    """
    # Simulated failure — tests agent retry/fallback behavior
    if random.random() < FAILURE_RATE:
        logger.warning("Simulated log search failure triggered")
        raise LogSearchError(
            "Connection to log aggregator timed out after 30 seconds. "
            "The log service may be temporarily unavailable."
        )

    if not query or not query.strip():
        raise ValueError("Search query cannot be empty")

    if level is not None and level.upper() not in VALID_LEVELS:
        raise ValueError(f"Invalid log level: {level}. Must be one of {VALID_LEVELS}")

    if not _LOGS_FILE.exists():
        raise FileNotFoundError(f"Log data file not found: {_LOGS_FILE}")

    # Load log data
    try:
        raw_data = _LOGS_FILE.read_text(encoding="utf-8")
        logs: list[dict[str, str]] = json.loads(raw_data)
    except json.JSONDecodeError as exc:
        raise LogSearchError(f"Failed to parse log data: {exc}") from exc

    # Calculate time cutoff
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours)

    query_lower = query.lower()
    level_upper = level.upper() if level else None

    # Filter logs
    matching: list[dict[str, str]] = []
    for entry in logs:
        # Time filter
        try:
            entry_time = datetime.fromisoformat(entry["timestamp"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue  # Skip entries with invalid timestamps

        if entry_time < cutoff:
            continue

        # Query text filter (search in message field)
        if query_lower not in entry.get("message", "").lower():
            continue

        # Service filter
        if service and entry.get("service", "").lower() != service.lower():
            continue

        # Level filter
        if level_upper and entry.get("level", "").upper() != level_upper:
            continue

        matching.append(entry)

    # Sort by timestamp descending (most recent first)
    matching.sort(key=lambda e: e.get("timestamp", ""), reverse=True)

    filters_applied = {
        "query": query,
        "service": service,
        "level": level,
        "hours": hours,
        "time_cutoff": cutoff.isoformat(),
    }

    logger.info(
        "Log search completed: query=%r, %d matches found",
        query, len(matching),
    )

    return {
        "entries": matching,
        "total_matches": len(matching),
        "filters_applied": filters_applied,
        "query": query,
    }

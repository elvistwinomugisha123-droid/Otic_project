"""SQLite-based memory store for conversation history and incident tracking.

Provides persistent storage for agent conversation turns (with sensitive data
stripped via regex patterns) and IT incident lifecycle tracking. Uses SQLite
for zero-dependency local persistence.
"""

import json
import logging
import os
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_DEFAULT_DB_PATH = str(_DATA_DIR / "memory.db")

VALID_ROLES = {"user", "assistant", "system"}
VALID_STATUSES = {"open", "investigating", "resolved", "escalated"}

# --- Sensitive Data Stripping ---

# Patterns are applied in order. More specific patterns (JWT, API keys) must
# come before generic ones (token, key) to avoid partial matches.
_SENSITIVE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # JWT tokens (three base64url segments separated by dots)
    (
        re.compile(r"eyJ[a-zA-Z0-9\-_]+\.eyJ[a-zA-Z0-9\-_]+\.[a-zA-Z0-9\-_]+"),
        "[REDACTED_JWT]",
    ),
    # API keys (sk-... pattern common in Anthropic, OpenAI, Stripe, etc.)
    (
        re.compile(r"sk-[a-zA-Z0-9\-_]{20,}"),
        "[REDACTED_API_KEY]",
    ),
    # Bearer tokens in authorization headers
    (
        re.compile(r"Bearer\s+[a-zA-Z0-9\-_.]+", re.IGNORECASE),
        "Bearer [REDACTED_TOKEN]",
    ),
    # Passwords in common assignment patterns
    (
        re.compile(r"(password)\s*[:\s=]+\S+", re.IGNORECASE),
        "password: [REDACTED]",
    ),
    # Generic secrets/tokens/keys with value assignment (: or = only, not free text)
    (
        re.compile(r"(secret|token|api_key|apikey)\s*[:=]\s*['\"]?\S+['\"]?", re.IGNORECASE),
        r"\1: [REDACTED]",
    ),
]


def strip_sensitive_data(text: str) -> str:
    """Remove sensitive data patterns from text before storage.

    Applies regex-based redaction for common secret patterns including
    API keys, bearer tokens, passwords, JWTs, and generic secrets.
    This function is intentionally standalone (not a class method) so
    it can be imported and used by other modules like agent/safety.py.

    Args:
        text: The raw text to sanitize.

    Returns:
        The text with sensitive patterns replaced by redaction markers.
    """
    result = text
    for pattern, replacement in _SENSITIVE_PATTERNS:
        result = pattern.sub(replacement, result)
    return result


# --- SQLite Schema ---

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    timestamp TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_conversations_session_ts
    ON conversations (session_id, timestamp);

CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id TEXT UNIQUE NOT NULL,
    session_id TEXT NOT NULL,
    status TEXT NOT NULL,
    summary TEXT,
    tools_used TEXT,
    resolution TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_incidents_session
    ON incidents (session_id);
"""


class MemoryStoreError(Exception):
    """Raised when memory store operations fail."""


class MemoryStore:
    """SQLite-backed store for conversation history and incident tracking.

    Automatically creates the database and tables on initialization.
    All conversation content is sanitized via strip_sensitive_data()
    before storage. Each method opens its own database connection for
    thread safety (Streamlit may call from different threads).

    Args:
        db_path: Path to the SQLite database file. Defaults to
            MEMORY_DB_PATH env var or ./data/memory.db.
    """

    def __init__(self, db_path: str | None = None) -> None:
        if db_path is not None:
            self._db_path = db_path
        else:
            self._db_path = os.environ.get("MEMORY_DB_PATH", _DEFAULT_DB_PATH)

        self._is_memory = self._db_path == ":memory:"

        # For in-memory databases, keep a persistent connection since each
        # sqlite3.connect(":memory:") creates a separate database.
        # For file-based databases, open/close per method for thread safety.
        if self._is_memory:
            self._shared_conn = sqlite3.connect(":memory:")
            self._shared_conn.row_factory = sqlite3.Row
        else:
            self._shared_conn = None
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)

        self._init_db()
        logger.info("Memory store initialized at %s", self._db_path)

    def _init_db(self) -> None:
        """Create database tables and indexes if they do not exist."""
        conn = self._connect()
        try:
            conn.executescript(_SCHEMA_SQL)
            conn.commit()
        finally:
            self._release(conn)

    def _connect(self) -> sqlite3.Connection:
        """Get a database connection with Row factory for dict-like access.

        For file-based databases, opens a new connection (thread-safe).
        For in-memory databases, returns the shared connection.
        """
        if self._shared_conn is not None:
            return self._shared_conn
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _release(self, conn: sqlite3.Connection) -> None:
        """Release a connection — closes file-based, keeps in-memory alive."""
        if self._shared_conn is None:
            conn.close()

    # --- Conversation Methods ---

    def save_turn(self, session_id: str, role: str, content: str) -> None:
        """Save a conversation turn with sensitive data stripped.

        Args:
            session_id: The session identifier.
            role: One of "user", "assistant", "system".
            content: The message content (will be sanitized before storage).

        Raises:
            ValueError: If role is not valid.
            MemoryStoreError: If database write fails.
        """
        if role not in VALID_ROLES:
            raise ValueError(f"Invalid role: '{role}'. Must be one of: {sorted(VALID_ROLES)}")

        sanitized_content = strip_sensitive_data(content)
        timestamp = datetime.now(timezone.utc).isoformat()

        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO conversations (session_id, role, content, timestamp) "
                "VALUES (?, ?, ?, ?)",
                (session_id, role, sanitized_content, timestamp),
            )
            conn.commit()
        except sqlite3.Error as exc:
            raise MemoryStoreError(f"Failed to save conversation turn: {exc}") from exc
        finally:
            self._release(conn)

    def get_recent_turns(self, session_id: str, limit: int = 20) -> list[dict[str, Any]]:
        """Retrieve the most recent conversation turns for a session.

        Args:
            session_id: The session identifier.
            limit: Maximum number of turns to return (default 20).

        Returns:
            List of dicts with keys: id, session_id, role, content, timestamp.
            Ordered chronologically (oldest first) for conversation context.
        """
        conn = self._connect()
        try:
            cursor = conn.execute(
                "SELECT id, session_id, role, content, timestamp "
                "FROM conversations "
                "WHERE session_id = ? "
                "ORDER BY timestamp DESC "
                "LIMIT ?",
                (session_id, limit),
            )
            rows = [dict(row) for row in cursor.fetchall()]
        finally:
            self._release(conn)

        # Reverse to chronological order (oldest first)
        rows.reverse()
        return rows

    # --- Incident Methods ---

    def create_incident(self, session_id: str, summary: str) -> str:
        """Create a new incident record.

        Args:
            session_id: The session identifier.
            summary: Brief description of the incident.

        Returns:
            The generated incident_id string (format: "INC-{8_hex_chars}").

        Raises:
            MemoryStoreError: If database write fails.
        """
        incident_id = f"INC-{uuid.uuid4().hex[:8].upper()}"
        now = datetime.now(timezone.utc).isoformat()

        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO incidents "
                "(incident_id, session_id, status, summary, tools_used, resolution, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (incident_id, session_id, "open", summary, "[]", None, now, now),
            )
            conn.commit()
        except sqlite3.Error as exc:
            raise MemoryStoreError(f"Failed to create incident: {exc}") from exc
        finally:
            self._release(conn)

        logger.info("Created incident %s for session %s", incident_id, session_id)
        return incident_id

    def update_incident(
        self,
        incident_id: str,
        status: str | None = None,
        tools_used: list[str] | None = None,
        resolution: str | None = None,
    ) -> None:
        """Update an existing incident record.

        Only provided (non-None) fields are updated.

        Args:
            incident_id: The incident to update.
            status: New status (must be in VALID_STATUSES if provided).
            tools_used: List of tool names used during investigation.
            resolution: Resolution description.

        Raises:
            ValueError: If status is provided but invalid.
            MemoryStoreError: If incident_id does not exist or update fails.
        """
        if status is not None and status not in VALID_STATUSES:
            raise ValueError(
                f"Invalid status: '{status}'. Must be one of: {sorted(VALID_STATUSES)}"
            )

        # Build dynamic UPDATE query with only non-None fields
        updates: list[str] = []
        params: list[Any] = []

        if status is not None:
            updates.append("status = ?")
            params.append(status)

        if tools_used is not None:
            updates.append("tools_used = ?")
            params.append(json.dumps(tools_used))

        if resolution is not None:
            updates.append("resolution = ?")
            params.append(resolution)

        # Always update the timestamp
        updates.append("updated_at = ?")
        params.append(datetime.now(timezone.utc).isoformat())

        params.append(incident_id)

        conn = self._connect()
        try:
            cursor = conn.execute(
                f"UPDATE incidents SET {', '.join(updates)} WHERE incident_id = ?",
                params,
            )
            conn.commit()

            if cursor.rowcount == 0:
                raise MemoryStoreError(f"Incident '{incident_id}' not found")
        except sqlite3.Error as exc:
            raise MemoryStoreError(f"Failed to update incident: {exc}") from exc
        finally:
            self._release(conn)

        logger.info("Updated incident %s", incident_id)

    def get_incident(self, incident_id: str) -> dict[str, Any] | None:
        """Retrieve a single incident by its ID.

        Args:
            incident_id: The incident identifier.

        Returns:
            Dict with incident fields (tools_used deserialized from JSON),
            or None if not found.
        """
        conn = self._connect()
        try:
            cursor = conn.execute(
                "SELECT * FROM incidents WHERE incident_id = ?",
                (incident_id,),
            )
            row = cursor.fetchone()
        finally:
            self._release(conn)

        if row is None:
            return None

        result = dict(row)
        # Deserialize tools_used from JSON string to list
        try:
            result["tools_used"] = json.loads(result.get("tools_used", "[]"))
        except (json.JSONDecodeError, TypeError):
            result["tools_used"] = []

        return result

    def get_session_incidents(self, session_id: str) -> list[dict[str, Any]]:
        """Retrieve all incidents for a session.

        Args:
            session_id: The session identifier.

        Returns:
            List of incident dicts ordered by created_at descending (newest first).
        """
        conn = self._connect()
        try:
            cursor = conn.execute(
                "SELECT * FROM incidents WHERE session_id = ? "
                "ORDER BY created_at DESC",
                (session_id,),
            )
            rows = [dict(row) for row in cursor.fetchall()]
        finally:
            self._release(conn)

        # Deserialize tools_used for each incident
        for row in rows:
            try:
                row["tools_used"] = json.loads(row.get("tools_used", "[]"))
            except (json.JSONDecodeError, TypeError):
                row["tools_used"] = []

        return rows

    def load_context(self, session_id: str) -> dict[str, Any]:
        """Load full conversation context for the agent loop.

        Combines recent conversation turns and active incidents into
        a single context dict that the agent can use for grounding.

        Args:
            session_id: The session identifier.

        Returns:
            Dict with keys:
                - recent_turns: list of recent conversation turn dicts
                - active_incidents: list of incident dicts where status
                  is "open" or "investigating"
        """
        recent_turns = self.get_recent_turns(session_id)

        # Get only active incidents
        conn = self._connect()
        try:
            cursor = conn.execute(
                "SELECT * FROM incidents "
                "WHERE session_id = ? AND status IN ('open', 'investigating') "
                "ORDER BY created_at DESC",
                (session_id,),
            )
            active_rows = [dict(row) for row in cursor.fetchall()]
        finally:
            self._release(conn)

        # Deserialize tools_used
        for row in active_rows:
            try:
                row["tools_used"] = json.loads(row.get("tools_used", "[]"))
            except (json.JSONDecodeError, TypeError):
                row["tools_used"] = []

        return {
            "recent_turns": recent_turns,
            "active_incidents": active_rows,
        }

"""Ticket creation tool — creates IT support tickets for incidents.

This is a GATED action: the 'confirmed' parameter must be True or the function
returns a confirmation request instead of creating the ticket. The agent must
obtain user confirmation before passing confirmed=True.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

VALID_PRIORITIES = {"low", "medium", "high", "critical"}
VALID_CATEGORIES = {"network", "server", "application", "security", "other"}


class TicketCreationError(Exception):
    """Raised when ticket creation fails validation."""


def create_ticket(
    title: str,
    description: str,
    priority: str,
    category: str,
    confirmed: bool = False,
) -> dict[str, Any]:
    """Create an IT support ticket for an incident.

    This is a GATED action — the 'confirmed' parameter must be True or the
    function returns a confirmation preview instead of actually creating the
    ticket. The agent loop should present the preview to the user and only
    call again with confirmed=True after explicit user approval.

    Args:
        title: Short descriptive title for the ticket.
        description: Detailed description of the issue and diagnosis so far.
        priority: One of "low", "medium", "high", "critical".
        category: One of "network", "server", "application", "security", "other".
        confirmed: Must be True to actually create the ticket (default False).

    Returns:
        If confirmed is False:
            A dict with status "confirmation_required" and a ticket preview.
        If confirmed is True:
            A dict with status "created", a generated ticket ID, and ticket details.

    Raises:
        TicketCreationError: If priority or category values are invalid.
        ValueError: If title or description is empty.
    """
    # Validate inputs
    if not title or not title.strip():
        raise ValueError("Ticket title cannot be empty")

    if not description or not description.strip():
        raise ValueError("Ticket description cannot be empty")

    priority_lower = priority.lower().strip()
    category_lower = category.lower().strip()

    if priority_lower not in VALID_PRIORITIES:
        raise TicketCreationError(
            f"Invalid priority: '{priority}'. Must be one of: {sorted(VALID_PRIORITIES)}"
        )

    if category_lower not in VALID_CATEGORIES:
        raise TicketCreationError(
            f"Invalid category: '{category}'. Must be one of: {sorted(VALID_CATEGORIES)}"
        )

    ticket_preview = {
        "title": title.strip(),
        "description": description.strip(),
        "priority": priority_lower,
        "category": category_lower,
    }

    # Confirmation gate — return preview if not confirmed
    if not confirmed:
        logger.info("Ticket creation requires confirmation: %s", title)
        return {
            "status": "confirmation_required",
            "message": (
                "Please confirm you want to create this support ticket. "
                "Call this tool again with confirmed=true to proceed."
            ),
            "ticket_preview": ticket_preview,
        }

    # Create the ticket
    ticket_id = f"TKT-{uuid.uuid4().hex[:8].upper()}"
    created_at = datetime.now(timezone.utc).isoformat()

    logger.info("Ticket created: %s — %s [%s/%s]", ticket_id, title, priority_lower, category_lower)

    return {
        "status": "created",
        "ticket_id": ticket_id,
        "title": title.strip(),
        "description": description.strip(),
        "priority": priority_lower,
        "category": category_lower,
        "created_at": created_at,
    }

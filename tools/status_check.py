"""Status check tool — checks the operational status of IT services.

Reads from dummy_services.json. This tool has no simulated failure because
it serves as the most reliable diagnostic starting point — if everything
else fails, status_check should still work.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_SERVICES_FILE = _DATA_DIR / "dummy_services.json"

VALID_STATUSES = {"healthy", "degraded", "warning", "down"}


def status_check(service_name: str | None = None) -> dict[str, Any]:
    """Check the operational status of IT services.

    Reads from dummy_services.json. If service_name is provided, returns
    status for that specific service. If None, returns status for all services.

    This tool is intentionally the most reliable — no simulated failures.
    It serves as the baseline diagnostic tool the agent can always depend on.

    Args:
        service_name: Optional specific service to check. If None, returns all.

    Returns:
        A dict with keys:
            - services: list of service status dicts
            - summary: dict with counts per status category
              (healthy, degraded, warning, down)
            - timestamp: current timestamp string

    Raises:
        FileNotFoundError: If dummy_services.json is missing.
        ValueError: If service_name is provided but not found.
    """
    if not _SERVICES_FILE.exists():
        raise FileNotFoundError(f"Services data file not found: {_SERVICES_FILE}")

    # Load services data
    try:
        raw_data = _SERVICES_FILE.read_text(encoding="utf-8")
        all_services: dict[str, dict[str, Any]] = json.loads(raw_data)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Failed to parse services data: {exc}") from exc

    now = datetime.now(timezone.utc).isoformat()

    if service_name is not None:
        # Return status for a specific service
        if service_name not in all_services:
            available = list(all_services.keys())
            raise ValueError(
                f"Service '{service_name}' not found. Available services: {available}"
            )

        service_data = all_services[service_name]
        status = service_data.get("status", "unknown")

        logger.info("Status check for %s: %s", service_name, status)

        return {
            "services": [service_data],
            "summary": {s: (1 if s == status else 0) for s in VALID_STATUSES},
            "timestamp": now,
        }

    # Return status for all services
    services_list = list(all_services.values())

    summary: dict[str, int] = {s: 0 for s in VALID_STATUSES}
    for svc in services_list:
        status = svc.get("status", "unknown")
        if status in summary:
            summary[status] += 1

    logger.info(
        "Status check for all services: %s",
        ", ".join(f"{k}={v}" for k, v in summary.items()),
    )

    return {
        "services": services_list,
        "summary": summary,
        "timestamp": now,
    }

"""Server metrics tool — fetches CPU, memory, disk, and network metrics for servers.

Reads from dummy_metrics.json. Has a 10% chance of simulated failure to test
the agent's retry and fallback behavior.
"""

import json
import logging
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_METRICS_FILE = _DATA_DIR / "dummy_metrics.json"

FAILURE_RATE = 0.10  # 10% chance of simulated failure

# Alert thresholds
CPU_THRESHOLD = 90.0
MEMORY_THRESHOLD = 85.0
DISK_THRESHOLD = 90.0
LOAD_THRESHOLD = 4.0


class MetricsConnectionError(Exception):
    """Raised when metrics collection encounters a simulated connection failure."""


def _generate_alerts(metrics: dict[str, Any]) -> list[str]:
    """Generate alert strings for metrics that exceed thresholds.

    Args:
        metrics: A single server's metrics dict.

    Returns:
        List of human-readable alert strings.
    """
    alerts: list[str] = []
    hostname = metrics.get("hostname", "unknown")

    cpu = metrics.get("cpu_percent", 0.0)
    if cpu > CPU_THRESHOLD:
        alerts.append(f"CRITICAL: CPU at {cpu}% on {hostname} (threshold: {CPU_THRESHOLD}%)")

    memory = metrics.get("memory_percent", 0.0)
    if memory > MEMORY_THRESHOLD:
        alerts.append(f"WARNING: Memory at {memory}% on {hostname} (threshold: {MEMORY_THRESHOLD}%)")

    disk = metrics.get("disk_percent", 0.0)
    if disk > DISK_THRESHOLD:
        alerts.append(f"CRITICAL: Disk at {disk}% on {hostname} (threshold: {DISK_THRESHOLD}%)")

    load = metrics.get("load_average_1m", 0.0)
    if load > LOAD_THRESHOLD:
        alerts.append(f"WARNING: Load average (1m) at {load} on {hostname} (threshold: {LOAD_THRESHOLD})")

    return alerts


def server_metrics(hostname: str | None = None) -> dict[str, Any]:
    """Fetch current resource metrics (CPU, memory, disk, network) for servers.

    Reads from dummy_metrics.json. If hostname is provided, returns metrics
    for that specific server. If None, returns metrics for all servers.
    Has a 10% chance of simulated connection failure.

    Args:
        hostname: Optional server hostname to check. If None, returns all servers.

    Returns:
        A dict with keys:
            - metrics: server metrics dict (single server or all servers)
            - alerts: list of alert strings for values exceeding thresholds
            - timestamp: current timestamp string

    Raises:
        MetricsConnectionError: On simulated failure (10% chance).
        FileNotFoundError: If dummy_metrics.json is missing.
        ValueError: If hostname is provided but not found in data.
    """
    # Simulated failure — tests agent retry/fallback behavior
    if random.random() < FAILURE_RATE:
        logger.warning("Simulated metrics connection failure triggered")
        raise MetricsConnectionError(
            "Failed to connect to metrics collector on port 9090. "
            "Connection refused — the monitoring agent may be down."
        )

    if not _METRICS_FILE.exists():
        raise FileNotFoundError(f"Metrics data file not found: {_METRICS_FILE}")

    # Load metrics data
    try:
        raw_data = _METRICS_FILE.read_text(encoding="utf-8")
        all_metrics: dict[str, dict[str, Any]] = json.loads(raw_data)
    except json.JSONDecodeError as exc:
        raise MetricsConnectionError(f"Failed to parse metrics data: {exc}") from exc

    now = datetime.now(timezone.utc).isoformat()

    if hostname is not None:
        # Return metrics for a specific server
        if hostname not in all_metrics:
            available = list(all_metrics.keys())
            raise ValueError(
                f"Server '{hostname}' not found. Available servers: {available}"
            )

        server_data = all_metrics[hostname]
        alerts = _generate_alerts(server_data)

        logger.info("Metrics retrieved for %s: %d alerts", hostname, len(alerts))

        return {
            "metrics": {hostname: server_data},
            "alerts": alerts,
            "timestamp": now,
        }

    # Return metrics for all servers
    all_alerts: list[str] = []
    for server_data in all_metrics.values():
        all_alerts.extend(_generate_alerts(server_data))

    logger.info("Metrics retrieved for all %d servers: %d alerts", len(all_metrics), len(all_alerts))

    return {
        "metrics": all_metrics,
        "alerts": all_alerts,
        "timestamp": now,
    }

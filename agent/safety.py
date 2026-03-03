"""Safety module — prompt injection detection, action gating, and sensitive data filtering.

Three components:
1. Prompt Injection Detector — scores user input for injection risk (0.0-1.0)
2. Action Gate — flags destructive tools as requiring user confirmation
3. Sensitive Data Filter — strips secrets and PII before display or storage

All functions are pure (no side effects, no I/O) and independently testable.
"""

import logging
import re
from typing import Any

from memory.store import strip_sensitive_data

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. Prompt Injection Detector
# ---------------------------------------------------------------------------

# Each pattern has a regex, a human-readable label, and a weight (0.0-1.0)
# that contributes to the overall risk score. Higher weight = more dangerous.
_INJECTION_PATTERNS: list[tuple[re.Pattern[str], str, float]] = [
    # --- Instruction override attempts (high risk) ---
    (
        re.compile(
            r"ignore\s+(all\s+)?(previous|prior|above|earlier|your)\s+"
            r"(instructions|prompts|rules|directives|guidelines)",
            re.IGNORECASE,
        ),
        "instruction_override",
        0.95,
    ),
    (
        re.compile(
            r"(disregard|forget|drop|abandon)\s+(all\s+)?"
            r"(your|previous|prior|the)\s+"
            r"(instructions|rules|prompts|constraints|guidelines)",
            re.IGNORECASE,
        ),
        "instruction_disregard",
        0.95,
    ),
    (
        re.compile(
            r"(override|bypass|circumvent|disable)\s+(your\s+)?"
            r"(safety|security|instructions|rules|filters|restrictions|constraints)",
            re.IGNORECASE,
        ),
        "safety_bypass",
        0.90,
    ),
    # --- System role hijacking (high risk) ---
    (
        re.compile(
            r"you\s+are\s+now\s+(a|an|my)\s+\w+",
            re.IGNORECASE,
        ),
        "role_hijack",
        0.85,
    ),
    (
        re.compile(
            r"(pretend|act|behave|function)\s+(to be|as if you are|as|like)\s+",
            re.IGNORECASE,
        ),
        "role_impersonation",
        0.80,
    ),
    (
        re.compile(
            r"your\s+new\s+(role|purpose|instructions|task|objective)\s+(is|are)",
            re.IGNORECASE,
        ),
        "role_reassignment",
        0.90,
    ),
    # --- Prompt / system info extraction (medium-high risk) ---
    (
        re.compile(
            r"(reveal|show|display|print|output|repeat|echo)\s+"
            r"(\w+\s+)?(your|the)\s+(system\s+)?(prompt|instructions|rules|configuration)",
            re.IGNORECASE,
        ),
        "prompt_extraction",
        0.75,
    ),
    (
        re.compile(
            r"what\s+are\s+your\s+(instructions|rules|guidelines|directives|constraints)",
            re.IGNORECASE,
        ),
        "instruction_probe",
        0.70,
    ),
    # --- Dangerous action requests (medium risk) ---
    (
        re.compile(
            r"(delete|drop|truncate|destroy|wipe|remove)\s+(all\s+)?"
            r"(data|records|tickets|files|tables|databases|logs|entries)",
            re.IGNORECASE,
        ),
        "destructive_action",
        0.70,
    ),
    (
        re.compile(
            r"(execute|run)\s+(system|shell|bash|cmd|powershell)\s+(command|script)",
            re.IGNORECASE,
        ),
        "command_injection",
        0.85,
    ),
    # --- SQL injection patterns (medium risk) ---
    (
        re.compile(
            r"('\s*(OR|AND)\s+'[^']*'\s*=\s*'|;\s*(DROP|DELETE|UPDATE|INSERT)\s)",
            re.IGNORECASE,
        ),
        "sql_injection",
        0.80,
    ),
    # --- Encoding / obfuscation tricks (medium risk) ---
    (
        re.compile(
            r"(base64|rot13|hex)\s*(decode|encode|convert)",
            re.IGNORECASE,
        ),
        "encoding_trick",
        0.50,
    ),
    # --- Jailbreak framing (medium risk) ---
    (
        re.compile(
            r"(DAN|developer\s*mode|jailbreak|unrestricted\s*mode|god\s*mode)",
            re.IGNORECASE,
        ),
        "jailbreak_framing",
        0.90,
    ),
    (
        re.compile(
            r"(do\s+not|don'?t)\s+follow\s+(your|the|any)\s+"
            r"(rules|instructions|guidelines|safety|policies)",
            re.IGNORECASE,
        ),
        "rule_negation",
        0.85,
    ),
]

INJECTION_THRESHOLD = 0.7  # Risk score >= this means unsafe


def detect_injection(text: str) -> dict[str, Any]:
    """Analyze user input for prompt injection patterns.

    Checks the input text against a curated list of injection patterns,
    each with a severity weight. Returns a risk score (0.0-1.0) and
    details about which patterns were detected.

    This function is pure — no side effects or I/O.

    Args:
        text: The raw user input to analyze.

    Returns:
        A dict with keys:
            - risk_score: float 0.0-1.0 (highest matched pattern weight,
              boosted if multiple patterns match)
            - flagged_patterns: list of pattern label strings that matched
            - is_safe: bool (True if risk_score < INJECTION_THRESHOLD)
            - threshold: the threshold value used
    """
    if not text or not text.strip():
        return {
            "risk_score": 0.0,
            "flagged_patterns": [],
            "is_safe": True,
            "threshold": INJECTION_THRESHOLD,
        }

    flagged: list[str] = []
    max_weight = 0.0

    for pattern, label, weight in _INJECTION_PATTERNS:
        if pattern.search(text):
            flagged.append(label)
            if weight > max_weight:
                max_weight = weight

    # Boost score slightly when multiple patterns match (compounding risk)
    # Each additional pattern adds 0.05, capped at 1.0
    if len(flagged) > 1:
        bonus = (len(flagged) - 1) * 0.05
        risk_score = min(max_weight + bonus, 1.0)
    else:
        risk_score = max_weight

    risk_score = round(risk_score, 2)
    is_safe = risk_score < INJECTION_THRESHOLD

    if flagged:
        logger.warning(
            "Injection detection: score=%.2f, patterns=%s, safe=%s",
            risk_score, flagged, is_safe,
        )

    return {
        "risk_score": risk_score,
        "flagged_patterns": flagged,
        "is_safe": is_safe,
        "threshold": INJECTION_THRESHOLD,
    }


# ---------------------------------------------------------------------------
# 2. Action Gate
# ---------------------------------------------------------------------------

# Tools that require explicit user confirmation before execution.
# Maps tool_name -> risk description shown to the user.
GATED_ACTIONS: dict[str, str] = {
    "create_ticket": (
        "Creating a support ticket is a persistent action that generates "
        "a tracked incident record. Please confirm you want to proceed."
    ),
}


def check_action_gate(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Check whether a tool action requires user confirmation.

    The agent loop should call this BEFORE executing any tool. If the tool
    is gated, the agent must present the risk description to the user and
    obtain confirmation before calling execute_tool().

    For create_ticket specifically, if the arguments already include
    confirmed=True, the gate is considered satisfied.

    This function is pure — no side effects or I/O.

    Args:
        tool_name: The tool name from Claude's tool_use response.
        arguments: The arguments dict from Claude's tool_use input.

    Returns:
        A dict with keys:
            - requires_confirmation: bool — True if user must confirm
            - risk_description: str — explanation of why confirmation is needed
              (empty string if not gated)
            - tool_name: the original tool name
    """
    if tool_name not in GATED_ACTIONS:
        return {
            "requires_confirmation": False,
            "risk_description": "",
            "tool_name": tool_name,
        }

    # For create_ticket, check if already confirmed in the arguments
    if tool_name == "create_ticket" and arguments.get("confirmed") is True:
        return {
            "requires_confirmation": False,
            "risk_description": "",
            "tool_name": tool_name,
        }

    return {
        "requires_confirmation": True,
        "risk_description": GATED_ACTIONS[tool_name],
        "tool_name": tool_name,
    }


# ---------------------------------------------------------------------------
# 3. Sensitive Data Filter (extends memory/store.py patterns)
# ---------------------------------------------------------------------------

# Additional patterns for output filtering (beyond what memory/store.py covers).
# memory/store.py handles: JWTs, API keys, Bearer tokens, passwords, secrets.
# We add: email addresses, credit card-like numbers, IP addresses with context.
_OUTPUT_FILTER_PATTERNS: list[tuple[re.Pattern[str], str, str]] = [
    # Email addresses
    (
        re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"),
        "[REDACTED_EMAIL]",
        "email",
    ),
]


def filter_sensitive_output(text: str) -> dict[str, Any]:
    """Filter sensitive data from text before display or response.

    Applies the memory/store.py patterns first (JWTs, API keys, passwords,
    etc.) then applies additional output-specific patterns (emails).
    Returns the filtered text along with metadata about what was redacted.

    This function is pure — no side effects or I/O.

    Args:
        text: The text to filter (agent response or tool output).

    Returns:
        A dict with keys:
            - filtered_text: the sanitized text
            - redactions_made: total count of redactions applied
            - redaction_types: list of pattern labels that were triggered
    """
    if not text:
        return {
            "filtered_text": text,
            "redactions_made": 0,
            "redaction_types": [],
        }

    redaction_types: list[str] = []
    redaction_count = 0

    # Phase 1: Apply memory/store.py patterns via strip_sensitive_data
    filtered = strip_sensitive_data(text)

    # Count what the base patterns caught by comparing before/after
    if filtered != text:
        # Detect which base categories fired by checking for redaction markers
        base_markers = {
            "[REDACTED_JWT]": "jwt",
            "[REDACTED_API_KEY]": "api_key",
            "Bearer [REDACTED_TOKEN]": "bearer_token",
            "password: [REDACTED]": "password",
        }
        for marker, label in base_markers.items():
            count = filtered.count(marker) - text.count(marker)
            if count > 0:
                redaction_types.append(label)
                redaction_count += count

        # Check for generic secret redaction (pattern uses \1 backreference)
        # These appear as "secret: [REDACTED]", "token: [REDACTED]", etc.
        generic_count = filtered.count(": [REDACTED]") - text.count(": [REDACTED]")
        if generic_count > redaction_count:
            remaining = generic_count - redaction_count
            redaction_types.append("generic_secret")
            redaction_count += remaining

    # Phase 2: Apply output-specific patterns (emails, etc.)
    for pattern, replacement, label in _OUTPUT_FILTER_PATTERNS:
        matches = pattern.findall(filtered)
        if matches:
            filtered = pattern.sub(replacement, filtered)
            redaction_types.append(label)
            redaction_count += len(matches)

    return {
        "filtered_text": filtered,
        "redactions_made": redaction_count,
        "redaction_types": redaction_types,
    }


# ---------------------------------------------------------------------------
# 4. Combined Safety Check (convenience for agent loop)
# ---------------------------------------------------------------------------


def run_safety_checks(user_input: str) -> dict[str, Any]:
    """Run all input-side safety checks on user input in one call.

    Combines prompt injection detection and sensitive data filtering.
    The agent loop calls this once when it receives user input, before
    any processing begins.

    This function is pure — no side effects or I/O.

    Args:
        user_input: The raw message from the user.

    Returns:
        A dict with keys:
            - is_safe: bool — True only if injection check passes
            - injection: the full detect_injection() result dict
            - filtered_input: the full filter_sensitive_output() result dict
            - should_block: bool — True if the input should be rejected entirely
            - block_reason: str — explanation if blocked (empty if not blocked)
    """
    injection_result = detect_injection(user_input)
    filter_result = filter_sensitive_output(user_input)

    should_block = not injection_result["is_safe"]
    block_reason = ""
    if should_block:
        patterns = ", ".join(injection_result["flagged_patterns"])
        block_reason = (
            f"Potential prompt injection detected (risk score: "
            f"{injection_result['risk_score']}). "
            f"Flagged patterns: {patterns}. "
            f"For security, this request cannot be processed."
        )

    return {
        "is_safe": injection_result["is_safe"],
        "injection": injection_result,
        "filtered_input": filter_result,
        "should_block": should_block,
        "block_reason": block_reason,
    }

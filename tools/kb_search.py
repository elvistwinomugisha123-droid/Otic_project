"""Knowledge base search tool — searches IT runbooks for troubleshooting procedures.

This is a keyword-based search over local markdown runbook files. In the full system,
this would be replaced by RAG via ChromaDB + sentence-transformers for semantic search.
"""

import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_RUNBOOKS_DIR = _DATA_DIR / "runbooks"


def _parse_runbook_sections(filepath: Path) -> list[dict[str, str]]:
    """Parse a markdown runbook into its sections.

    Args:
        filepath: Path to the markdown runbook file.

    Returns:
        List of dicts with 'section' (heading text) and 'content' (section body).
    """
    text = filepath.read_text(encoding="utf-8")
    # Split on ## headings (level 2), keeping the heading text
    parts = re.split(r"^## ", text, flags=re.MULTILINE)

    sections: list[dict[str, str]] = []
    for part in parts[1:]:  # Skip the first split (before first ##)
        lines = part.strip().split("\n", 1)
        heading = lines[0].strip()
        body = lines[1].strip() if len(lines) > 1 else ""
        sections.append({"section": heading, "content": body})

    return sections


def _score_section(section_content: str, query_keywords: list[str]) -> float:
    """Score a section by counting how many query keywords appear in it.

    Args:
        section_content: The text content of the section.
        query_keywords: Lowercase keywords extracted from the search query.

    Returns:
        A relevance score (higher is more relevant).
    """
    content_lower = section_content.lower()
    score = 0.0
    for keyword in query_keywords:
        # Count occurrences of each keyword
        count = content_lower.count(keyword)
        if count > 0:
            # Diminishing returns for repeated matches of the same keyword
            score += 1.0 + (min(count, 5) - 1) * 0.2
    return score


def kb_search(query: str, top_k: int = 3) -> dict[str, Any]:
    """Search the IT knowledge base runbooks for relevant troubleshooting information.

    Reads all markdown files from data/runbooks/, parses them into sections,
    and scores each section by keyword match against the query.

    Args:
        query: The search query describing the IT issue or topic.
        top_k: Maximum number of runbook sections to return (default 3).

    Returns:
        A dict with keys:
            - results: list of dicts with runbook, section, content, relevance_score
            - total_results: count of results returned
            - query: the original query string

    Raises:
        FileNotFoundError: If the runbooks directory does not exist.
        ValueError: If query is empty.
    """
    if not query or not query.strip():
        raise ValueError("Search query cannot be empty")

    if not _RUNBOOKS_DIR.exists():
        raise FileNotFoundError(f"Runbooks directory not found: {_RUNBOOKS_DIR}")

    # Extract keywords (3+ chars, lowercased, deduplicated)
    query_keywords = list({
        word.lower()
        for word in re.findall(r"[a-zA-Z0-9]+", query)
        if len(word) >= 3
    })

    logger.info("KB search for query=%r with keywords=%s", query, query_keywords)

    # Score all sections across all runbooks
    scored_results: list[dict[str, Any]] = []

    for md_file in sorted(_RUNBOOKS_DIR.glob("*.md")):
        runbook_name = md_file.stem.replace("_", " ").title()
        sections = _parse_runbook_sections(md_file)

        for section in sections:
            full_text = f"{section['section']} {section['content']}"
            score = _score_section(full_text, query_keywords)

            if score > 0:
                scored_results.append({
                    "runbook": runbook_name,
                    "runbook_file": md_file.name,
                    "section": section["section"],
                    "content": section["content"][:500],  # Truncate long sections
                    "relevance_score": round(score, 2),
                })

    # Sort by score descending, take top_k
    scored_results.sort(key=lambda r: r["relevance_score"], reverse=True)
    top_results = scored_results[:top_k]

    logger.info("KB search returned %d results (from %d candidates)", len(top_results), len(scored_results))

    return {
        "results": top_results,
        "total_results": len(top_results),
        "query": query,
    }

"""Runbook indexer — chunks markdown runbooks and indexes them into ChromaDB.

Parses each runbook's ## headings into discrete chunks, embeds them using
sentence-transformers all-MiniLM-L6-v2, and stores them in a persistent
ChromaDB collection for semantic retrieval.
"""

import logging
import os
import re
from pathlib import Path
from typing import Any

import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_RUNBOOKS_DIR = _DATA_DIR / "runbooks"
_DEFAULT_CHROMA_DIR = str(_DATA_DIR / "chroma_db")

COLLECTION_NAME = "it_runbooks"
EMBEDDING_MODEL = "all-MiniLM-L6-v2"


class IndexingError(Exception):
    """Raised when runbook indexing fails."""


def chunk_runbook(filepath: Path) -> list[dict[str, str]]:
    """Parse a markdown runbook file into section-level chunks.

    Splits the file on level-2 headings (## ). Each chunk contains the
    section heading and its body text, plus metadata for the source file.

    Args:
        filepath: Path to a .md runbook file.

    Returns:
        List of dicts, each with keys:
            - id: deterministic chunk ID in format "{stem}_{index}"
            - content: full text of the section (heading + body)
            - runbook_name: human-readable runbook name
            - section_heading: the ## heading text
            - file_name: the source filename

    Raises:
        FileNotFoundError: If filepath does not exist.
        IndexingError: If the file contains no parseable sections.
    """
    if not filepath.exists():
        raise FileNotFoundError(f"Runbook file not found: {filepath}")

    text = filepath.read_text(encoding="utf-8")

    # Split on ## headings (level 2), keeping the heading text
    parts = re.split(r"^## ", text, flags=re.MULTILINE)

    if len(parts) <= 1:
        raise IndexingError(
            f"No ## sections found in {filepath.name}. "
            "Runbooks must have level-2 markdown headings."
        )

    stem = filepath.stem
    runbook_name = stem.replace("_", " ").title()
    chunks: list[dict[str, str]] = []

    for index, part in enumerate(parts[1:]):  # Skip text before first ##
        lines = part.strip().split("\n", 1)
        heading = lines[0].strip()
        body = lines[1].strip() if len(lines) > 1 else ""

        # Reconstruct the full section text with heading
        content = f"## {heading}\n\n{body}"

        chunks.append({
            "id": f"{stem}_{index}",
            "content": content,
            "runbook_name": runbook_name,
            "section_heading": heading,
            "file_name": filepath.name,
        })

    logger.info("Chunked %s into %d sections", filepath.name, len(chunks))
    return chunks


def index_runbooks(
    runbooks_dir: Path | None = None,
    force_reindex: bool = False,
) -> int:
    """Index all markdown runbooks into ChromaDB for semantic search.

    Chunks every .md file in the runbooks directory using chunk_runbook(),
    then upserts all chunks into the ChromaDB collection. Uses deterministic
    chunk IDs so re-indexing is idempotent.

    Args:
        runbooks_dir: Directory containing .md runbook files.
            Defaults to data/runbooks/ relative to this package.
        force_reindex: If True, deletes the existing collection and
            re-indexes from scratch. Default False.

    Returns:
        Number of chunks indexed.

    Raises:
        FileNotFoundError: If runbooks_dir does not exist or contains no .md files.
        IndexingError: If ChromaDB operations fail.
    """
    if runbooks_dir is None:
        runbooks_dir = _RUNBOOKS_DIR

    if not runbooks_dir.exists():
        raise FileNotFoundError(f"Runbooks directory not found: {runbooks_dir}")

    md_files = sorted(runbooks_dir.glob("*.md"))
    if not md_files:
        raise FileNotFoundError(f"No .md files found in {runbooks_dir}")

    # Initialize ChromaDB
    persist_path = os.environ.get("CHROMA_PERSIST_DIR", _DEFAULT_CHROMA_DIR)
    client = chromadb.PersistentClient(path=persist_path)
    ef = SentenceTransformerEmbeddingFunction(model_name=EMBEDDING_MODEL)

    # Handle force reindex
    if force_reindex:
        try:
            client.delete_collection(COLLECTION_NAME)
            logger.info("Deleted existing collection '%s' for reindexing", COLLECTION_NAME)
        except ValueError:
            pass  # Collection doesn't exist yet

    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=ef,
    )

    # Chunk all runbooks
    all_chunks: list[dict[str, str]] = []
    for md_file in md_files:
        try:
            chunks = chunk_runbook(md_file)
            all_chunks.extend(chunks)
        except IndexingError as exc:
            logger.warning("Skipping %s: %s", md_file.name, exc)

    if not all_chunks:
        raise IndexingError("No chunks produced from any runbook file")

    # Prepare data for ChromaDB upsert
    ids = [chunk["id"] for chunk in all_chunks]
    documents = [chunk["content"] for chunk in all_chunks]
    metadatas = [
        {
            "runbook_name": chunk["runbook_name"],
            "section_heading": chunk["section_heading"],
            "file_name": chunk["file_name"],
        }
        for chunk in all_chunks
    ]

    # Upsert into ChromaDB (idempotent with deterministic IDs)
    try:
        collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
    except Exception as exc:
        raise IndexingError(f"Failed to upsert chunks into ChromaDB: {exc}") from exc

    logger.info(
        "Indexed %d chunks from %d runbooks into collection '%s'",
        len(ids), len(md_files), COLLECTION_NAME,
    )
    return len(ids)

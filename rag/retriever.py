"""Runbook retriever — semantic search over indexed IT runbooks via ChromaDB.

Queries the ChromaDB collection to find the most relevant runbook sections
for a given IT issue description. Returns results with source citations
and relevance scores.
"""

import logging
import os
from pathlib import Path
from typing import Any

import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

from rag.indexer import index_runbooks, COLLECTION_NAME, EMBEDDING_MODEL

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_DEFAULT_CHROMA_DIR = str(_DATA_DIR / "chroma_db")


class RetrievalError(Exception):
    """Raised when retrieval from ChromaDB fails."""


def _get_collection() -> chromadb.Collection:
    """Get the ChromaDB collection with the configured embedding function.

    Returns:
        The "it_runbooks" ChromaDB collection.

    Raises:
        RetrievalError: If the collection does not exist.
    """
    persist_path = os.environ.get("CHROMA_PERSIST_DIR", _DEFAULT_CHROMA_DIR)
    client = chromadb.PersistentClient(path=persist_path)
    ef = SentenceTransformerEmbeddingFunction(model_name=EMBEDDING_MODEL)

    try:
        return client.get_collection(name=COLLECTION_NAME, embedding_function=ef)
    except ValueError as exc:
        raise RetrievalError(
            f"Collection '{COLLECTION_NAME}' not found. "
            "Run setup_rag() to index runbooks first."
        ) from exc


def retrieve(query: str, top_k: int = 3) -> dict[str, Any]:
    """Search indexed runbooks for sections semantically relevant to the query.

    Queries the ChromaDB collection using the provided natural language query
    and returns the most relevant runbook sections with source citations.

    Args:
        query: Natural language description of the IT issue or topic.
        top_k: Maximum number of results to return (default 3).

    Returns:
        A dict with keys:
            - results: list of dicts, each containing:
                - content: the chunk text
                - runbook: human-readable runbook name
                - section: section heading
                - source_file: filename of the runbook
                - relevance_score: float 0.0-1.0 (1.0 = most relevant)
            - total_results: number of results returned
            - query: the original query string

    Raises:
        ValueError: If query is empty.
        RetrievalError: If ChromaDB query fails.
    """
    if not query or not query.strip():
        raise ValueError("Search query cannot be empty")

    try:
        collection = _get_collection()
    except RetrievalError:
        raise
    except Exception as exc:
        raise RetrievalError(f"Failed to connect to ChromaDB: {exc}") from exc

    try:
        query_result = collection.query(
            query_texts=[query],
            n_results=top_k,
        )
    except Exception as exc:
        raise RetrievalError(f"ChromaDB query failed: {exc}") from exc

    # ChromaDB returns lists of lists (one inner list per query text)
    documents = query_result.get("documents", [[]])[0]
    metadatas = query_result.get("metadatas", [[]])[0]
    distances = query_result.get("distances", [[]])[0]

    results: list[dict[str, Any]] = []
    for doc, meta, dist in zip(documents, metadatas, distances):
        # Convert L2 distance to similarity score (0.0 to 1.0)
        # distance=0 → score=1.0, larger distances → lower scores
        relevance_score = round(1.0 / (1.0 + dist), 4)

        results.append({
            "content": doc,
            "runbook": meta.get("runbook_name", "Unknown"),
            "section": meta.get("section_heading", "Unknown"),
            "source_file": meta.get("file_name", "Unknown"),
            "relevance_score": relevance_score,
        })

    logger.info(
        "RAG retrieval for query=%r returned %d results",
        query, len(results),
    )

    return {
        "results": results,
        "total_results": len(results),
        "query": query,
    }


def setup_rag() -> None:
    """Initialize the RAG pipeline — indexes runbooks if the collection is empty.

    This should be called once at application startup. If the ChromaDB
    collection already contains documents, this is a no-op. If the collection
    is empty or does not exist, it triggers a full indexing run.

    Raises:
        RetrievalError: If setup fails due to ChromaDB or indexing errors.
    """
    persist_path = os.environ.get("CHROMA_PERSIST_DIR", _DEFAULT_CHROMA_DIR)

    try:
        client = chromadb.PersistentClient(path=persist_path)
        ef = SentenceTransformerEmbeddingFunction(model_name=EMBEDDING_MODEL)
        collection = client.get_or_create_collection(
            name=COLLECTION_NAME,
            embedding_function=ef,
        )
    except Exception as exc:
        raise RetrievalError(f"Failed to initialize ChromaDB: {exc}") from exc

    doc_count = collection.count()

    if doc_count > 0:
        logger.info(
            "RAG collection '%s' already contains %d documents. Skipping indexing.",
            COLLECTION_NAME, doc_count,
        )
        return

    logger.info(
        "RAG collection '%s' is empty. Indexing runbooks "
        "(first run may download the embedding model)...",
        COLLECTION_NAME,
    )

    try:
        chunk_count = index_runbooks()
    except Exception as exc:
        raise RetrievalError(f"Failed to index runbooks during setup: {exc}") from exc

    logger.info("RAG setup complete. Indexed %d chunks.", chunk_count)

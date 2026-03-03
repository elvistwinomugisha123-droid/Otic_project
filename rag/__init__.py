"""RAG pipeline — ChromaDB indexing and semantic retrieval over IT runbooks."""

from rag.indexer import index_runbooks, chunk_runbook
from rag.retriever import retrieve, setup_rag

__all__ = ["index_runbooks", "chunk_runbook", "retrieve", "setup_rag"]

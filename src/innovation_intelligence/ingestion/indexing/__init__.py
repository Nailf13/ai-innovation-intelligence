# src/innovation_intelligence/ingestion/vector_indexing/__init__.py
"""
Vector indexing module for chunking and embedding content.

Provides two ingestion modes:
- Single document: ingest_document() - API-ready, with manual upload
- Batch: index_documents() - folder discovery for bulk processing
"""
from innovation_intelligence.ingestion.indexing.pipeline import (
    VectorIndexingPipeline,
    DocumentIngestionResult,
    IndexingStats,
    # Single document ingestion (API-ready)
    ingest_document,
    # Batch ingestion (folder discovery)
    index_podcasts,
    index_documents,
    run_full_ingestion,
)

__all__ = [
    # Pipeline
    "VectorIndexingPipeline",
    # Result types
    "DocumentIngestionResult",
    "IndexingStats",
    # Single document (API-ready)
    "ingest_document",
    # Batch processing
    "index_podcasts",
    "index_documents",
    "run_full_ingestion",
]

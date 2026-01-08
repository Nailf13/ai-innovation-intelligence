# src/innovation_intelligence/search/__init__.py
"""
Search module for vector-based retrieval.
"""
from innovation_intelligence.search.vector_search import (
    VectorSearchService,
    search_chunks,
)

__all__ = ["VectorSearchService", "search_chunks"]

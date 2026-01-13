# src/innovation_intelligence/search/vector_search.py
"""
High-level vector search service.

Combines embedding generation with similarity search.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from innovation_intelligence.analysis.insights.embedder import embed_text
from innovation_intelligence.db.session import SessionLocal
from innovation_intelligence.db.repositories.vector_repository import (
    VectorRepository,
    SearchResult,
)
from innovation_intelligence.logger import get_logger

log = get_logger(__name__)


@dataclass
class SearchQuery:
    """Search query configuration."""
    query_text: str
    top_k: int = 3
    include_podcasts: bool = True
    include_documents: bool = True
    source_filter: Optional[str] = None
    speaker_filter: Optional[str] = None


class VectorSearchService:
    """
    Service for semantic search over embedded chunks.

    Handles:
    - Query embedding
    - Database search
    - Result formatting
    """

    def __init__(self, session=None):
        self.session = session or SessionLocal()
        self._owns_session = session is None
        self.repo = VectorRepository(self.session)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._owns_session:
            self.session.close()

    def search(
        self,
        query: str | SearchQuery,
        top_k: int = 3,
    ) -> List[SearchResult]:
        """
        Search for relevant chunks.

        Args:
            query: Query string or SearchQuery object
            top_k: Number of results (overridden by SearchQuery.top_k if provided)

        Returns:
            List of SearchResult ordered by similarity
        """
        # Normalize query
        if isinstance(query, str):
            query = SearchQuery(query_text=query, top_k=top_k)

        log.info(f"[SEARCH] Query: {query.query_text[:50]}...")

        # Generate query embedding
        query_embedding = embed_text(query.query_text)

        # Search based on configuration
        results = []

        if query.include_podcasts:
            podcast_results = self.repo.search_podcast_chunks(
                query_embedding=query_embedding,
                top_k=query.top_k,
                source_filter=query.source_filter if query.include_podcasts else None,
                speaker_filter=query.speaker_filter,
            )
            results.extend(podcast_results)

        if query.include_documents:
            doc_results = self.repo.search_document_chunks(
                query_embedding=query_embedding,
                top_k=query.top_k,
                source_filter=query.source_filter if not query.include_podcasts else None,
            )
            results.extend(doc_results)

        # Sort by similarity and limit
        results.sort(key=lambda x: x.score, reverse=True)
        results = results[:query.top_k]

        log.info(f"[SEARCH] Found {len(results)} results")
        return results

    def search_podcasts(
        self,
        query: str,
        top_k: int = 3,
        source_filter: Optional[str] = None,
        speaker_filter: Optional[str] = None,
    ) -> List[SearchResult]:
        """
        Search only podcast chunks.

        Args:
            query: Query string
            top_k: Number of results
            source_filter: Optional filter by episode
            speaker_filter: Optional filter by speaker

        Returns:
            List of SearchResult
        """
        query_embedding = embed_text(query)
        return self.repo.search_podcast_chunks(
            query_embedding=query_embedding,
            top_k=top_k,
            source_filter=source_filter,
            speaker_filter=speaker_filter,
        )

    def search_documents(
        self,
        query: str,
        top_k: int = 3,
        source_filter: Optional[str] = None,
    ) -> List[SearchResult]:
        """
        Search only document chunks.

        Args:
            query: Query string
            top_k: Number of results
            source_filter: Optional filter by document

        Returns:
            List of SearchResult
        """
        query_embedding = embed_text(query)
        return self.repo.search_document_chunks(
            query_embedding=query_embedding,
            top_k=top_k,
            source_filter=source_filter,
        )

    def get_stats(self) -> dict:
        """Get index statistics."""
        return {
            "podcast_chunks": self.repo.get_podcast_chunk_count(),
            "document_chunks": self.repo.get_document_chunk_count(),
        }


def search_chunks(
    query: str,
    top_k: int = 3,
    include_podcasts: bool = True,
    include_documents: bool = True,
) -> List[SearchResult]:
    """
    Convenience function for quick searches.

    Args:
        query: Query string
        top_k: Number of results
        include_podcasts: Search podcast chunks
        include_documents: Search document chunks

    Returns:
        List of SearchResult
    """
    with VectorSearchService() as service:
        return service.search(
            SearchQuery(
                query_text=query,
                top_k=top_k,
                include_podcasts=include_podcasts,
                include_documents=include_documents,
            )
        )


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    import json

    if len(sys.argv) < 2:
        print("Usage: python vector_search.py <query> [top_k]")
        sys.exit(1)

    query = sys.argv[1]
    top_k = int(sys.argv[2]) if len(sys.argv) > 2 else 5

    results = search_chunks(query, top_k=top_k)

    print(f"\n{'='*60}")
    print(f"Query: {query}")
    print(f"Results: {len(results)}")
    print(f"{'='*60}\n")

    for i, r in enumerate(results, 1):
        print(f"[{i}] Score: {r.score:.4f}")
        print(f"    Source: {r.source}")
        print(f"    Metadata: {json.dumps(r.metadata, default=str)}")
        print(f"    Text: {r.text[:150]}...")
        print()

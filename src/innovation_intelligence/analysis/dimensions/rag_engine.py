# src/innovation_intelligence/analysis/dimensions/rag_engine.py
"""
RAG (Retrieval-Augmented Generation) engine for dimension assessment.

This module provides:
- Semantic search over pgvector-indexed chunks
- Query enhancement with dimension hints
- Context formatting for LLM consumption
- Multi-source retrieval (podcasts + documents)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from innovation_intelligence.analysis.insights.embedder import embed_text
from innovation_intelligence.db.repositories.vector_repository import (
    VectorRepository,
    SearchResult,
)
from innovation_intelligence.llm.tools.dimension_tool import (
    DimensionType,
    DIMENSION_HINTS,
)
from innovation_intelligence.logger import get_logger

log = get_logger(__name__)


@dataclass
class RAGContext:
    """
    Retrieved context with metadata for dimension assessment.
    """
    formatted_text: str
    chunks: List[SearchResult]
    sources: List[str]
    total_chars: int
    query: str


@dataclass
class RAGEngineConfig:
    """Configuration for RAG retrieval."""
    # Number of chunks to retrieve per query
    top_k: int = 10

    # Minimum similarity threshold (0-1)
    min_similarity: float = 0.3

    # Maximum total context length
    max_context_chars: int = 200_000

    # Whether to include podcast chunks
    include_podcasts: bool = True

    # Whether to include document chunks
    include_documents: bool = True

    # Whether to deduplicate overlapping content
    deduplicate: bool = True

    # Deduplication similarity threshold
    dedup_threshold: float = 0.9


class RAGEngine:
    """
    RAG engine for retrieving context from pgvector-indexed chunks.

    Uses the VectorRepository to query indexed podcast and document
    chunks, formats them for LLM consumption, and provides dimension-
    specific query enhancement.
    """

    def __init__(
        self,
        session: Session,
        config: Optional[RAGEngineConfig] = None,
    ):
        """
        Initialize the RAG engine.

        Args:
            session: SQLAlchemy database session
            config: Optional configuration (uses defaults if None)
        """
        self.session = session
        self.config = config or RAGEngineConfig()
        self._repo = VectorRepository(session)

    def retrieve_context(
        self,
        query: str,
        *,
        dimension: Optional[DimensionType] = None,
        source_filter: Optional[str] = None,
        top_k: Optional[int] = None,
        precomputed_embedding: Optional[List[float]] = None,
    ) -> RAGContext:
        """
        Retrieve relevant context for a query.

        Args:
            query: The search query (typically insight name + description)
            dimension: Optional dimension to enhance query with hints
            source_filter: Optional filter to specific source
            top_k: Override default top_k
            precomputed_embedding: Optional pre-computed embedding to reuse

        Returns:
            RAGContext with formatted text and metadata
        """
        # Enhance query with dimension hints if provided
        enhanced_query = self._enhance_query(query, dimension)

        log.info(f"[RAG] Query: {enhanced_query[:100]}...")

        # Reuse precomputed embedding if available
        # Note: Even with dimension hints, the base insight embedding provides
        # good semantic similarity for retrieval. The hints mainly help the LLM
        # understand what to look for in the context.
        if precomputed_embedding is not None:
            query_embedding = precomputed_embedding
            log.debug("[RAG] Reusing precomputed embedding from UnitInsight")
        else:
            query_embedding = embed_text(enhanced_query)

        # Retrieve chunks
        k = top_k or self.config.top_k
        chunks = self._search_chunks(
            query_embedding=query_embedding,
            source_filter=source_filter,
            top_k=k,
        )

        # Filter by similarity threshold
        chunks = [c for c in chunks if c.score >= self.config.min_similarity]

        log.info(f"[RAG] Retrieved {len(chunks)} chunks (min_sim={self.config.min_similarity})")

        # Deduplicate if enabled
        if self.config.deduplicate and len(chunks) > 1:
            chunks = self._deduplicate_chunks(chunks)
            log.info(f"[RAG] After dedup: {len(chunks)} chunks")

        # Format context
        formatted_text = self._format_chunks(chunks)

        # Truncate if needed
        if len(formatted_text) > self.config.max_context_chars:
            formatted_text = formatted_text[:self.config.max_context_chars]
            log.warning(f"[RAG] Truncated context to {self.config.max_context_chars} chars")

        # Extract unique sources
        sources = list({c.source for c in chunks})

        return RAGContext(
            formatted_text=formatted_text,
            chunks=chunks,
            sources=sources,
            total_chars=len(formatted_text),
            query=enhanced_query,
        )

    def retrieve_for_insight(
        self,
        insight_name: str,
        insight_description: str,
        dimension: DimensionType,
        *,
        source_filter: Optional[str] = None,
        precomputed_embedding: Optional[List[float]] = None,
    ) -> RAGContext:
        """
        Retrieve context specifically for assessing an insight's dimension.

        Args:
            insight_name: Name of the insight
            insight_description: Description of the insight
            dimension: Which dimension to assess
            source_filter: Optional filter to specific source
            precomputed_embedding: Optional pre-computed embedding from UnitInsight

        Returns:
            RAGContext optimized for dimension assessment
        """
        # Build query from insight name + description
        query = f"{insight_name}. {insight_description}" if insight_description else insight_name

        return self.retrieve_context(
            query=query,
            dimension=dimension,
            source_filter=source_filter,
            precomputed_embedding=precomputed_embedding,
        )

    def _enhance_query(
        self,
        query: str,
        dimension: Optional[DimensionType] = None,
    ) -> str:
        """
        Enhance query with dimension-specific hints.

        Args:
            query: Base query text
            dimension: Optional dimension for hints

        Returns:
            Enhanced query string
        """
        if dimension is None:
            return query

        hint = DIMENSION_HINTS.get(dimension, "")
        if hint:
            return f"{query} {hint}"
        return query

    def _search_chunks(
        self,
        query_embedding: List[float],
        source_filter: Optional[str],
        top_k: int,
    ) -> List[SearchResult]:
        """
        Search for relevant chunks across configured sources.

        Args:
            query_embedding: Query embedding vector
            source_filter: Optional source filter
            top_k: Number of results per source type

        Returns:
            Combined list of search results
        """
        results: List[SearchResult] = []

        if self.config.include_podcasts:
            podcast_results = self._repo.search_podcast_chunks(
                query_embedding=query_embedding,
                top_k=top_k,
                source_filter=source_filter,
            )
            results.extend(podcast_results)

        if self.config.include_documents:
            doc_results = self._repo.search_document_chunks(
                query_embedding=query_embedding,
                top_k=top_k,
                source_filter=source_filter,
            )
            results.extend(doc_results)

        # Sort by similarity score
        results.sort(key=lambda x: x.score, reverse=True)

        # Return top_k overall
        return results[:top_k]

    def _deduplicate_chunks(
        self,
        chunks: List[SearchResult],
    ) -> List[SearchResult]:
        """
        Remove near-duplicate chunks based on text similarity.

        Uses simple text overlap ratio for efficiency.

        Args:
            chunks: List of search results

        Returns:
            Deduplicated list
        """
        if not chunks:
            return chunks

        unique: List[SearchResult] = []

        for chunk in chunks:
            is_dup = False
            chunk_text_lower = chunk.text.lower()

            for existing in unique:
                existing_text_lower = existing.text.lower()

                # Simple overlap check (Jaccard-like)
                chunk_words = set(chunk_text_lower.split())
                existing_words = set(existing_text_lower.split())

                if not chunk_words or not existing_words:
                    continue

                intersection = len(chunk_words & existing_words)
                union = len(chunk_words | existing_words)

                if union > 0:
                    overlap = intersection / union
                    if overlap >= self.config.dedup_threshold:
                        is_dup = True
                        break

            if not is_dup:
                unique.append(chunk)

        return unique

    def _format_chunks(self, chunks: List[SearchResult]) -> str:
        """
        Format chunks into a context string for LLM consumption.

        Each chunk includes metadata header for evidence extraction.

        Args:
            chunks: List of search results

        Returns:
            Formatted context string
        """
        if not chunks:
            return ""

        formatted_parts: List[str] = []

        for chunk in chunks:
            header = self._format_chunk_header(chunk)
            text = chunk.text.strip()

            if text:
                formatted_parts.append(f"{header}\n{text}")

        return "\n\n".join(formatted_parts)

    def _format_chunk_header(self, chunk: SearchResult) -> str:
        """
        Format the header line for a chunk.

        Format: [source | speaker/page | timestamps/section]

        Args:
            chunk: Search result

        Returns:
            Formatted header string
        """
        parts = [chunk.source]
        meta = chunk.metadata

        # Speaker (podcasts) or page (documents)
        if meta.get("speaker"):
            parts.append(meta["speaker"])
        elif meta.get("page") is not None:
            parts.append(f"Page {meta['page']}")

        # Timestamps (podcasts) or section (documents)
        if meta.get("start") is not None and meta.get("end") is not None:
            parts.append(f"{meta['start']:.1f}–{meta['end']:.1f}")
        elif meta.get("section"):
            parts.append(meta["section"])

        return f"[{' | '.join(parts)}]"

    def get_stats(self) -> Dict[str, Any]:
        """
        Get statistics about the indexed content.

        Returns:
            Dict with chunk counts and other stats
        """
        return {
            "podcast_chunks": self._repo.get_podcast_chunk_count(),
            "document_chunks": self._repo.get_document_chunk_count(),
            "config": {
                "top_k": self.config.top_k,
                "min_similarity": self.config.min_similarity,
                "include_podcasts": self.config.include_podcasts,
                "include_documents": self.config.include_documents,
            },
        }


# ---------------------------------------------------------------------
# Convenience factory
# ---------------------------------------------------------------------
def get_rag_engine(
    session: Session,
    config: Optional[RAGEngineConfig] = None,
) -> RAGEngine:
    """
    Factory function for RAGEngine.

    Args:
        session: SQLAlchemy database session
        config: Optional configuration

    Returns:
        Configured RAGEngine instance
    """
    return RAGEngine(session, config)

# src/innovation_intelligence/db/repositories/vector_repository.py
"""
Repository for vector chunk storage and retrieval.

Provides:
- Storing embedded chunks
- Similarity search
- Batch operations
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select, delete, func, text
from sqlalchemy.orm import Session

from innovation_intelligence.db.models import (
    PodcastChunkVector,
    DocumentChunkVector,
    EMBEDDING_DIM,
)
from innovation_intelligence.chunking.podcast_chunker import PodcastChunk
from innovation_intelligence.chunking.document_chunker import DocumentChunk
from innovation_intelligence.logger import get_logger

log = get_logger(__name__)


@dataclass
class SearchResult:
    """Result from similarity search."""
    chunk_id: str
    text: str
    score: float  # Cosine similarity (higher = more similar)
    source: str
    metadata: Dict[str, Any]


class VectorRepository:
    """
    Repository for vector chunk operations.
    """

    def __init__(self, session: Session):
        self.session = session

    # -----------------------------------------------------------------
    # Podcast chunk operations
    # -----------------------------------------------------------------
    def store_podcast_chunk(
        self,
        chunk: PodcastChunk,
        embedding: List[float],
    ) -> PodcastChunkVector:
        """
        Store a single podcast chunk with its embedding.

        Args:
            chunk: The podcast chunk
            embedding: The embedding vector (must be EMBEDDING_DIM dimensions)

        Returns:
            The created PodcastChunkVector
        """
        if len(embedding) != EMBEDDING_DIM:
            raise ValueError(
                f"Embedding dimension mismatch: got {len(embedding)}, "
                f"expected {EMBEDDING_DIM}"
            )

        # Parse episode_date if string
        episode_date = None
        if chunk.episode_date:
            if isinstance(chunk.episode_date, str):
                try:
                    episode_date = datetime.fromisoformat(chunk.episode_date)
                except ValueError:
                    pass
            elif isinstance(chunk.episode_date, datetime):
                episode_date = chunk.episode_date

        record = PodcastChunkVector(
            chunk_id=chunk.chunk_id,
            chunk_text=chunk.full_text,  # Include overlap for context
            embedding=embedding,
            speaker=chunk.speaker,
            speakers=chunk.speakers,
            start_time=chunk.start,
            end_time=chunk.end,
            source=chunk.source,
            episode_date=episode_date,
        )

        self.session.add(record)
        return record

    def store_podcast_chunks_batch(
        self,
        chunks: List[PodcastChunk],
        embeddings: List[List[float]],
    ) -> int:
        """
        Store multiple podcast chunks with embeddings.

        Args:
            chunks: List of podcast chunks
            embeddings: Corresponding embedding vectors

        Returns:
            Number of chunks stored
        """
        if len(chunks) != len(embeddings):
            raise ValueError(
                f"Chunk/embedding count mismatch: {len(chunks)} vs {len(embeddings)}"
            )

        stored = 0
        for chunk, embedding in zip(chunks, embeddings):
            try:
                self.store_podcast_chunk(chunk, embedding)
                stored += 1
            except Exception as e:
                log.error(f"Failed to store chunk {chunk.chunk_id}: {e}")

        self.session.commit()
        log.info(f"[VECTOR] Stored {stored}/{len(chunks)} podcast chunks")
        return stored

    def search_podcast_chunks(
        self,
        query_embedding: List[float],
        top_k: int = 10,
        source_filter: Optional[str] = None,
        speaker_filter: Optional[str] = None,
    ) -> List[SearchResult]:
        """
        Search for similar podcast chunks.

        Args:
            query_embedding: The query vector
            top_k: Number of results to return
            source_filter: Optional filter by source/episode
            speaker_filter: Optional filter by speaker

        Returns:
            List of SearchResult ordered by similarity
        """
        # Build query with cosine distance
        # pgvector uses <=> for cosine distance (1 - similarity)
        distance = PodcastChunkVector.embedding.cosine_distance(query_embedding)

        query = (
            select(
                PodcastChunkVector,
                (1 - distance).label('similarity')
            )
            .order_by(distance)
            .limit(top_k)
        )

        if source_filter:
            query = query.where(PodcastChunkVector.source == source_filter)

        if speaker_filter:
            query = query.where(PodcastChunkVector.speaker == speaker_filter)

        results = self.session.execute(query).all()

        return [
            SearchResult(
                chunk_id=row.PodcastChunkVector.chunk_id,
                text=row.PodcastChunkVector.chunk_text,
                score=float(row.similarity),
                source=row.PodcastChunkVector.source,
                metadata={
                    "speaker": row.PodcastChunkVector.speaker,
                    "speakers": row.PodcastChunkVector.speakers,
                    "start": row.PodcastChunkVector.start_time,
                    "end": row.PodcastChunkVector.end_time,
                    "episode_date": (
                        row.PodcastChunkVector.episode_date.isoformat()
                        if row.PodcastChunkVector.episode_date else None
                    ),
                },
            )
            for row in results
        ]

    def delete_podcast_chunks_by_source(self, source: str) -> int:
        """
        Delete all podcast chunks for a given source.

        Args:
            source: The source/episode identifier

        Returns:
            Number of chunks deleted
        """
        result = self.session.execute(
            delete(PodcastChunkVector).where(PodcastChunkVector.source == source)
        )
        self.session.commit()
        count = result.rowcount
        log.info(f"[VECTOR] Deleted {count} podcast chunks for source: {source}")
        return count

    def get_podcast_chunk_count(self) -> int:
        """Get total number of podcast chunks."""
        result = self.session.execute(
            select(func.count(PodcastChunkVector.id))
        )
        return result.scalar() or 0

    # -----------------------------------------------------------------
    # Document chunk operations
    # -----------------------------------------------------------------
    def store_document_chunk(
        self,
        chunk: DocumentChunk,
        embedding: List[float],
    ) -> DocumentChunkVector:
        """
        Store a single document chunk with its embedding.

        Args:
            chunk: The document chunk
            embedding: The embedding vector

        Returns:
            The created DocumentChunkVector
        """
        if len(embedding) != EMBEDDING_DIM:
            raise ValueError(
                f"Embedding dimension mismatch: got {len(embedding)}, "
                f"expected {EMBEDDING_DIM}"
            )

        # Parse document_date if string
        document_date = None
        if chunk.document_date:
            if isinstance(chunk.document_date, str):
                try:
                    document_date = datetime.fromisoformat(chunk.document_date)
                except ValueError:
                    pass
            elif isinstance(chunk.document_date, datetime):
                document_date = chunk.document_date

        record = DocumentChunkVector(
            chunk_id=chunk.chunk_id,
            chunk_text=chunk.full_text,  # Include overlap
            embedding=embedding,
            page=chunk.page,
            section=chunk.section,
            source=chunk.source,
            document_date=document_date,
        )

        self.session.add(record)
        return record

    def store_document_chunks_batch(
        self,
        chunks: List[DocumentChunk],
        embeddings: List[List[float]],
    ) -> int:
        """
        Store multiple document chunks with embeddings.

        Args:
            chunks: List of document chunks
            embeddings: Corresponding embedding vectors

        Returns:
            Number of chunks stored
        """
        if len(chunks) != len(embeddings):
            raise ValueError(
                f"Chunk/embedding count mismatch: {len(chunks)} vs {len(embeddings)}"
            )

        stored = 0
        for chunk, embedding in zip(chunks, embeddings):
            try:
                self.store_document_chunk(chunk, embedding)
                stored += 1
            except Exception as e:
                log.error(f"Failed to store chunk {chunk.chunk_id}: {e}")

        self.session.commit()
        log.info(f"[VECTOR] Stored {stored}/{len(chunks)} document chunks")
        return stored

    def search_document_chunks(
        self,
        query_embedding: List[float],
        top_k: int = 10,
        source_filter: Optional[str] = None,
    ) -> List[SearchResult]:
        """
        Search for similar document chunks.

        Args:
            query_embedding: The query vector
            top_k: Number of results to return
            source_filter: Optional filter by source/document

        Returns:
            List of SearchResult ordered by similarity
        """
        distance = DocumentChunkVector.embedding.cosine_distance(query_embedding)

        query = (
            select(
                DocumentChunkVector,
                (1 - distance).label('similarity')
            )
            .order_by(distance)
            .limit(top_k)
        )

        if source_filter:
            query = query.where(DocumentChunkVector.source == source_filter)

        results = self.session.execute(query).all()

        return [
            SearchResult(
                chunk_id=row.DocumentChunkVector.chunk_id,
                text=row.DocumentChunkVector.chunk_text,
                score=float(row.similarity),
                source=row.DocumentChunkVector.source,
                metadata={
                    "page": row.DocumentChunkVector.page,
                    "section": row.DocumentChunkVector.section,
                    "document_date": (
                        row.DocumentChunkVector.document_date.isoformat()
                        if row.DocumentChunkVector.document_date else None
                    ),
                },
            )
            for row in results
        ]

    def delete_document_chunks_by_source(self, source: str) -> int:
        """
        Delete all document chunks for a given source.

        Args:
            source: The document identifier

        Returns:
            Number of chunks deleted
        """
        result = self.session.execute(
            delete(DocumentChunkVector).where(DocumentChunkVector.source == source)
        )
        self.session.commit()
        count = result.rowcount
        log.info(f"[VECTOR] Deleted {count} document chunks for source: {source}")
        return count

    def get_document_chunk_count(self) -> int:
        """Get total number of document chunks."""
        result = self.session.execute(
            select(func.count(DocumentChunkVector.id))
        )
        return result.scalar() or 0

    # -----------------------------------------------------------------
    # Combined search
    # -----------------------------------------------------------------
    def search_all(
        self,
        query_embedding: List[float],
        top_k: int = 10,
        include_podcasts: bool = True,
        include_documents: bool = True,
    ) -> List[SearchResult]:
        """
        Search across all chunk types.

        Args:
            query_embedding: The query vector
            top_k: Number of results to return
            include_podcasts: Whether to search podcast chunks
            include_documents: Whether to search document chunks

        Returns:
            List of SearchResult ordered by similarity
        """
        results = []

        if include_podcasts:
            results.extend(self.search_podcast_chunks(query_embedding, top_k))

        if include_documents:
            results.extend(self.search_document_chunks(query_embedding, top_k))

        # Sort by similarity and return top_k
        results.sort(key=lambda x: x.score, reverse=True)
        return results[:top_k]


# -----------------------------------------------------------------
# Convenience functions
# -----------------------------------------------------------------
def get_vector_repository(session: Session) -> VectorRepository:
    """Factory function for VectorRepository."""
    return VectorRepository(session)

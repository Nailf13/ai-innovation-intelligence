from __future__ import annotations

from datetime import datetime
from enum import Enum

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Column,
    Integer,
    String,
    Text,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Enum as SQLEnum,
)
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import JSONB

from innovation_intelligence.db.base import Base


# BGE-M3 produces 1024-dimensional embeddings
EMBEDDING_DIM = 1024


class EpisodeStatus(str, Enum):
    """
    Podcast episode processing status lifecycle.

    Lifecycle: needs_processing → downloading → transcribing →
               indexing → ready → analyzing → analyzed
    """
    NEEDS_PROCESSING = "needs_processing"
    DOWNLOADING = "downloading"
    TRANSCRIBING = "transcribing"
    INDEXING = "indexing"
    READY = "ready"
    ANALYZING = "analyzing"
    ANALYZED = "analyzed"
    FAILED = "failed"


class DocumentStatus(str, Enum):
    """
    Document processing status lifecycle.

    Lifecycle: needs_processing → uploading → indexing →
               ready → analyzing → analyzed
    """
    NEEDS_PROCESSING = "needs_processing"
    UPLOADING = "uploading"
    INDEXING = "indexing"
    READY = "ready"
    ANALYZING = "analyzing"
    ANALYZED = "analyzed"
    FAILED = "failed"


class PodcastEpisode(Base):
    """
    Podcast episode acting as a source of extracted unit insights.
    """
    __tablename__ = "podcast_episodes"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, index=True)

    podcast_name = Column(String(255), nullable=False)
    episode_title = Column(String(512), nullable=False)
    audio_url = Column(String(1024), nullable=True)
    episode_date = Column(DateTime, nullable=True)

    # GCS storage URIs (primary storage)
    gcs_audio_uri = Column(String(1024), nullable=False)
    gcs_transcript_uri = Column(String(1024), nullable=True)

    # Audio metadata (for fast seeking / Range request calculation)
    audio_duration_seconds = Column(Float, nullable=True)
    audio_file_size = Column(BigInteger, nullable=True)

    # Processing status
    status = Column(
        SQLEnum(EpisodeStatus, values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
        default=EpisodeStatus.NEEDS_PROCESSING,
        index=True,
    )

    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    unit_insights = relationship(
        "UnitInsight",
        back_populates="episode",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return (
            f"<PodcastEpisode id={self.id} "
            f"podcast_name={self.podcast_name!r} "
            f"episode_title={self.episode_title!r}>"
        )


class Document(Base):
    """
    Document source (PDFs, reports, web exports, etc.).
    """
    __tablename__ = "documents"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, index=True)

    title = Column(String(512), nullable=False)
    source_type = Column(String(64), nullable=True)
    document_date = Column(DateTime, nullable=True)

    # GCS storage URIs (primary storage)
    gcs_document_uri = Column(String(1024), nullable=False)
    gcs_transcript_uri = Column(String(1024), nullable=True)

    # Processing status
    status = Column(
        SQLEnum(DocumentStatus, values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
        default=DocumentStatus.NEEDS_PROCESSING,
        index=True,
    )

    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    unit_insights = relationship(
        "UnitInsight",
        back_populates="document",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Document id={self.id} title={self.title!r}>"


class UnitInsight(Base):
    """
    Atomic extracted insight (trend or health stake).

    High granularity, noisy, never merged or deleted.
    Used as the raw material for semantic consolidation.
    """
    __tablename__ = "unit_insights"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, index=True)

    name = Column(String(512), nullable=False)
    description = Column(Text, nullable=False)
    type = Column(String(32), nullable=False)  # 'trend' | 'health_stake'

    # Semantic embedding (name + description)
    embedding = Column(JSONB, nullable=False)

    # Primary source (exactly one should be set)
    episode_id = Column(Integer, ForeignKey("podcast_episodes.id"), nullable=True)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=True)

    # Additional sources for merged insights
    # Format: {"episode_ids": [1, 2, 3], "document_ids": [4, 5]}
    additional_source_ids = Column(JSONB, nullable=True, default=None)

    # Semantic grouping
    macro_insight_id = Column(Integer, ForeignKey("macro_insights.id"), nullable=True)

    # Direct cluster assignment (for orphan unit insights)
    cluster_id = Column(Integer, ForeignKey("clusters.id"), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    # Relationships
    episode = relationship("PodcastEpisode", back_populates="unit_insights")
    document = relationship("Document", back_populates="unit_insights")
    macro_insight = relationship("MacroInsight", back_populates="unit_insights")
    cluster = relationship("Cluster", back_populates="unit_insights")
    dimensions = relationship("InsightDimension", back_populates="unit_insight", cascade="all, delete-orphan")


    def __repr__(self) -> str:
        return (
            f"<UnitInsight id={self.id} "
            f"name={self.name!r} type={self.type!r}>"
        )


class MacroInsight(Base):
    """
    Semantically clustered group of UnitInsights.

    Discovered via embedding similarity.
    Stable over time, expandable as new unit insights arrive.
    """
    __tablename__ = "macro_insights"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, index=True)

    name = Column(String(512), nullable=False)
    description = Column(Text, nullable=True)

    # Centroid embedding of attached unit insights
    centroid_embedding = Column(JSONB, nullable=False)

    #  framing
    cluster_id = Column(
        Integer,
        ForeignKey("clusters.id"),
        nullable=True,
    )

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    # Relationships
    unit_insights = relationship(
        "UnitInsight",
        back_populates="macro_insight",
    )

    cluster = relationship(
        "Cluster",
        back_populates="macro_insights",
    )

    def __repr__(self) -> str:
        return f"<MacroInsight id={self.id} name={self.name!r}>"


class Cluster(Base):
    """
    High-level  framing layer.

    Starts with predefined clusters but may expand if macro
    insights do not fit existing categories.
    """
    __tablename__ = "clusters"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, index=True)

    name = Column(String(255), unique=True, nullable=False)
    description = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    macro_insights = relationship(
        "MacroInsight",
        back_populates="cluster",
    )
    unit_insights = relationship(
        "UnitInsight",
        back_populates="cluster",
    )

    def __repr__(self) -> str:
        return f"<Cluster id={self.id} name={self.name!r}>"


class InsightDimension(Base):
    """
    Dimension assessment for a UnitInsight (adoption, horizon, expectation).
    Each dimension is inferred via RAG from a vector store.
    """
    __tablename__ = "insight_dimensions"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, index=True)
    
    unit_insight_id = Column(Integer, ForeignKey("unit_insights.id"), nullable=False)
    
    # Dimension type: 'adoption' | 'horizon' | 'expectation'
    dimension_type = Column(String(32), nullable=False)
    
    # Value: e.g., 'nascent', 'experimentation', 'mainstream' for adoption
    #        'short_term', 'mid_term', 'long_term' for horizon
    #        'low', 'medium', 'high' for expectation
    value = Column(String(64), nullable=False)
    
    # Optional confidence score (0.0 - 1.0)
    confidence = Column(Float, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    unit_insight = relationship("UnitInsight", back_populates="dimensions")
    evidence_chunks = relationship("DimensionEvidence", back_populates="dimension", cascade="all, delete-orphan")


class DimensionEvidence(Base):
    """
    Evidence chunk retrieved from vector DB to support a dimension assessment.
    """
    __tablename__ = "dimension_evidence"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, index=True)

    dimension_id = Column(Integer, ForeignKey("insight_dimensions.id"), nullable=False)

    # The retrieved text chunk
    chunk_text = Column(Text, nullable=False)

    # Similarity score from vector search
    similarity_score = Column(Float, nullable=True)

    # Source reference (file path, chunk index, etc.)
    source_ref = Column(String(1024), nullable=True)

    # Metadata from the vector chunk (JSONB for flexibility)
    # Format: {"start_time": 123.4, "end_time": 145.6, "page": 5, "section": "Introduction"}
    chunk_metadata = Column(JSONB, nullable=True)

    # Relationships
    dimension = relationship("InsightDimension", back_populates="evidence_chunks")


# ============================================================================
# Vector Models (pgvector-based chunk storage)
# ============================================================================

class PodcastChunkVector(Base):
    """
    Embedded chunk from a podcast transcript.

    Stores:
    - Chunk text and embedding
    - Timestamps
    - Episode metadata

    Note: source column format is "podcast_name - episode_title"
    """
    __tablename__ = "podcast_chunk_vectors"

    id = Column(Integer, primary_key=True, index=True)

    # Chunk identification
    chunk_id = Column(String(255), unique=True, nullable=False, index=True)

    # Core content
    chunk_text = Column(Text, nullable=False)
    embedding = Column(Vector(EMBEDDING_DIM), nullable=False)

    # Timestamps (seconds)
    start_time = Column(Float, nullable=True)
    end_time = Column(Float, nullable=True)

    # Source info: format is "podcast_name - episode_title"
    source = Column(String(1024), nullable=False, index=True)
    episode_date = Column(DateTime, nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)

    # Create HNSW index for fast similarity search
    __table_args__ = (
        Index(
            'ix_podcast_chunk_embedding_hnsw',
            embedding,
            postgresql_using='hnsw',
            postgresql_with={'m': 16, 'ef_construction': 64},
            postgresql_ops={'embedding': 'vector_cosine_ops'},
        ),
        {"extend_existing": True},
    )

    def __repr__(self) -> str:
        return (
            f"<PodcastChunkVector id={self.id} "
            f"chunk_id={self.chunk_id!r} "
            f"source={self.source!r}>"
        )


class DocumentChunkVector(Base):
    """
    Embedded chunk from a document.

    Stores:
    - Chunk text and embedding
    - Page/section information
    - Document metadata

    Note: source column format is the document title
    """
    __tablename__ = "document_chunk_vectors"

    id = Column(Integer, primary_key=True, index=True)

    # Chunk identification
    chunk_id = Column(String(255), unique=True, nullable=False, index=True)

    # Core content
    chunk_text = Column(Text, nullable=False)
    embedding = Column(Vector(EMBEDDING_DIM), nullable=False)

    # Location info
    page = Column(Integer, nullable=True)
    section = Column(String(512), nullable=True)

    # Source info: format is the document title
    source = Column(String(512), nullable=False, index=True)
    document_date = Column(DateTime, nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)

    # Create HNSW index for fast similarity search
    __table_args__ = (
        Index(
            'ix_document_chunk_embedding_hnsw',
            embedding,
            postgresql_using='hnsw',
            postgresql_with={'m': 16, 'ef_construction': 64},
            postgresql_ops={'embedding': 'vector_cosine_ops'},
        ),
        {"extend_existing": True},
    )

    def __repr__(self) -> str:
        return (
            f"<DocumentChunkVector id={self.id} "
            f"chunk_id={self.chunk_id!r} "
            f"source={self.source!r}>"
        )

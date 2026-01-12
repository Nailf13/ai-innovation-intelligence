# src/innovation_intelligence/ingestion/podcasts/__init__.py
"""
Podcast ingestion module.

Provides production-ready pipeline for:
- Podcast discovery via Podcast Index API
- Audio downloading with optional trimming
- Upload to Google Cloud Storage (GCS)
- Transcription via Google Speech API (Chirp)
- Transcript transformation
- Chunking for downstream RAG and insight extraction
"""

# ---------------------------------------------------------------------
# Models / DTOs
# ---------------------------------------------------------------------
from innovation_intelligence.ingestion.podcasts.models import (
    IngestionStatus,
    IngestionStage,
    PodcastInfo,
    EpisodeInfo,
    IngestionRequest,
    EpisodeIngestionResult,
    IngestionBatchResult,
)

# ---------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------
from innovation_intelligence.ingestion.podcasts.services import (
    PodcastSearchService,
    AudioDownloadService,
    TranscriptionService,
    DEFAULT_RELEVANCE_KEYWORDS,
)

# ---------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------
from innovation_intelligence.ingestion.podcasts.pipeline import (
    PodcastIngestionPipeline,
    ingest_podcast,
    ingest_episodes,
    ProgressCallback,
)

# ---------------------------------------------------------------------
# Transcript processing
# ---------------------------------------------------------------------
from innovation_intelligence.ingestion.podcasts.transcript_transformer import (
    transform_transcript,
    TransformedTranscript,
    load_transcript,
)

# ---------------------------------------------------------------------
# Transcription (Chirp)
# ---------------------------------------------------------------------
from innovation_intelligence.ingestion.podcasts.chirp_transcription import (
    ChirpTranscriptionService,
)

# ---------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------
from innovation_intelligence.ingestion.podcasts.podcast_chunker import (
    PodcastChunker,
    PodcastChunk,
    chunk_podcast_transcript,
)

# ---------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------
__all__ = [
    # Models
    "IngestionStatus",
    "IngestionStage",
    "PodcastInfo",
    "EpisodeInfo",
    "IngestionRequest",
    "EpisodeIngestionResult",
    "IngestionBatchResult",

    # Services
    "PodcastSearchService",
    "AudioDownloadService",
    "TranscriptionService",
    "DEFAULT_RELEVANCE_KEYWORDS",

    # Pipeline
    "PodcastIngestionPipeline",
    "ingest_podcast",
    "ingest_episodes",
    "ProgressCallback",

    # Transcript
    "transform_transcript",
    "TransformedTranscript",
    "load_transcript",

    # Chirp
    "ChirpTranscriptionService",

    # Chunking
    "PodcastChunker",
    "PodcastChunk",
    "chunk_podcast_transcript",
]

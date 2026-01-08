# src/innovation_intelligence/ingestion/podcasts/__init__.py
"""
Podcast ingestion module.

Provides production-ready pipeline for:
- Podcast discovery via Podcast Index API
- Audio downloading with optional trimming
- Transcription via Modal GPU (WhisperX)
- Speaker identification
- Vector indexing for RAG

Usage:
    from innovation_intelligence.ingestion.podcasts import (
        PodcastIngestionPipeline,
        ingest_podcast,
        ingest_episodes,
    )

    # Quick ingestion from podcast name
    result = ingest_podcast("Huberman Lab", max_episodes=5)

    # Or with full control
    from innovation_intelligence.ingestion.podcasts import (
        PodcastSearchService,
        EpisodeInfo,
        IngestionRequest,
    )

    search = PodcastSearchService()
    podcast, episodes = search.discover_relevant_episodes("Huberman Lab")

    with PodcastIngestionPipeline() as pipeline:
        result = pipeline.ingest(IngestionRequest(episodes=episodes))
"""

# Models and DTOs
from innovation_intelligence.ingestion.podcasts.models import (
    IngestionStatus,
    IngestionStage,
    PodcastInfo,
    EpisodeInfo,
    IngestionRequest,
    EpisodeIngestionResult,
    IngestionBatchResult,
)

# Services
from innovation_intelligence.ingestion.podcasts.services import (
    PodcastSearchService,
    AudioDownloadService,
    TranscriptionService,
    DEFAULT_RELEVANCE_KEYWORDS,
)

# Pipeline
from innovation_intelligence.ingestion.podcasts.pipeline import (
    PodcastIngestionPipeline,
    ingest_podcast,
    ingest_episodes,
    ProgressCallback,
)

# Transcript transformation
from innovation_intelligence.ingestion.podcasts.transcript_transformer import (
    transform_transcript,
    TransformedTranscript,
    load_transcript,
    apply_speaker_mapping,
)

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
    "apply_speaker_mapping",
]

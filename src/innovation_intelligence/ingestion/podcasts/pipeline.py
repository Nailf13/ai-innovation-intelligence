# src/innovation_intelligence/ingestion/podcasts/pipeline.py
"""
Production-ready podcast ingestion pipeline using Google Speech API (Chirp).

Orchestrates the full ingestion workflow:
1. Episode discovery and selection
2. Audio downloading (with optional trimming) and upload to GCS
3. Transcription via Google Speech API (Chirp)
4. Vector indexing

Designed for frontend integration where users can:
- Search podcasts and select episodes
- Monitor ingestion progress
- Configure pipeline options
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Any
import json

from innovation_intelligence.config import settings
from innovation_intelligence.logger import get_logger
from innovation_intelligence.db.session import SessionLocal

# Local models and services
from innovation_intelligence.ingestion.podcasts.models import (
    EpisodeInfo,
    PodcastInfo,
    IngestionRequest,
    IngestionStatus,
    IngestionStage,
    EpisodeIngestionResult,
    IngestionBatchResult,
)
from innovation_intelligence.ingestion.podcasts.services import (
    PodcastSearchService,
    AudioDownloadService,
    TranscriptionService,
)

# Repository
from innovation_intelligence.db.repositories.episode_repository import EpisodeRepository

# Vector indexing
from innovation_intelligence.ingestion.podcasts.podcast_chunker import (
    PodcastChunker,
)
from innovation_intelligence.analysis.insights.embedder import embed_texts_batch
from innovation_intelligence.db.repositories.vector_repository import VectorRepository


log = get_logger(__name__)


# Progress callback type: (stage, status, message, progress_pct)
ProgressCallback = Callable[[IngestionStage, IngestionStatus, str, float], None]


class PodcastIngestionPipeline:
    """
    Production-ready pipeline for ingesting podcast episodes.

    Uses Google Cloud services:
    - GCS for audio and transcript storage
    - Google Speech API (Chirp) for transcription

    Supports:
    - Batch ingestion of multiple episodes
    - Progress callbacks for frontend updates
    - Configurable pipeline stages
    - Error handling with per-episode status

    Usage:
        pipeline = PodcastIngestionPipeline()

        # From manual episode selection
        request = IngestionRequest(episodes=[episode1, episode2])
        result = pipeline.ingest(request)

        # From podcast search
        result = pipeline.ingest_from_podcast(
            podcast_name="Huberman Lab",
            max_episodes=5,
        )
    """

    def __init__(
        self,
        session=None,
        progress_callback: Optional[ProgressCallback] = None,
        language_codes: Optional[List[str]] = None,
    ):
        # Session management
        self._session = session
        self._owns_session = session is None

        # Language codes for transcription
        self.language_codes = language_codes or ["en-US"]

        # Services (GCS-first mode)
        self.download_service = AudioDownloadService()
        self.transcription_service = TranscriptionService(
            language_codes=self.language_codes,
        )
        self.search_service = PodcastSearchService()
        self.chunker = PodcastChunker()

        # Progress tracking
        self.progress_callback = progress_callback

    @property
    def session(self):
        if self._session is None:
            self._session = SessionLocal()
        return self._session

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._owns_session and self._session is not None:
            self._session.close()

    # -----------------------------------------------------------------
    # Progress reporting
    # -----------------------------------------------------------------
    def _report_progress(
        self,
        stage: IngestionStage,
        status: IngestionStatus,
        message: str,
        progress_pct: float = 0.0,
    ) -> None:
        """Report progress to callback if registered."""
        if self.progress_callback:
            self.progress_callback(stage, status, message, progress_pct)

    # -----------------------------------------------------------------
    # Main ingestion methods
    # -----------------------------------------------------------------
    def ingest(
        self,
        request: IngestionRequest,
        skip_existing: bool = True,
    ) -> IngestionBatchResult:
        """
        Ingest a batch of episodes.

        Args:
            request: IngestionRequest with episodes and config
            skip_existing: Skip episodes already in database

        Returns:
            IngestionBatchResult with per-episode results
        """
        batch_result = IngestionBatchResult(started_at=datetime.utcnow())

        total = len(request.episodes)
        log.info(f"[INGEST] Starting ingestion of {total} episodes")

        for i, episode in enumerate(request.episodes, 1):
            progress_pct = (i - 1) / total * 100

            self._report_progress(
                IngestionStage.DOWNLOAD,
                IngestionStatus.PENDING,
                f"Processing episode {i}/{total}: {episode.title[:50]}...",
                progress_pct,
            )

            result = self._ingest_episode(
                episode=episode,
                podcast_info=request.podcast_info,
                skip_existing=skip_existing,
                skip_indexing=request.skip_indexing,
                trim_audio_seconds=request.trim_audio_seconds,
            )

            batch_result.results.append(result)

            log.info(
                f"[INGEST] Episode {i}/{total} - "
                f"Status: {result.status.value}, "
                f"Chunks: {result.chunks_indexed}"
            )

        batch_result.completed_at = datetime.utcnow()

        log.info(f"[INGEST] Batch complete:\n{batch_result}")
        return batch_result

    def ingest_from_podcast(
        self,
        podcast_name: str,
        max_episodes: int = 5,
        relevance_keywords: Optional[List[str]] = None,
        min_relevance_score: float = 0.0,
        skip_existing: bool = True,
        skip_indexing: bool = False,
        trim_audio_seconds: int = 0,
    ) -> IngestionBatchResult:
        """
        Discover and ingest episodes from a podcast.

        Args:
            podcast_name: Name of the podcast to search for
            max_episodes: Maximum episodes to ingest
            relevance_keywords: Keywords for filtering relevant episodes
            min_relevance_score: Minimum relevance score (0.0-1.0)
            skip_existing: Skip episodes already in database
            skip_indexing: Skip vector indexing stage
            trim_audio_seconds: Seconds to trim from audio start

        Returns:
            IngestionBatchResult
        """
        log.info(f"[INGEST] Discovering episodes for: {podcast_name}")

        self._report_progress(
            IngestionStage.SEARCH,
            IngestionStatus.PENDING,
            f"Searching for podcast: {podcast_name}",
            0.0,
        )

        # Discover relevant episodes
        podcast_info, episodes = self.search_service.discover_relevant_episodes(
            podcast_name=podcast_name,
            limit=max_episodes,
            keywords=relevance_keywords,
        )

        if not episodes:
            log.warning(f"[INGEST] No episodes found for: {podcast_name}")
            return IngestionBatchResult(
                started_at=datetime.utcnow(),
                completed_at=datetime.utcnow(),
            )

        # Filter by relevance score
        if min_relevance_score > 0:
            episodes = [e for e in episodes if e.relevance_score >= min_relevance_score]

        log.info(f"[INGEST] Found {len(episodes)} relevant episodes")

        # Create ingestion request
        request = IngestionRequest(
            episodes=episodes,
            podcast_info=podcast_info,
            skip_indexing=skip_indexing,
            trim_audio_seconds=trim_audio_seconds,
        )

        return self.ingest(request, skip_existing=skip_existing)

    # -----------------------------------------------------------------
    # Single episode processing
    # -----------------------------------------------------------------
    def _ingest_episode(
        self,
        episode: EpisodeInfo,
        podcast_info: Optional[PodcastInfo] = None,
        skip_existing: bool = True,
        skip_indexing: bool = False,
        trim_audio_seconds: int = 0,
    ) -> EpisodeIngestionResult:
        """
        Ingest a single episode through all pipeline stages.

        Stages:
        1. Download audio and upload to GCS
        2. Transcribe using Google Speech API (Chirp)
        3. Vector indexing (optional)

        Returns:
            EpisodeIngestionResult with status and metadata
        """
        result = EpisodeIngestionResult(
            episode_info=episode,
            status=IngestionStatus.PENDING,
            started_at=datetime.utcnow(),
        )

        repo = EpisodeRepository(self.session)

        # Get podcast name
        podcast_name = podcast_info.title if podcast_info else f"Feed_{episode.feed_id}"

        try:
            # ---------------------------------------------------------
            # Check for existing episode
            # ---------------------------------------------------------
            if skip_existing and repo.exists_by_audio_url(episode.audio_url):
                existing = repo.find_by_audio_url(episode.audio_url)
                result.status = IngestionStatus.SKIPPED
                result.db_episode_id = existing.id if existing else None
                result.completed_at = datetime.utcnow()
                log.info(f"[INGEST] Skipping existing episode: {episode.title[:50]}")
                return result

            # ---------------------------------------------------------
            # Stage 1: Download and Upload to GCS (GCS-first mode)
            # ---------------------------------------------------------
            result.status = IngestionStatus.DOWNLOADING
            self._report_progress(
                IngestionStage.DOWNLOAD,
                IngestionStatus.DOWNLOADING,
                f"Downloading and uploading to GCS: {episode.title[:50]}...",
                10.0,
            )

            try:
                # Download directly to GCS (uses temporary files only for trimming)
                gcs_audio_uri = self.download_service.download_to_gcs(
                    episode,
                    trim_start=trim_audio_seconds,
                )

                result.status = IngestionStatus.DOWNLOADED
                result.gcs_audio_uri = gcs_audio_uri
                log.info(f"[INGEST] Audio uploaded to GCS: {gcs_audio_uri}")

            except Exception as e:
                result.status = IngestionStatus.FAILED
                result.error_stage = IngestionStage.DOWNLOAD
                result.error_message = str(e)
                result.completed_at = datetime.utcnow()
                log.error(f"[INGEST] Download failed: {e}")
                return result

            # ---------------------------------------------------------
            # Create DB entry (GCS-first: only GCS URIs stored)
            # ---------------------------------------------------------
            db_episode, created = repo.get_or_create(
                podcast_name=podcast_name,
                episode_title=episode.title,
                audio_url=episode.audio_url,
                episode_date=episode.published_at,
                gcs_audio_uri=gcs_audio_uri,
            )
            result.db_episode_id = db_episode.id

            if not created:
                # Update GCS URI if needed
                repo.update_gcs_uris(db_episode.id, gcs_audio_uri=gcs_audio_uri)

            # ---------------------------------------------------------
            # Stage 2: Transcription (GCS-first mode)
            # ---------------------------------------------------------
            result.status = IngestionStatus.TRANSCRIBING
            self._report_progress(
                IngestionStage.TRANSCRIBE,
                IngestionStatus.TRANSCRIBING,
                f"Transcribing with Chirp from GCS: {episode.title[:50]}...",
                30.0,
            )

            try:
                # Transcribe from GCS using Chirp (returns GCS URI)
                gcs_transcript_uri = self.transcription_service.transcribe_from_gcs(
                    gcs_audio_uri=gcs_audio_uri,
                    episode=episode,
                )
                result.status = IngestionStatus.TRANSCRIBED
                result.gcs_transcript_uri = gcs_transcript_uri

                # Update DB with transcript GCS URI
                repo.update_gcs_uris(db_episode.id, gcs_transcript_uri=gcs_transcript_uri)

            except Exception as e:
                result.status = IngestionStatus.FAILED
                result.error_stage = IngestionStage.TRANSCRIBE
                result.error_message = str(e)
                result.completed_at = datetime.utcnow()
                log.error(f"[INGEST] Transcription failed: {e}")
                return result

            # ---------------------------------------------------------
            # Stage 3: Vector Indexing
            # ---------------------------------------------------------
            if not skip_indexing:
                result.status = IngestionStatus.INDEXING
                self._report_progress(
                    IngestionStage.INDEX,
                    IngestionStatus.INDEXING,
                    f"Indexing: {episode.title[:50]}...",
                    70.0,
                )

                try:
                    chunks_indexed = self._index_episode(
                        gcs_transcript_uri=gcs_transcript_uri,
                        episode=episode,
                        db_episode_id=db_episode.id,
                    )
                    result.chunks_indexed = chunks_indexed
                    result.status = IngestionStatus.INDEXED

                except Exception as e:
                    # Indexing failure is not fatal
                    log.warning(f"[INGEST] Vector indexing failed: {e}")

            # ---------------------------------------------------------
            # Complete
            # ---------------------------------------------------------
            result.status = IngestionStatus.COMPLETED
            result.completed_at = datetime.utcnow()

            self._report_progress(
                IngestionStage.INDEX,
                IngestionStatus.COMPLETED,
                f"Completed: {episode.title[:50]}",
                100.0,
            )

            return result

        except Exception as e:
            result.status = IngestionStatus.FAILED
            result.error_message = str(e)
            result.completed_at = datetime.utcnow()
            log.error(f"[INGEST] Episode ingestion failed: {e}")
            return result

    # -----------------------------------------------------------------
    # Vector indexing
    # -----------------------------------------------------------------
    def _index_episode(
        self,
        gcs_transcript_uri: str,
        episode: EpisodeInfo,
        db_episode_id: int,
    ) -> int:
        """
        Index a transcript into the vector database (GCS-first mode).

        Args:
            gcs_transcript_uri: GCS URI of the transcript
            episode: Episode info
            db_episode_id: Database episode ID

        Returns:
            Number of chunks indexed
        """
        from innovation_intelligence.db.models import PodcastEpisode

        # Load transcript from GCS
        data = self.transcription_service.get_transcript(gcs_transcript_uri)

        segments = data.get("segments", [])

        if not segments:
            log.warning(f"[INDEX] No segments in transcript: {gcs_transcript_uri}")
            return 0

        # Query database to get podcast_name and episode_title for proper source formatting
        db_episode = self.session.query(PodcastEpisode).filter(
            PodcastEpisode.id == db_episode_id
        ).first()

        if db_episode:
            # Format source as "podcast_name - episode_title"
            source = f"{db_episode.podcast_name} - {db_episode.episode_title}"
            podcast_name = db_episode.podcast_name
            episode_title = db_episode.episode_title
            log.info(f"[INDEX] Using source from DB: {source}")
        else:
            # Fallback to using episode info from RSS feed
            source = episode.title
            podcast_name = None
            episode_title = episode.title
            log.warning(f"[INDEX] Episode not found in DB, using RSS title as source: {source}")

        # Chunk the transcript
        chunks = self.chunker.chunk(
            segments=segments,
            source=source,
            episode_date=episode.published_at.isoformat() if episode.published_at else None,
            metadata={
                "podcast_name": podcast_name,
                "episode_title": episode_title,
                "episode_id": episode.episode_id,
                "feed_id": episode.feed_id,
                "db_episode_id": db_episode_id,
            },
        )

        if not chunks:
            log.warning(f"[INDEX] No chunks created for: {source}")
            return 0

        log.info(f"[INDEX] Created {len(chunks)} chunks for {source}")

        # Generate embeddings
        texts_to_embed = [chunk.full_text for chunk in chunks]
        embeddings = embed_texts_batch(texts_to_embed)

        # Store in database
        vector_repo = VectorRepository(self.session)

        # Delete existing chunks for this source (re-indexing)
        vector_repo.delete_podcast_chunks_by_source(source)

        # Store new chunks
        stored = vector_repo.store_podcast_chunks_batch(chunks, embeddings)

        log.info(f"[INDEX] Stored {stored} chunks for {source}")
        return stored


# -----------------------------------------------------------------
# Convenience functions
# -----------------------------------------------------------------
def ingest_podcast(
    podcast_name: str,
    max_episodes: int = 5,
    relevance_keywords: Optional[List[str]] = None,
    skip_indexing: bool = False,
    trim_audio_seconds: int = 0,
    language_codes: Optional[List[str]] = None,
) -> IngestionBatchResult:
    """
    Convenience function to ingest episodes from a podcast.

    Args:
        podcast_name: Name of the podcast
        max_episodes: Maximum episodes to ingest
        relevance_keywords: Keywords for filtering
        skip_indexing: Skip vector indexing
        trim_audio_seconds: Seconds to trim from start
        language_codes: Language codes for transcription (default: ["en-US"])

    Returns:
        IngestionBatchResult
    """
    with PodcastIngestionPipeline(language_codes=language_codes) as pipeline:
        return pipeline.ingest_from_podcast(
            podcast_name=podcast_name,
            max_episodes=max_episodes,
            relevance_keywords=relevance_keywords,
            skip_indexing=skip_indexing,
            trim_audio_seconds=trim_audio_seconds,
        )


def ingest_episodes(
    episodes: List[EpisodeInfo],
    podcast_info: Optional[PodcastInfo] = None,
    skip_indexing: bool = False,
    language_codes: Optional[List[str]] = None,
) -> IngestionBatchResult:
    """
    Convenience function to ingest specific episodes.

    Args:
        episodes: List of EpisodeInfo objects
        podcast_info: Optional podcast metadata
        skip_indexing: Skip vector indexing
        language_codes: Language codes for transcription (default: ["en-US"])

    Returns:
        IngestionBatchResult
    """
    request = IngestionRequest(
        episodes=episodes,
        podcast_info=podcast_info,
        skip_indexing=skip_indexing,
    )

    with PodcastIngestionPipeline(language_codes=language_codes) as pipeline:
        return pipeline.ingest(request)


# -----------------------------------------------------------------
# CLI
# -----------------------------------------------------------------
if __name__ == "__main__":
    import sys

    def print_usage():
        print("""
Podcast Ingestion Pipeline (Google Speech API - Chirp)

Usage:
    python pipeline.py <podcast_name> [options]

Options:
    --max-episodes N        Maximum episodes to ingest (default: 5)
    --trim N                Trim N seconds from audio start (default: 0)
    --no-indexing           Skip vector indexing
    --keywords "k1,k2,k3"   Comma-separated relevance keywords
    --language CODE         Language code for transcription (default: en-US)

Examples:
    python pipeline.py "Huberman Lab" --max-episodes 3
    python pipeline.py "The Peter Attia Drive" --keywords "health,longevity"
    python pipeline.py "French Podcast" --language fr-FR
""")

    args = sys.argv[1:]

    if not args or "--help" in args or "-h" in args:
        print_usage()
        sys.exit(0)

    # Parse arguments
    podcast_name = args[0]
    max_episodes = 5
    trim_seconds = 0
    skip_indexing = False
    keywords: Optional[List[str]] = None
    language_codes: Optional[List[str]] = None

    i = 1
    while i < len(args):
        arg = args[i]
        if arg == "--max-episodes" and i + 1 < len(args):
            max_episodes = int(args[i + 1])
            i += 2
        elif arg == "--trim" and i + 1 < len(args):
            trim_seconds = int(args[i + 1])
            i += 2
        elif arg == "--no-indexing":
            skip_indexing = True
            i += 1
        elif arg == "--keywords" and i + 1 < len(args):
            keywords = [k.strip() for k in args[i + 1].split(",")]
            i += 2
        elif arg == "--language" and i + 1 < len(args):
            language_codes = [args[i + 1]]
            i += 2
        else:
            i += 1

    # Run ingestion
    print(f"\nIngesting from: {podcast_name}")
    print(f"  Max episodes: {max_episodes}")
    print(f"  Trim seconds: {trim_seconds}")
    print(f"  Keywords: {keywords or 'default health keywords'}")
    print(f"  Language: {language_codes[0] if language_codes else 'en-US'}")
    print()

    result = ingest_podcast(
        podcast_name=podcast_name,
        max_episodes=max_episodes,
        relevance_keywords=keywords,
        skip_indexing=skip_indexing,
        trim_audio_seconds=trim_seconds,
        language_codes=language_codes,
    )

    print("\n" + "=" * 60)
    print(result)

    if result.failed > 0:
        print("\nFailed episodes:")
        for r in result.results:
            if r.status == IngestionStatus.FAILED:
                print(f"  - {r.episode_info.title[:50]}")
                print(f"    Stage: {r.error_stage.value if r.error_stage else 'unknown'}")
                print(f"    Error: {r.error_message}")

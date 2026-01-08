# src/innovation_intelligence/ingestion/podcasts/pipeline.py
"""
Production-ready podcast ingestion pipeline.

Orchestrates the full ingestion workflow:
1. Episode discovery and selection
2. Audio downloading (with optional trimming)
3. Transcription via Modal GPU
4. Speaker identification
5. Vector indexing

Designed for frontend integration where users can:
- Search podcasts and select episodes
- Monitor ingestion progress
- Configure pipeline options
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Any

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

# Transcript transformation (speaker identification)
from innovation_intelligence.ingestion.podcasts.transcript_transformer import (
    transform_transcript,
    TransformedTranscript,
)

# Vector indexing
from innovation_intelligence.chunking.podcast_chunker import (
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
        audio_dir: Optional[Path] = None,
        transcripts_dir: Optional[Path] = None,
        use_modal: bool = True,
        progress_callback: Optional[ProgressCallback] = None,
    ):
        # Session management
        self._session = session
        self._owns_session = session is None

        # Directories
        self.audio_dir = audio_dir or settings.paths.audio_dir
        self.transcripts_dir = transcripts_dir or settings.paths.transcripts_dir

        # Services
        self.download_service = AudioDownloadService(output_dir=self.audio_dir)
        self.transcription_service = TranscriptionService(
            output_dir=self.transcripts_dir,
            use_modal=use_modal,
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
                skip_speaker_identification=request.skip_speaker_identification,
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
        skip_speaker_identification: bool = False,
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
            skip_speaker_identification: Skip speaker ID stage
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
            skip_speaker_identification=skip_speaker_identification,
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
        skip_speaker_identification: bool = False,
        skip_indexing: bool = False,
        trim_audio_seconds: int = 0,
    ) -> EpisodeIngestionResult:
        """
        Ingest a single episode through all pipeline stages.

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
            # Stage 1: Download
            # ---------------------------------------------------------
            result.status = IngestionStatus.DOWNLOADING
            self._report_progress(
                IngestionStage.DOWNLOAD,
                IngestionStatus.DOWNLOADING,
                f"Downloading: {episode.title[:50]}...",
                10.0,
            )

            try:
                if trim_audio_seconds > 0:
                    audio_path = self.download_service.download_and_trim(
                        episode,
                        trim_start=trim_audio_seconds,
                    )
                else:
                    audio_path = self.download_service.download(episode)

                result.audio_path = audio_path
                result.status = IngestionStatus.DOWNLOADED
            except Exception as e:
                result.status = IngestionStatus.FAILED
                result.error_stage = IngestionStage.DOWNLOAD
                result.error_message = str(e)
                result.completed_at = datetime.utcnow()
                log.error(f"[INGEST] Download failed: {e}")
                return result

            # ---------------------------------------------------------
            # Create DB entry
            # ---------------------------------------------------------
            db_episode, created = repo.get_or_create(
                podcast_name=podcast_name,
                episode_title=episode.title,
                audio_url=episode.audio_url,
                audio_path=audio_path,
                episode_date=episode.published_at,
            )
            result.db_episode_id = db_episode.id

            if not created:
                # Update audio path if needed
                repo.update_audio_path(db_episode.id, str(audio_path))

            # ---------------------------------------------------------
            # Stage 2: Transcription
            # ---------------------------------------------------------
            result.status = IngestionStatus.TRANSCRIBING
            self._report_progress(
                IngestionStage.TRANSCRIBE,
                IngestionStatus.TRANSCRIBING,
                f"Transcribing: {episode.title[:50]}...",
                30.0,
            )

            try:
                transcript_path = self.transcription_service.transcribe(
                    audio_path=audio_path,
                    episode=episode,
                    db_episode_id=db_episode.id,
                )
                result.transcript_path = transcript_path
                result.status = IngestionStatus.TRANSCRIBED

                # Update DB with transcript path
                repo.update_transcript_path(db_episode.id, str(transcript_path))

            except Exception as e:
                result.status = IngestionStatus.FAILED
                result.error_stage = IngestionStage.TRANSCRIBE
                result.error_message = str(e)
                result.completed_at = datetime.utcnow()
                log.error(f"[INGEST] Transcription failed: {e}")
                return result

            # ---------------------------------------------------------
            # Stage 3: Speaker Identification
            # ---------------------------------------------------------
            speaker_map: Dict[str, str] = {}

            if not skip_speaker_identification:
                result.status = IngestionStatus.IDENTIFYING_SPEAKERS
                self._report_progress(
                    IngestionStage.SPEAKER_ID,
                    IngestionStatus.IDENTIFYING_SPEAKERS,
                    f"Identifying speakers: {episode.title[:50]}...",
                    60.0,
                )

                try:
                    transformed = transform_transcript(
                        transcript_path,
                        episode_title=episode.title,
                        run_identification=True,
                    )
                    speaker_map = transformed.speaker_map
                    result.speaker_map = speaker_map
                    result.status = IngestionStatus.SPEAKERS_IDENTIFIED

                except Exception as e:
                    # Speaker identification failure is not fatal
                    log.warning(f"[INGEST] Speaker identification failed: {e}")
                    speaker_map = {}

            # ---------------------------------------------------------
            # Stage 4: Vector Indexing
            # ---------------------------------------------------------
            if not skip_indexing:
                result.status = IngestionStatus.INDEXING
                self._report_progress(
                    IngestionStage.INDEX,
                    IngestionStatus.INDEXING,
                    f"Indexing: {episode.title[:50]}...",
                    80.0,
                )

                try:
                    chunks_indexed = self._index_episode(
                        transcript_path=transcript_path,
                        episode=episode,
                        db_episode_id=db_episode.id,
                        speaker_map=speaker_map,
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
        transcript_path: Path,
        episode: EpisodeInfo,
        db_episode_id: int,
        speaker_map: Optional[Dict[str, str]] = None,
    ) -> int:
        """
        Index a transcript into the vector database.

        Returns:
            Number of chunks indexed
        """
        from innovation_intelligence.ingestion.podcasts.transcript_transformer import (
            load_transcript,
            apply_speaker_mapping,
        )

        # Load transcript
        data = load_transcript(transcript_path)
        segments = data.get("segments", [])

        if not segments:
            log.warning(f"[INDEX] No segments in transcript: {transcript_path}")
            return 0

        # Apply speaker mapping if available
        if speaker_map:
            segments = apply_speaker_mapping(segments, speaker_map)

        # Create source identifier
        source = transcript_path.stem

        # Chunk the transcript
        chunks = self.chunker.chunk(
            segments=segments,
            source=source,
            episode_date=episode.published_at.isoformat() if episode.published_at else None,
            metadata={
                "episode_title": episode.title,
                "episode_id": episode.episode_id,
                "feed_id": episode.feed_id,
                "db_episode_id": db_episode_id,
                "speaker_map": speaker_map or {},
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
    skip_speaker_identification: bool = False,
    skip_indexing: bool = False,
    trim_audio_seconds: int = 0,
) -> IngestionBatchResult:
    """
    Convenience function to ingest episodes from a podcast.

    Args:
        podcast_name: Name of the podcast
        max_episodes: Maximum episodes to ingest
        relevance_keywords: Keywords for filtering
        skip_speaker_identification: Skip speaker ID
        skip_indexing: Skip vector indexing
        trim_audio_seconds: Seconds to trim from start

    Returns:
        IngestionBatchResult
    """
    with PodcastIngestionPipeline() as pipeline:
        return pipeline.ingest_from_podcast(
            podcast_name=podcast_name,
            max_episodes=max_episodes,
            relevance_keywords=relevance_keywords,
            skip_speaker_identification=skip_speaker_identification,
            skip_indexing=skip_indexing,
            trim_audio_seconds=trim_audio_seconds,
        )


def ingest_episodes(
    episodes: List[EpisodeInfo],
    podcast_info: Optional[PodcastInfo] = None,
    skip_speaker_identification: bool = False,
    skip_indexing: bool = False,
) -> IngestionBatchResult:
    """
    Convenience function to ingest specific episodes.

    Args:
        episodes: List of EpisodeInfo objects
        podcast_info: Optional podcast metadata
        skip_speaker_identification: Skip speaker ID
        skip_indexing: Skip vector indexing

    Returns:
        IngestionBatchResult
    """
    request = IngestionRequest(
        episodes=episodes,
        podcast_info=podcast_info,
        skip_speaker_identification=skip_speaker_identification,
        skip_indexing=skip_indexing,
    )

    with PodcastIngestionPipeline() as pipeline:
        return pipeline.ingest(request)


# -----------------------------------------------------------------
# CLI
# -----------------------------------------------------------------
if __name__ == "__main__":
    import sys

    def print_usage():
        print("""
Podcast Ingestion Pipeline

Usage:
    python pipeline.py <podcast_name> [options]

Options:
    --max-episodes N        Maximum episodes to ingest (default: 5)
    --trim N                Trim N seconds from audio start (default: 0)
    --no-speaker-id         Skip speaker identification
    --no-indexing           Skip vector indexing
    --keywords "k1,k2,k3"   Comma-separated relevance keywords

Examples:
    python pipeline.py "Huberman Lab" --max-episodes 3
    python pipeline.py "The Peter Attia Drive" --keywords "health,longevity"
""")

    args = sys.argv[1:]

    if not args or "--help" in args or "-h" in args:
        print_usage()
        sys.exit(0)

    # Parse arguments
    podcast_name = args[0]
    max_episodes = 5
    trim_seconds = 0
    skip_speaker_id = False
    skip_indexing = False
    keywords: Optional[List[str]] = None

    i = 1
    while i < len(args):
        arg = args[i]
        if arg == "--max-episodes" and i + 1 < len(args):
            max_episodes = int(args[i + 1])
            i += 2
        elif arg == "--trim" and i + 1 < len(args):
            trim_seconds = int(args[i + 1])
            i += 2
        elif arg == "--no-speaker-id":
            skip_speaker_id = True
            i += 1
        elif arg == "--no-indexing":
            skip_indexing = True
            i += 1
        elif arg == "--keywords" and i + 1 < len(args):
            keywords = [k.strip() for k in args[i + 1].split(",")]
            i += 2
        else:
            i += 1

    # Run ingestion
    print(f"\nIngesting from: {podcast_name}")
    print(f"  Max episodes: {max_episodes}")
    print(f"  Trim seconds: {trim_seconds}")
    print(f"  Keywords: {keywords or 'default health keywords'}")
    print()

    result = ingest_podcast(
        podcast_name=podcast_name,
        max_episodes=max_episodes,
        relevance_keywords=keywords,
        skip_speaker_identification=skip_speaker_id,
        skip_indexing=skip_indexing,
        trim_audio_seconds=trim_seconds,
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

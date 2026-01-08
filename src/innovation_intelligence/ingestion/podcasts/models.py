# src/innovation_intelligence/ingestion/podcasts/models.py
"""
Data models and enums for podcast ingestion pipeline.

Provides:
- Processing status enums
- Data transfer objects for pipeline stages
- Type-safe representations of external API responses
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional


class IngestionStatus(str, Enum):
    """Status of an episode in the ingestion pipeline."""
    PENDING = "pending"
    DOWNLOADING = "downloading"
    DOWNLOADED = "downloaded"
    TRANSCRIBING = "transcribing"
    TRANSCRIBED = "transcribed"
    IDENTIFYING_SPEAKERS = "identifying_speakers"
    SPEAKERS_IDENTIFIED = "speakers_identified"
    INDEXING = "indexing"
    INDEXED = "indexed"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class IngestionStage(str, Enum):
    """Stages of the ingestion pipeline."""
    SEARCH = "search"
    DOWNLOAD = "download"
    TRANSCRIBE = "transcribe"
    SPEAKER_ID = "speaker_identification"
    INDEX = "index"


@dataclass
class PodcastInfo:
    """Information about a podcast feed from Podcast Index API."""
    feed_id: int
    title: str
    author: Optional[str] = None
    description: Optional[str] = None
    url: Optional[str] = None
    image_url: Optional[str] = None
    language: Optional[str] = None
    categories: List[str] = field(default_factory=list)
    episode_count: Optional[int] = None

    @classmethod
    def from_api_response(cls, data: Dict[str, Any]) -> "PodcastInfo":
        """Create from Podcast Index API response."""
        return cls(
            feed_id=data.get("id", 0),
            title=data.get("title", "Unknown"),
            author=data.get("author"),
            description=data.get("description"),
            url=data.get("url"),
            image_url=data.get("image"),
            language=data.get("language"),
            categories=list(data.get("categories", {}).values()) if data.get("categories") else [],
            episode_count=data.get("episodeCount"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "feed_id": self.feed_id,
            "title": self.title,
            "author": self.author,
            "description": self.description,
            "url": self.url,
            "image_url": self.image_url,
            "language": self.language,
            "categories": self.categories,
            "episode_count": self.episode_count,
        }


@dataclass
class EpisodeInfo:
    """Information about a podcast episode from Podcast Index API."""
    episode_id: int
    feed_id: int
    title: str
    audio_url: str
    published_at: Optional[datetime] = None
    duration: Optional[int] = None  # seconds
    description: Optional[str] = None
    episode_number: Optional[int] = None
    season_number: Optional[int] = None
    image_url: Optional[str] = None

    # Computed fields
    relevance_score: float = 0.0

    @classmethod
    def from_api_response(cls, data: Dict[str, Any], feed_id: int) -> "EpisodeInfo":
        """Create from Podcast Index API response."""
        published_at = None
        if data.get("datePublished"):
            try:
                published_at = datetime.fromtimestamp(data["datePublished"])
            except (ValueError, TypeError):
                pass

        return cls(
            episode_id=data.get("id", 0),
            feed_id=feed_id,
            title=data.get("title", "Unknown"),
            audio_url=data.get("enclosureUrl", ""),
            published_at=published_at,
            duration=data.get("duration"),
            description=data.get("description"),
            episode_number=data.get("episode"),
            season_number=data.get("season"),
            image_url=data.get("image"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "feed_id": self.feed_id,
            "title": self.title,
            "audio_url": self.audio_url,
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "duration": self.duration,
            "description": self.description,
            "episode_number": self.episode_number,
            "season_number": self.season_number,
            "image_url": self.image_url,
            "relevance_score": self.relevance_score,
        }


@dataclass
class IngestionRequest:
    """
    Request to ingest one or more podcast episodes.

    Can be created from:
    - Manual selection of episodes
    - Search results from frontend
    - Automated discovery
    """
    episodes: List[EpisodeInfo]
    podcast_info: Optional[PodcastInfo] = None

    # Pipeline configuration
    skip_speaker_identification: bool = False
    skip_indexing: bool = False
    trim_audio_seconds: int = 0  # Seconds to trim from start

    # Filtering (for automated discovery)
    max_episodes: int = 10
    relevance_keywords: List[str] = field(default_factory=list)
    min_relevance_score: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "episodes": [e.to_dict() for e in self.episodes],
            "podcast_info": self.podcast_info.to_dict() if self.podcast_info else None,
            "skip_speaker_identification": self.skip_speaker_identification,
            "skip_indexing": self.skip_indexing,
            "trim_audio_seconds": self.trim_audio_seconds,
            "max_episodes": self.max_episodes,
            "relevance_keywords": self.relevance_keywords,
            "min_relevance_score": self.min_relevance_score,
        }


@dataclass
class EpisodeIngestionResult:
    """Result of ingesting a single episode."""
    episode_info: EpisodeInfo
    status: IngestionStatus
    db_episode_id: Optional[int] = None

    # Paths
    audio_path: Optional[Path] = None
    transcript_path: Optional[Path] = None

    # Processing metadata
    speaker_map: Dict[str, str] = field(default_factory=dict)
    chunks_indexed: int = 0

    # Timing
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    # Error handling
    error_message: Optional[str] = None
    error_stage: Optional[IngestionStage] = None

    @property
    def is_success(self) -> bool:
        return self.status == IngestionStatus.COMPLETED

    @property
    def duration_seconds(self) -> Optional[float]:
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "episode_info": self.episode_info.to_dict(),
            "status": self.status.value,
            "db_episode_id": self.db_episode_id,
            "audio_path": str(self.audio_path) if self.audio_path else None,
            "transcript_path": str(self.transcript_path) if self.transcript_path else None,
            "speaker_map": self.speaker_map,
            "chunks_indexed": self.chunks_indexed,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "duration_seconds": self.duration_seconds,
            "error_message": self.error_message,
            "error_stage": self.error_stage.value if self.error_stage else None,
            "is_success": self.is_success,
        }


@dataclass
class IngestionBatchResult:
    """Result of ingesting a batch of episodes."""
    results: List[EpisodeIngestionResult] = field(default_factory=list)

    # Aggregate stats
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def succeeded(self) -> int:
        return sum(1 for r in self.results if r.is_success)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if r.status == IngestionStatus.FAILED)

    @property
    def skipped(self) -> int:
        return sum(1 for r in self.results if r.status == IngestionStatus.SKIPPED)

    @property
    def total_chunks_indexed(self) -> int:
        return sum(r.chunks_indexed for r in self.results)

    @property
    def duration_seconds(self) -> Optional[float]:
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "results": [r.to_dict() for r in self.results],
            "total": self.total,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "skipped": self.skipped,
            "total_chunks_indexed": self.total_chunks_indexed,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "duration_seconds": self.duration_seconds,
        }

    def __str__(self) -> str:
        duration = f" in {self.duration_seconds:.1f}s" if self.duration_seconds else ""
        return (
            f"Ingestion Results{duration}:\n"
            f"  Total: {self.total}\n"
            f"  Succeeded: {self.succeeded}\n"
            f"  Failed: {self.failed}\n"
            f"  Skipped: {self.skipped}\n"
            f"  Chunks indexed: {self.total_chunks_indexed}"
        )

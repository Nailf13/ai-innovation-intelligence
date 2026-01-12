from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import List, Optional

from sqlalchemy.orm import Session

from innovation_intelligence.db.models import PodcastEpisode


class EpisodeRepository:
    """
    Repository for PodcastEpisode database operations.

    Provides CRUD operations and queries for podcast episodes,
    including support for the ingestion pipeline.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    def create(
        self,
        podcast_name: str,
        episode_title: str,
        gcs_audio_uri: str,
        audio_url: Optional[str] = None,
        episode_date: Optional[datetime] = None,
        gcs_transcript_uri: Optional[str] = None,
    ) -> PodcastEpisode:
        """Create a new episode record (GCS-first mode)."""
        episode = PodcastEpisode(
            podcast_name=podcast_name,
            episode_title=episode_title,
            audio_url=audio_url,
            episode_date=episode_date,
            gcs_audio_uri=gcs_audio_uri,
            gcs_transcript_uri=gcs_transcript_uri,
        )
        self.session.add(episode)
        self.session.commit()
        self.session.refresh(episode)
        return episode

    def get(self, episode_id: int) -> Optional[PodcastEpisode]:
        """Get episode by ID."""
        return self.session.get(PodcastEpisode, episode_id)

    def delete(self, episode_id: int) -> bool:
        """Delete episode by ID. Returns True if deleted."""
        ep = self.get(episode_id)
        if ep is not None:
            self.session.delete(ep)
            self.session.commit()
            return True
        return False

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------
    def find_by_podcast_and_title(
        self,
        podcast_name: str,
        episode_title: str,
    ) -> Optional[PodcastEpisode]:
        """Find episode by podcast name and title."""
        return (
            self.session.query(PodcastEpisode)
            .filter(
                PodcastEpisode.podcast_name == podcast_name,
                PodcastEpisode.episode_title == episode_title,
            )
            .first()
        )

    def find_by_audio_url(self, audio_url: str) -> Optional[PodcastEpisode]:
        """Find episode by audio URL (for deduplication)."""
        return (
            self.session.query(PodcastEpisode)
            .filter(PodcastEpisode.audio_url == audio_url)
            .first()
        )

    def exists_by_audio_url(self, audio_url: str) -> bool:
        """Check if an episode with this audio URL exists."""
        return self.find_by_audio_url(audio_url) is not None

    def list_all(self, limit: Optional[int] = None) -> List[PodcastEpisode]:
        """List all episodes, ordered by creation date."""
        query = (
            self.session.query(PodcastEpisode)
            .order_by(PodcastEpisode.created_at.desc())
        )
        if limit:
            query = query.limit(limit)
        return query.all()

    def list_with_transcript(self) -> List[PodcastEpisode]:
        """List episodes that have transcripts (in GCS)."""
        return (
            self.session.query(PodcastEpisode)
            .filter(PodcastEpisode.gcs_transcript_uri.isnot(None))
            .order_by(PodcastEpisode.created_at.desc())
            .all()
        )

    def list_without_transcript(self) -> List[PodcastEpisode]:
        """List episodes that don't have transcripts yet."""
        return (
            self.session.query(PodcastEpisode)
            .filter(PodcastEpisode.gcs_transcript_uri.is_(None))
            .order_by(PodcastEpisode.created_at.desc())
            .all()
        )

    def list_by_podcast(self, podcast_name: str) -> List[PodcastEpisode]:
        """List all episodes for a specific podcast."""
        return (
            self.session.query(PodcastEpisode)
            .filter(PodcastEpisode.podcast_name == podcast_name)
            .order_by(PodcastEpisode.episode_date.desc())
            .all()
        )

    def count_all(self) -> int:
        """Count total episodes."""
        return self.session.query(PodcastEpisode).count()

    def count_with_transcript(self) -> int:
        """Count episodes with transcripts (in GCS)."""
        return (
            self.session.query(PodcastEpisode)
            .filter(PodcastEpisode.gcs_transcript_uri.isnot(None))
            .count()
        )

    def count(self) -> int:
        """Count total episodes (alias for count_all)."""
        return self.count_all()

    def count_with_transcripts(self) -> int:
        """Count episodes with transcripts (alias for count_with_transcript)."""
        return self.count_with_transcript()

    # ------------------------------------------------------------------
    # Updates
    # ------------------------------------------------------------------
    def update(
        self,
        episode_id: int,
        episode_date: Optional[datetime] = None,
        gcs_audio_uri: Optional[str] = None,
        gcs_transcript_uri: Optional[str] = None,
    ) -> PodcastEpisode:
        """Update fields on an episode (GCS-first mode)."""
        episode = self.get(episode_id)
        if episode is None:
            raise ValueError(f"Episode {episode_id} not found")

        if episode_date is not None:
            episode.episode_date = episode_date
        if gcs_audio_uri is not None:
            episode.gcs_audio_uri = gcs_audio_uri
        if gcs_transcript_uri is not None:
            episode.gcs_transcript_uri = gcs_transcript_uri

        self.session.commit()
        self.session.refresh(episode)
        return episode

    def update_gcs_uris(
        self,
        episode_id: int,
        gcs_audio_uri: Optional[str] = None,
        gcs_transcript_uri: Optional[str] = None,
    ) -> PodcastEpisode:
        """Update GCS URIs for an episode."""
        episode = self.get(episode_id)
        if episode is None:
            raise ValueError(f"Episode {episode_id} not found")

        if gcs_audio_uri is not None:
            episode.gcs_audio_uri = gcs_audio_uri
        if gcs_transcript_uri is not None:
            episode.gcs_transcript_uri = gcs_transcript_uri

        self.session.commit()
        self.session.refresh(episode)
        return episode

    # ------------------------------------------------------------------
    # Upsert (for ingestion pipeline)
    # ------------------------------------------------------------------
    def get_or_create(
        self,
        podcast_name: str,
        episode_title: str,
        audio_url: str,
        episode_date: Optional[datetime] = None,
        gcs_audio_uri: Optional[str] = None,
        gcs_transcript_uri: Optional[str] = None,
    ) -> tuple[PodcastEpisode, bool]:
        """
        Get existing episode or create new one (GCS-first mode).

        Returns:
            Tuple of (episode, created) where created is True if new.
        """
        # First try to find by audio URL (most reliable identifier)
        existing = self.find_by_audio_url(audio_url)
        if existing:
            return existing, False

        # Then try by podcast name + title
        existing = self.find_by_podcast_and_title(podcast_name, episode_title)
        if existing:
            return existing, False

        # Create new episode
        episode = self.create(
            podcast_name=podcast_name,
            episode_title=episode_title,
            audio_url=audio_url,
            episode_date=episode_date,
            gcs_audio_uri=gcs_audio_uri,
            gcs_transcript_uri=gcs_transcript_uri,
        )
        return episode, True

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
        audio_path: Path | str,
        audio_url: Optional[str] = None,
        episode_date: Optional[datetime] = None,
        transcript_path: Optional[str] = None,
    ) -> PodcastEpisode:
        """Create a new episode record."""
        episode = PodcastEpisode(
            podcast_name=podcast_name,
            episode_title=episode_title,
            audio_path=str(audio_path),
            audio_url=audio_url,
            episode_date=episode_date,
            transcript_path=transcript_path,
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
        """List episodes that have transcripts."""
        return (
            self.session.query(PodcastEpisode)
            .filter(PodcastEpisode.transcript_path.isnot(None))
            .order_by(PodcastEpisode.created_at.desc())
            .all()
        )

    def list_without_transcript(self) -> List[PodcastEpisode]:
        """List episodes that don't have transcripts yet."""
        return (
            self.session.query(PodcastEpisode)
            .filter(PodcastEpisode.transcript_path.is_(None))
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
        """Count episodes with transcripts."""
        return (
            self.session.query(PodcastEpisode)
            .filter(PodcastEpisode.transcript_path.isnot(None))
            .count()
        )

    # ------------------------------------------------------------------
    # Updates
    # ------------------------------------------------------------------
    def update_transcript_path(
        self,
        episode_id: int,
        transcript_path: str,
    ) -> PodcastEpisode:
        """Update the transcript path for an episode."""
        episode = self.get(episode_id)
        if episode is None:
            raise ValueError(f"Episode {episode_id} not found")

        episode.transcript_path = transcript_path
        self.session.commit()
        self.session.refresh(episode)
        return episode

    def update_audio_path(
        self,
        episode_id: int,
        audio_path: str,
    ) -> PodcastEpisode:
        """Update the audio path for an episode."""
        episode = self.get(episode_id)
        if episode is None:
            raise ValueError(f"Episode {episode_id} not found")

        episode.audio_path = audio_path
        self.session.commit()
        self.session.refresh(episode)
        return episode

    def update(
        self,
        episode_id: int,
        audio_path: Optional[str] = None,
        transcript_path: Optional[str] = None,
        episode_date: Optional[datetime] = None,
    ) -> PodcastEpisode:
        """Update multiple fields on an episode."""
        episode = self.get(episode_id)
        if episode is None:
            raise ValueError(f"Episode {episode_id} not found")

        if audio_path is not None:
            episode.audio_path = audio_path
        if transcript_path is not None:
            episode.transcript_path = transcript_path
        if episode_date is not None:
            episode.episode_date = episode_date

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
        audio_path: Optional[Path | str] = None,
        episode_date: Optional[datetime] = None,
    ) -> tuple[PodcastEpisode, bool]:
        """
        Get existing episode or create new one.

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
            audio_path=str(audio_path) if audio_path else "",
            audio_url=audio_url,
            episode_date=episode_date,
        )
        return episode, True

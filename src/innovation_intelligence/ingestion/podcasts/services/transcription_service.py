# src/innovation_intelligence/ingestion/podcasts/services/transcription_service.py
"""
Transcription service for podcast audio.

Provides:
- Google Speech API (Chirp) transcription
- GCS integration for cloud-based transcription
- Local and cloud transcript storage
- Transcript persistence
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from innovation_intelligence.config import settings
from innovation_intelligence.logger import get_logger
from innovation_intelligence.ingestion.podcasts.models import EpisodeInfo

log = get_logger(__name__)


class TranscriptionService:
    """
    Service for transcribing podcast audio using Google Speech API (Chirp).

    Uses GCS-first storage - transcripts are stored in GCS and never saved locally.
    Audio must be in GCS before transcription (use AudioDownloadService.download_to_gcs).

    Usage:
        service = TranscriptionService()

        # Transcribe from GCS URI (GCS-first mode)
        gcs_transcript_uri = service.transcribe_from_gcs(gcs_audio_uri, episode)

        # Load transcript data from GCS
        data = service.get_transcript(gcs_transcript_uri)
    """

    def __init__(
        self,
        language_codes: Optional[List[str]] = None,
    ):
        """
        Initialize the transcription service.

        Args:
            language_codes: Language codes for transcription (default: ["en-US"])
        """
        self.language_codes = language_codes or ["en-US"]
        self._chirp_service = None
        self._gcs_service = None

    @property
    def chirp_service(self):
        """Lazily load the Chirp transcription service."""
        if self._chirp_service is None:
            from innovation_intelligence.ingestion.podcasts.chirp_transcription import (
                ChirpTranscriptionService,
            )
            self._chirp_service = ChirpTranscriptionService(
                language_codes=self.language_codes,
            )
            log.info("[TRANSCRIBE] Chirp service initialized")
        return self._chirp_service

    @property
    def gcs_service(self):
        """Lazily create GCS service."""
        if self._gcs_service is None:
            from innovation_intelligence.ingestion.gcs_service import GCSStorageService
            self._gcs_service = GCSStorageService()
        return self._gcs_service

    def _get_episode_id(self, episode: EpisodeInfo) -> str:
        """Generate episode ID for storage."""
        return f"{episode.feed_id}_{episode.episode_id}"

    def transcribe_from_gcs(
        self,
        gcs_audio_uri: str,
        episode: EpisodeInfo,
        force: bool = False,
    ) -> str:
        """
        Transcribe audio from GCS and store transcript in GCS (GCS-first mode).

        Args:
            gcs_audio_uri: GCS URI of the audio file (gs://bucket/path)
            episode: Episode info (for metadata)
            force: Force re-transcription even if transcript exists

        Returns:
            GCS URI of the transcript
        """
        episode_id = self._get_episode_id(episode)

        # Check for existing transcript in GCS
        if not force and self.gcs_service.transcript_exists(episode_id, content_type="podcast"):
            gcs_transcript_uri = self.gcs_service.get_transcript_gcs_uri(
                episode_id, content_type="podcast"
            )
            log.info(f"[TRANSCRIBE] Transcript already exists in GCS: {gcs_transcript_uri}")
            return gcs_transcript_uri

        log.info(f"[TRANSCRIBE] Transcribing from GCS: {episode.title[:50]}...")

        # Transcribe directly from GCS
        transcript_data = self.chirp_service.transcribe(
            gcs_uri=gcs_audio_uri,
            language_codes=self.language_codes,
        )

        # Add metadata
        transcript_data["_metadata"] = {
            "episode_title": episode.title,
            "episode_id": episode.episode_id,
            "feed_id": episode.feed_id,
            "published_at": episode.published_at.isoformat() if episode.published_at else None,
            "gcs_audio_uri": gcs_audio_uri,
            "transcription_service": "google_speech_chirp",
        }

        # Store transcript in GCS
        gcs_transcript_uri = self.gcs_service.store_transcript(
            data=transcript_data,
            content_id=episode_id,
            content_type="podcast",
        )
        log.info(f"[TRANSCRIBE] Stored transcript in GCS: {gcs_transcript_uri}")

        return gcs_transcript_uri

    def get_transcript(self, gcs_transcript_uri: str) -> Dict[str, Any]:
        """
        Load a transcript from GCS.

        Args:
            gcs_transcript_uri: GCS URI of the transcript

        Returns:
            Transcript data dictionary
        """
        return self.gcs_service.get_transcript(gcs_transcript_uri)

    def get_transcript_by_episode(self, episode: EpisodeInfo) -> Optional[Dict[str, Any]]:
        """
        Load a transcript from GCS by episode info.

        Args:
            episode: Episode info

        Returns:
            Transcript data dictionary or None if not found
        """
        episode_id = self._get_episode_id(episode)
        if self.gcs_service.transcript_exists(episode_id, content_type="podcast"):
            gcs_uri = self.gcs_service.get_transcript_gcs_uri(episode_id, content_type="podcast")
            return self.gcs_service.get_transcript(gcs_uri)
        return None

    def get_segment_count_from_gcs(self, gcs_transcript_uri: str) -> int:
        """
        Get number of segments in a transcript from GCS.

        Args:
            gcs_transcript_uri: GCS URI of the transcript

        Returns:
            Number of segments
        """
        data = self.get_transcript(gcs_transcript_uri)
        return len(data.get("segments", []))

    def delete_transcript(self, episode: EpisodeInfo) -> bool:
        """
        Delete transcript from GCS.

        Args:
            episode: Episode info

        Returns:
            True if deleted
        """
        episode_id = self._get_episode_id(episode)
        gcs_uri = self.gcs_service.get_transcript_gcs_uri(episode_id, content_type="podcast")
        return self.gcs_service.delete_file(gcs_uri)

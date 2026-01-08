# src/innovation_intelligence/ingestion/podcasts/services/transcription_service.py
"""
Transcription service for podcast audio.

Provides:
- WhisperX transcription via Modal GPU
- Local file handling
- Transcript persistence
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from innovation_intelligence.config import settings
from innovation_intelligence.logger import get_logger
from innovation_intelligence.ingestion.podcasts.models import EpisodeInfo

log = get_logger(__name__)


class TranscriptionService:
    """
    Service for transcribing podcast audio.

    Uses Modal for GPU-accelerated transcription with WhisperX.
    Falls back to local processing if Modal is not available.

    Usage:
        service = TranscriptionService()
        transcript_path = service.transcribe(audio_path, episode_info)
    """

    def __init__(
        self,
        output_dir: Optional[Path] = None,
        use_modal: bool = True,
    ):
        self.output_dir = output_dir or settings.paths.transcripts_dir
        self.use_modal = use_modal
        self._modal_model = None

    def _get_output_path(
        self,
        episode: EpisodeInfo,
        db_episode_id: Optional[int] = None,
    ) -> Path:
        """Generate output path for transcript."""
        if db_episode_id:
            filename = f"episode_{db_episode_id}.json"
        else:
            filename = f"{episode.feed_id}_{episode.episode_id}.json"

        return self.output_dir / filename

    def _load_modal_model(self):
        """Lazily load the Modal model."""
        if self._modal_model is None:
            try:
                from innovation_intelligence.ingestion.podcasts.modal_app import Model
                self._modal_model = Model()
                log.info("[TRANSCRIBE] Modal model initialized")
            except ImportError as e:
                log.warning(f"[TRANSCRIBE] Modal not available: {e}")
                self.use_modal = False
            except Exception as e:
                log.error(f"[TRANSCRIBE] Failed to initialize Modal: {e}")
                self.use_modal = False

        return self._modal_model

    def transcribe(
        self,
        audio_path: Path,
        episode: EpisodeInfo,
        db_episode_id: Optional[int] = None,
        force: bool = False,
    ) -> Path:
        """
        Transcribe an audio file.

        Args:
            audio_path: Path to audio file
            episode: Episode info (for metadata)
            db_episode_id: Database episode ID (for filename)
            force: Force re-transcription even if transcript exists

        Returns:
            Path to transcript JSON file
        """
        output_path = self._get_output_path(episode, db_episode_id)

        # Check for existing transcript
        if output_path.exists() and not force:
            log.info(f"[TRANSCRIBE] Transcript exists: {output_path.name}")
            return output_path

        log.info(f"[TRANSCRIBE] Transcribing: {episode.title[:50]}...")

        # Load audio bytes
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        audio_bytes = audio_path.read_bytes()

        # Transcribe using Modal
        if self.use_modal:
            transcript_data = self._transcribe_modal(audio_bytes)
        else:
            raise NotImplementedError(
                "Local transcription not implemented. "
                "Please ensure Modal is configured."
            )

        # Add metadata
        transcript_data["_metadata"] = {
            "episode_title": episode.title,
            "episode_id": episode.episode_id,
            "feed_id": episode.feed_id,
            "published_at": episode.published_at.isoformat() if episode.published_at else None,
            "audio_path": str(audio_path),
        }

        # Save transcript
        self._save_transcript(transcript_data, output_path)

        log.info(f"[TRANSCRIBE] Saved transcript: {output_path.name}")
        return output_path

    def _transcribe_modal(self, audio_bytes: bytes) -> Dict[str, Any]:
        """Transcribe using Modal GPU."""
        model = self._load_modal_model()
        if model is None:
            raise RuntimeError("Modal model not available")

        # Call Modal remote function
        transcript_json = model.transcribe_with_diarization.remote(audio_bytes)

        # Parse JSON response
        return json.loads(transcript_json)

    def _save_transcript(self, data: Dict[str, Any], output_path: Path) -> None:
        """Save transcript to JSON file."""
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def load_transcript(self, transcript_path: Path) -> Dict[str, Any]:
        """
        Load a transcript from disk.

        Args:
            transcript_path: Path to transcript JSON

        Returns:
            Transcript data dictionary
        """
        with open(transcript_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def get_segment_count(self, transcript_path: Path) -> int:
        """Get number of segments in a transcript."""
        data = self.load_transcript(transcript_path)
        return len(data.get("segments", []))

    def cleanup(self, episode: EpisodeInfo, db_episode_id: Optional[int] = None) -> bool:
        """
        Delete transcript for an episode.

        Args:
            episode: Episode info
            db_episode_id: Database episode ID

        Returns:
            True if file was deleted
        """
        output_path = self._get_output_path(episode, db_episode_id)
        if output_path.exists():
            output_path.unlink()
            log.info(f"[TRANSCRIBE] Deleted: {output_path.name}")
            return True
        return False


class TranscriptionServiceLocal:
    """
    Local transcription service (without Modal).

    This is a placeholder for environments where Modal is not available.
    Requires local GPU and WhisperX installation.
    """

    def __init__(self):
        raise NotImplementedError(
            "Local transcription requires WhisperX and GPU. "
            "Use TranscriptionService with Modal for cloud GPU."
        )

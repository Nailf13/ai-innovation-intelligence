# src/innovation_intelligence/ingestion/podcasts/services/download_service.py
"""
Audio download service for podcast episodes.

Provides:
- Streaming download with progress
- Optional audio trimming via ffmpeg
- Upload to GCS for cloud storage
- Retry logic and error handling
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Optional, Tuple

import requests

from innovation_intelligence.config import settings
from innovation_intelligence.logger import get_logger
from innovation_intelligence.ingestion.podcasts.models import EpisodeInfo

log = get_logger(__name__)


# Type alias for progress callback
ProgressCallback = Callable[[int, int], None]  # (downloaded_bytes, total_bytes)


class AudioDownloadService:
    """
    Service for downloading podcast audio files with GCS-first storage.

    Downloads audio files directly to GCS. Uses temporary local files only
    when processing is needed (e.g., trimming with ffmpeg).

    Usage:
        service = AudioDownloadService()

        # Download and upload to GCS (GCS-first mode)
        gcs_uri = service.download_to_gcs(episode)

        # Download with trimming (uses temp files)
        gcs_uri = service.download_to_gcs(episode, trim_start=60)
    """

    def __init__(
        self,
        timeout: int = 120,
        chunk_size: int = 8192,
        max_retries: int = 3,
    ):
        self.timeout = timeout
        self.chunk_size = chunk_size
        self.max_retries = max_retries
        self._gcs_service = None

    @property
    def gcs_service(self):
        """Lazily create GCS service."""
        if self._gcs_service is None:
            from innovation_intelligence.ingestion.gcs_service import GCSStorageService
            self._gcs_service = GCSStorageService()
        return self._gcs_service

    def _get_episode_id(self, episode: EpisodeInfo) -> str:
        """Generate unique episode ID for GCS storage."""
        return f"{episode.feed_id}_{episode.episode_id}"

    def _download_with_retry(
        self,
        url: str,
        output_path: Path,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> Path:
        """Download file with retry logic."""
        last_error = None

        for attempt in range(self.max_retries):
            try:
                return self._download_stream(url, output_path, progress_callback)
            except Exception as e:
                last_error = e
                log.warning(
                    f"[DOWNLOAD] Attempt {attempt + 1}/{self.max_retries} failed: {e}"
                )
                if attempt < self.max_retries - 1:
                    continue

        raise RuntimeError(f"Download failed after {self.max_retries} attempts: {last_error}")

    def _download_stream(
        self,
        url: str,
        output_path: Path,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> Path:
        """Download file using streaming."""
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with requests.get(url, stream=True, timeout=self.timeout) as response:
            response.raise_for_status()

            total_size = int(response.headers.get("content-length", 0))
            downloaded = 0

            with open(output_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=self.chunk_size):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)

                        if progress_callback and total_size:
                            progress_callback(downloaded, total_size)

        log.info(f"[DOWNLOAD] Downloaded {downloaded / (1024*1024):.1f} MB to {output_path.name}")
        return output_path

    def download_to_gcs(
        self,
        episode: EpisodeInfo,
        trim_start: int = 0,
        trim_end: Optional[int] = None,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> str:
        """
        Download audio and upload directly to GCS (GCS-first mode).

        Uses temporary files only when trimming is required.

        Args:
            episode: Episode to download
            trim_start: Seconds to trim from start (optional)
            trim_end: Optional end time (seconds from start)
            progress_callback: Optional progress callback

        Returns:
            GCS URI of uploaded audio
        """
        episode_id = self._get_episode_id(episode)

        # Check if already in GCS
        if self.gcs_service.podcast_audio_exists(episode_id):
            gcs_uri = self.gcs_service.get_podcast_audio_gcs_uri(episode_id)
            log.info(f"[DOWNLOAD] Audio already in GCS: {gcs_uri}")
            return gcs_uri

        # If trimming required, use temporary files
        if trim_start > 0 or trim_end:
            return self._download_trim_and_upload(
                episode, episode_id, trim_start, trim_end, progress_callback
            )

        # Otherwise, download to temp and upload to GCS
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
            tmp_path = Path(tmp.name)

        try:
            log.info(f"[DOWNLOAD] Downloading: {episode.title[:50]}...")
            self._download_with_retry(
                episode.audio_url,
                tmp_path,
                progress_callback,
            )

            # Upload to GCS
            gcs_uri = self.gcs_service.upload_podcast_audio(tmp_path, episode_id)
            log.info(f"[DOWNLOAD] Uploaded to GCS: {gcs_uri}")

            return gcs_uri

        finally:
            # Clean up temp file
            if tmp_path.exists():
                tmp_path.unlink()
                log.debug(f"[DOWNLOAD] Cleaned up temp file: {tmp_path}")

    def _download_trim_and_upload(
        self,
        episode: EpisodeInfo,
        episode_id: str,
        trim_start: int,
        trim_end: Optional[int],
        progress_callback: Optional[ProgressCallback] = None,
    ) -> str:
        """Download audio, trim with ffmpeg, and upload to GCS."""
        # Check if ffmpeg is available
        if not self._ffmpeg_available():
            log.warning("[DOWNLOAD] ffmpeg not available, uploading without trim")
            return self.download_to_gcs(episode, 0, None, progress_callback)

        # Download to temp file
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp_download:
            tmp_download_path = Path(tmp_download.name)

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp_trimmed:
            tmp_trimmed_path = Path(tmp_trimmed.name)

        try:
            log.info(f"[DOWNLOAD] Downloading to temp: {episode.title[:50]}...")
            self._download_with_retry(episode.audio_url, tmp_download_path, progress_callback)

            log.info(f"[DOWNLOAD] Trimming audio (start={trim_start}s)...")
            self._trim_audio(tmp_download_path, tmp_trimmed_path, trim_start, trim_end)

            # Upload trimmed audio to GCS
            gcs_uri = self.gcs_service.upload_podcast_audio(tmp_trimmed_path, episode_id)
            log.info(f"[DOWNLOAD] Uploaded trimmed audio to GCS: {gcs_uri}")

            return gcs_uri

        finally:
            # Clean up temp files
            for path in [tmp_download_path, tmp_trimmed_path]:
                if path.exists():
                    path.unlink()
                    log.debug(f"[DOWNLOAD] Cleaned up temp file: {path}")

    def get_gcs_uri(self, episode: EpisodeInfo) -> str:
        """
        Get the GCS URI for an episode's audio.

        Args:
            episode: Episode info

        Returns:
            GCS URI
        """
        episode_id = self._get_episode_id(episode)
        return self.gcs_service.get_podcast_audio_gcs_uri(episode_id)

    def _ffmpeg_available(self) -> bool:
        """Check if ffmpeg is available."""
        return shutil.which("ffmpeg") is not None

    def _trim_audio(
        self,
        input_path: Path,
        output_path: Path,
        start_seconds: int,
        end_seconds: Optional[int] = None,
    ) -> Path:
        """Trim audio using ffmpeg."""
        output_path.parent.mkdir(parents=True, exist_ok=True)

        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start_seconds),
            "-i", str(input_path),
        ]

        if end_seconds:
            duration = end_seconds - start_seconds
            cmd.extend(["-t", str(duration)])

        cmd.extend([
            "-c", "copy",  # Stream copy (fast, no re-encoding)
            str(output_path),
        ])

        try:
            subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                timeout=300,  # 5 minute timeout
            )
        except subprocess.CalledProcessError as e:
            log.error(f"[DOWNLOAD] ffmpeg failed: {e.stderr.decode()}")
            raise RuntimeError(f"Audio trimming failed: {e}")
        except subprocess.TimeoutExpired:
            raise RuntimeError("Audio trimming timed out")

        log.info(f"[DOWNLOAD] Trimmed audio saved: {output_path.name}")
        return output_path

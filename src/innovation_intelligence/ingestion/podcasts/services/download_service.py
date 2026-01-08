# src/innovation_intelligence/ingestion/podcasts/services/download_service.py
"""
Audio download service for podcast episodes.

Provides:
- Streaming download with progress
- Optional audio trimming via ffmpeg
- Retry logic and error handling
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Optional

import requests

from innovation_intelligence.config import settings
from innovation_intelligence.logger import get_logger
from innovation_intelligence.ingestion.podcasts.models import EpisodeInfo

log = get_logger(__name__)


# Type alias for progress callback
ProgressCallback = Callable[[int, int], None]  # (downloaded_bytes, total_bytes)


class AudioDownloadService:
    """
    Service for downloading podcast audio files.

    Usage:
        service = AudioDownloadService()
        path = service.download(episode, output_dir)
        # or with trimming:
        path = service.download_and_trim(episode, output_dir, trim_start=60)
    """

    def __init__(
        self,
        output_dir: Optional[Path] = None,
        timeout: int = 120,
        chunk_size: int = 8192,
        max_retries: int = 3,
    ):
        self.output_dir = output_dir or settings.paths.audio_dir
        self.timeout = timeout
        self.chunk_size = chunk_size
        self.max_retries = max_retries

    def _get_output_path(
        self,
        episode: EpisodeInfo,
        custom_filename: Optional[str] = None,
    ) -> Path:
        """Generate output path for downloaded audio."""
        if custom_filename:
            filename = custom_filename
        else:
            # Use feed_id + episode_id for unique filename
            filename = f"{episode.feed_id}_{episode.episode_id}.mp3"

        return self.output_dir / filename

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

    def download(
        self,
        episode: EpisodeInfo,
        output_dir: Optional[Path] = None,
        custom_filename: Optional[str] = None,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> Path:
        """
        Download audio for an episode.

        Args:
            episode: Episode to download
            output_dir: Override output directory
            custom_filename: Override filename
            progress_callback: Optional progress callback

        Returns:
            Path to downloaded file
        """
        if output_dir:
            self.output_dir = output_dir

        output_path = self._get_output_path(episode, custom_filename)

        # Skip if already downloaded
        if output_path.exists():
            log.info(f"[DOWNLOAD] Audio already exists: {output_path.name}")
            return output_path

        log.info(f"[DOWNLOAD] Downloading: {episode.title[:50]}...")
        return self._download_with_retry(
            episode.audio_url,
            output_path,
            progress_callback,
        )

    def download_and_trim(
        self,
        episode: EpisodeInfo,
        output_dir: Optional[Path] = None,
        trim_start: int = 0,
        trim_end: Optional[int] = None,
        custom_filename: Optional[str] = None,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> Path:
        """
        Download and optionally trim audio.

        Args:
            episode: Episode to download
            output_dir: Override output directory
            trim_start: Seconds to trim from start
            trim_end: Optional end time (seconds from start)
            custom_filename: Override filename
            progress_callback: Progress callback for download

        Returns:
            Path to processed file
        """
        if output_dir:
            self.output_dir = output_dir

        output_path = self._get_output_path(episode, custom_filename)

        # Skip if already processed
        if output_path.exists():
            log.info(f"[DOWNLOAD] Processed audio already exists: {output_path.name}")
            return output_path

        # Check if ffmpeg is available
        if trim_start > 0 or trim_end:
            if not self._ffmpeg_available():
                log.warning("[DOWNLOAD] ffmpeg not available, downloading without trim")
                trim_start = 0
                trim_end = None

        if trim_start == 0 and trim_end is None:
            # No trimming needed, just download
            return self.download(episode, output_dir, custom_filename, progress_callback)

        # Download to temp file first
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
            tmp_path = Path(tmp.name)

        try:
            log.info(f"[DOWNLOAD] Downloading to temp: {episode.title[:50]}...")
            self._download_with_retry(episode.audio_url, tmp_path, progress_callback)

            log.info(f"[DOWNLOAD] Trimming audio (start={trim_start}s)...")
            self._trim_audio(tmp_path, output_path, trim_start, trim_end)

            return output_path

        finally:
            # Clean up temp file
            if tmp_path.exists():
                tmp_path.unlink()

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

    def cleanup(self, episode: EpisodeInfo) -> bool:
        """
        Delete downloaded audio for an episode.

        Args:
            episode: Episode whose audio to delete

        Returns:
            True if file was deleted
        """
        output_path = self._get_output_path(episode)
        if output_path.exists():
            output_path.unlink()
            log.info(f"[DOWNLOAD] Deleted: {output_path.name}")
            return True
        return False

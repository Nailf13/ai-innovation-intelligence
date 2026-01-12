# src/innovation_intelligence/ingestion/gcs_service.py
"""
Google Cloud Storage service for audio and document files.

Provides:
- Upload/download audio files to/from GCS
- Upload/download documents to/from GCS
- Store and retrieve transcripts from GCS
- List files in GCS buckets

Bucket structure:
bucket/
├── podcasts/
│   ├── raw/           # Original audio files
│   └── transcripts/   # Transcript JSON files
└── documents/
    ├── raw/           # Original document files (PDFs, etc.)
    └── transcripts/   # Extracted text files
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from google.cloud import storage
from google.cloud.exceptions import NotFound

from innovation_intelligence.config import settings
from innovation_intelligence.logger import get_logger

log = get_logger(__name__)


class GCSStorageService:
    """
    Service for storing and retrieving files from Google Cloud Storage.

    Supports both podcasts and documents with a consistent bucket structure.

    Usage:
        service = GCSStorageService()

        # Podcast audio
        gcs_uri = service.upload_podcast_audio(local_path, episode_id="123")
        local_path = service.download_podcast_audio(gcs_uri, output_dir)

        # Document files
        gcs_uri = service.upload_document(local_path, document_id="456")
        local_path = service.download_document(gcs_uri, output_dir)

        # Transcripts (for both podcasts and documents)
        gcs_uri = service.store_transcript(data, content_id="123", content_type="podcast")
        data = service.get_transcript(gcs_uri)
    """

    # GCS path prefixes
    PODCASTS_RAW_PREFIX = "podcasts/raw"
    PODCASTS_TRANSCRIPTS_PREFIX = "podcasts/transcripts"
    DOCUMENTS_RAW_PREFIX = "documents/raw"
    DOCUMENTS_TRANSCRIPTS_PREFIX = "documents/transcripts"

    # Default timeout for uploads/downloads (in seconds)
    DEFAULT_TIMEOUT = 600  # 10 minutes

    def __init__(
        self,
        bucket_name: Optional[str] = None,
        project_id: Optional[str] = None,
        timeout: int = DEFAULT_TIMEOUT,
    ):
        """
        Initialize the GCS storage service.

        Args:
            bucket_name: GCS bucket name (defaults to settings.gcp.gcs_bucket)
            project_id: GCP project ID (defaults to settings.gcp.project_id)
            timeout: Timeout in seconds for upload/download operations (default: 600)
        """
        self.bucket_name = bucket_name or settings.gcp.gcs_bucket
        self.project_id = project_id or settings.gcp.project_id
        self.timeout = timeout

        if not self.bucket_name:
            raise ValueError(
                "GCS_BUCKET must be set in environment or .env file"
            )

        self._client = None
        self._bucket = None

    @property
    def client(self) -> storage.Client:
        """Lazily create GCS client."""
        if self._client is None:
            self._client = storage.Client(project=self.project_id)
            log.debug(f"[GCS] Initialized client for project: {self.project_id}")
        return self._client

    @property
    def bucket(self) -> storage.Bucket:
        """Get the GCS bucket."""
        if self._bucket is None:
            self._bucket = self.client.bucket(self.bucket_name)
            log.debug(f"[GCS] Using bucket: {self.bucket_name}")
        return self._bucket

    # -------------------------------------------------------------------------
    # URI helpers
    # -------------------------------------------------------------------------
    def _make_gcs_uri(self, key: str) -> str:
        """Create GCS URI from bucket and key."""
        return f"gs://{self.bucket_name}/{key}"

    def _parse_gcs_uri(self, gcs_uri: str) -> tuple[str, str]:
        """
        Parse GCS URI into bucket and key.

        Args:
            gcs_uri: GCS URI (gs://bucket/key)

        Returns:
            Tuple of (bucket_name, key)
        """
        if not gcs_uri.startswith("gs://"):
            raise ValueError(f"Invalid GCS URI: {gcs_uri}")
        parts = gcs_uri.replace("gs://", "").split("/", 1)
        bucket_name = parts[0]
        key = parts[1] if len(parts) > 1 else ""
        return bucket_name, key

    def _blob_exists(self, key: str) -> bool:
        """Check if a blob exists in GCS."""
        blob = self.bucket.blob(key)
        return blob.exists()

    # -------------------------------------------------------------------------
    # Generic upload/download
    # -------------------------------------------------------------------------
    def upload_file(
        self,
        local_path: Path,
        key: str,
        content_type: Optional[str] = None,
        timeout: Optional[int] = None,
        chunk_size: int = 8 * 1024 * 1024,  # 8MB chunks for resumable uploads
    ) -> str:
        """
        Upload a file to GCS with timeout and retry support.

        Args:
            local_path: Path to local file
            key: GCS object key
            content_type: MIME type of the file
            timeout: Upload timeout in seconds (defaults to self.timeout)
            chunk_size: Chunk size for resumable uploads (default: 8MB)

        Returns:
            GCS URI of uploaded file
        """
        local_path = Path(local_path)
        timeout = timeout or self.timeout

        # Use resumable uploads for larger files
        blob = self.bucket.blob(key, chunk_size=chunk_size)

        file_size = local_path.stat().st_size
        log.info(
            f"[GCS] Uploading: {local_path.name} ({file_size / (1024*1024):.1f} MB) "
            f"-> gs://{self.bucket_name}/{key}"
        )

        blob.upload_from_filename(
            str(local_path),
            content_type=content_type,
            timeout=timeout,
        )

        gcs_uri = self._make_gcs_uri(key)
        log.info(f"[GCS] Uploaded: {gcs_uri}")
        return gcs_uri

    def download_file(
        self,
        gcs_uri: str,
        output_path: Path,
        timeout: Optional[int] = None,
    ) -> Path:
        """
        Download a file from GCS with timeout support.

        Args:
            gcs_uri: GCS URI of the file
            output_path: Local path to save the file
            timeout: Download timeout in seconds (defaults to self.timeout)

        Returns:
            Path to downloaded file
        """
        bucket_name, key = self._parse_gcs_uri(gcs_uri)
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        timeout = timeout or self.timeout

        # Use the correct bucket if different from default
        if bucket_name != self.bucket_name:
            bucket = self.client.bucket(bucket_name)
        else:
            bucket = self.bucket

        blob = bucket.blob(key)

        log.info(f"[GCS] Downloading: {gcs_uri} -> {output_path}")
        blob.download_to_filename(str(output_path), timeout=timeout)
        log.info(f"[GCS] Downloaded: {output_path}")

        return output_path

    # -------------------------------------------------------------------------
    # Podcast audio operations
    # -------------------------------------------------------------------------
    def upload_podcast_audio(
        self,
        local_path: Path,
        episode_id: str,
        content_type: str = "audio/mpeg",
    ) -> str:
        """
        Upload a podcast audio file to GCS.

        Args:
            local_path: Path to local audio file
            episode_id: Unique episode identifier
            content_type: MIME type of the file

        Returns:
            GCS URI of uploaded file
        """
        local_path = Path(local_path)
        key = f"{self.PODCASTS_RAW_PREFIX}/{episode_id}{local_path.suffix}"
        return self.upload_file(local_path, key, content_type)

    def download_podcast_audio(
        self,
        gcs_uri: str,
        output_dir: Path,
        filename: Optional[str] = None,
    ) -> Path:
        """
        Download a podcast audio file from GCS.

        Args:
            gcs_uri: GCS URI of the audio file
            output_dir: Local directory to save the file
            filename: Optional custom filename

        Returns:
            Path to downloaded file
        """
        _, key = self._parse_gcs_uri(gcs_uri)
        if filename is None:
            filename = Path(key).name

        output_path = Path(output_dir) / filename
        return self.download_file(gcs_uri, output_path)

    def podcast_audio_exists(self, episode_id: str, suffix: str = ".mp3") -> bool:
        """Check if podcast audio exists in GCS."""
        key = f"{self.PODCASTS_RAW_PREFIX}/{episode_id}{suffix}"
        return self._blob_exists(key)

    def get_podcast_audio_gcs_uri(self, episode_id: str, suffix: str = ".mp3") -> str:
        """Get the GCS URI for a podcast's audio file."""
        key = f"{self.PODCASTS_RAW_PREFIX}/{episode_id}{suffix}"
        return self._make_gcs_uri(key)

    # -------------------------------------------------------------------------
    # Document operations
    # -------------------------------------------------------------------------
    def upload_document(
        self,
        local_path: Path,
        document_id: str,
        content_type: Optional[str] = None,
    ) -> str:
        """
        Upload a document file to GCS.

        Args:
            local_path: Path to local document file
            document_id: Unique document identifier
            content_type: MIME type (auto-detected if not provided)

        Returns:
            GCS URI of uploaded file
        """
        local_path = Path(local_path)

        # Auto-detect content type
        if content_type is None:
            suffix = local_path.suffix.lower()
            content_types = {
                ".pdf": "application/pdf",
                ".doc": "application/msword",
                ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                ".txt": "text/plain",
            }
            content_type = content_types.get(suffix, "application/octet-stream")

        key = f"{self.DOCUMENTS_RAW_PREFIX}/{document_id}{local_path.suffix}"
        return self.upload_file(local_path, key, content_type)

    def download_document(
        self,
        gcs_uri: str,
        output_dir: Path,
        filename: Optional[str] = None,
    ) -> Path:
        """
        Download a document file from GCS.

        Args:
            gcs_uri: GCS URI of the document file
            output_dir: Local directory to save the file
            filename: Optional custom filename

        Returns:
            Path to downloaded file
        """
        _, key = self._parse_gcs_uri(gcs_uri)
        if filename is None:
            filename = Path(key).name

        output_path = Path(output_dir) / filename
        return self.download_file(gcs_uri, output_path)

    def document_exists(self, document_id: str, suffix: str = ".pdf") -> bool:
        """Check if document exists in GCS."""
        key = f"{self.DOCUMENTS_RAW_PREFIX}/{document_id}{suffix}"
        return self._blob_exists(key)

    def get_document_gcs_uri(self, document_id: str, suffix: str = ".pdf") -> str:
        """Get the GCS URI for a document file."""
        key = f"{self.DOCUMENTS_RAW_PREFIX}/{document_id}{suffix}"
        return self._make_gcs_uri(key)

    # -------------------------------------------------------------------------
    # Direct read/write operations (GCS-first, no local files)
    # -------------------------------------------------------------------------
    def read_json(self, gcs_uri: str) -> Dict[str, Any]:
        """
        Read JSON content directly from GCS.

        Args:
            gcs_uri: GCS URI of the JSON file

        Returns:
            Parsed JSON data
        """
        bucket_name, key = self._parse_gcs_uri(gcs_uri)

        log.debug(f"[GCS] Reading JSON: {gcs_uri}")

        # Use the correct bucket if different from default
        if bucket_name != self.bucket_name:
            bucket = self.client.bucket(bucket_name)
        else:
            bucket = self.bucket

        blob = bucket.blob(key)
        data = json.loads(blob.download_as_text())

        return data

    def write_json(
        self,
        data: Dict[str, Any],
        key: str,
        indent: int = 2,
    ) -> str:
        """
        Write JSON content directly to GCS.

        Args:
            data: Data to serialize as JSON
            key: GCS object key
            indent: JSON indentation (default: 2)

        Returns:
            GCS URI of written file
        """
        blob = self.bucket.blob(key)

        log.info(f"[GCS] Writing JSON: gs://{self.bucket_name}/{key}")

        json_data = json.dumps(data, ensure_ascii=False, indent=indent)
        blob.upload_from_string(json_data, content_type="application/json")

        gcs_uri = self._make_gcs_uri(key)
        log.debug(f"[GCS] JSON written: {gcs_uri}")
        return gcs_uri

    def read_text(self, gcs_uri: str, encoding: str = "utf-8") -> str:
        """
        Read text content directly from GCS.

        Args:
            gcs_uri: GCS URI of the text file
            encoding: Text encoding (default: utf-8)

        Returns:
            Text content as string
        """
        bucket_name, key = self._parse_gcs_uri(gcs_uri)

        log.debug(f"[GCS] Reading text: {gcs_uri}")

        # Use the correct bucket if different from default
        if bucket_name != self.bucket_name:
            bucket = self.client.bucket(bucket_name)
        else:
            bucket = self.bucket

        blob = bucket.blob(key)
        text = blob.download_as_text(encoding=encoding)

        return text

    def write_text(
        self,
        text: str,
        key: str,
        content_type: str = "text/plain",
        encoding: str = "utf-8",
    ) -> str:
        """
        Write text content directly to GCS.

        Args:
            text: Text content to write
            key: GCS object key
            content_type: MIME type (default: text/plain)
            encoding: Text encoding (default: utf-8)

        Returns:
            GCS URI of written file
        """
        blob = self.bucket.blob(key)

        log.info(f"[GCS] Writing text: gs://{self.bucket_name}/{key}")

        blob.upload_from_string(text, content_type=content_type, encoding=encoding)

        gcs_uri = self._make_gcs_uri(key)
        log.debug(f"[GCS] Text written: {gcs_uri}")
        return gcs_uri

    # -------------------------------------------------------------------------
    # Transcript operations (shared for podcasts and documents)
    # -------------------------------------------------------------------------
    def store_transcript(
        self,
        data: Dict[str, Any],
        content_id: str,
        content_type: str = "podcast",
    ) -> str:
        """
        Store transcript data to GCS as JSON.

        Args:
            data: Transcript data dictionary
            content_id: Unique content identifier (episode_id or document_id)
            content_type: "podcast" or "document"

        Returns:
            GCS URI of stored transcript
        """
        if content_type == "podcast":
            prefix = self.PODCASTS_TRANSCRIPTS_PREFIX
        elif content_type == "document":
            prefix = self.DOCUMENTS_TRANSCRIPTS_PREFIX
        else:
            raise ValueError(f"Invalid content_type: {content_type}")

        key = f"{prefix}/{content_id}.json"
        return self.write_json(data, key)

    def get_transcript(self, gcs_uri: str) -> Dict[str, Any]:
        """
        Retrieve transcript data from GCS.

        Args:
            gcs_uri: GCS URI of the transcript

        Returns:
            Transcript data dictionary
        """
        return self.read_json(gcs_uri)

    def transcript_exists(
        self,
        content_id: str,
        content_type: str = "podcast",
    ) -> bool:
        """Check if a transcript exists in GCS."""
        if content_type == "podcast":
            prefix = self.PODCASTS_TRANSCRIPTS_PREFIX
        else:
            prefix = self.DOCUMENTS_TRANSCRIPTS_PREFIX

        key = f"{prefix}/{content_id}.json"
        return self._blob_exists(key)

    def get_transcript_gcs_uri(
        self,
        content_id: str,
        content_type: str = "podcast",
    ) -> str:
        """Get the GCS URI for a transcript."""
        if content_type == "podcast":
            prefix = self.PODCASTS_TRANSCRIPTS_PREFIX
        else:
            prefix = self.DOCUMENTS_TRANSCRIPTS_PREFIX

        key = f"{prefix}/{content_id}.json"
        return self._make_gcs_uri(key)

    # -------------------------------------------------------------------------
    # List operations
    # -------------------------------------------------------------------------
    def list_files(
        self,
        prefix: str,
        max_results: int = 1000,
    ) -> List[str]:
        """
        List files in GCS with a given prefix.

        Args:
            prefix: GCS prefix to search
            max_results: Maximum number of results to return

        Returns:
            List of GCS URIs
        """
        blobs = self.bucket.list_blobs(prefix=prefix, max_results=max_results)
        return [self._make_gcs_uri(blob.name) for blob in blobs]

    def list_podcast_audio(self, max_results: int = 1000) -> List[str]:
        """List all podcast audio files."""
        return self.list_files(self.PODCASTS_RAW_PREFIX, max_results)

    def list_podcast_transcripts(self, max_results: int = 1000) -> List[str]:
        """List all podcast transcripts."""
        return self.list_files(self.PODCASTS_TRANSCRIPTS_PREFIX, max_results)

    def list_documents(self, max_results: int = 1000) -> List[str]:
        """List all document files."""
        return self.list_files(self.DOCUMENTS_RAW_PREFIX, max_results)

    def list_document_transcripts(self, max_results: int = 1000) -> List[str]:
        """List all document transcripts."""
        return self.list_files(self.DOCUMENTS_TRANSCRIPTS_PREFIX, max_results)

    # -------------------------------------------------------------------------
    # Delete operations
    # -------------------------------------------------------------------------
    def delete_file(self, gcs_uri: str) -> bool:
        """
        Delete a file from GCS.

        Args:
            gcs_uri: GCS URI of the file to delete

        Returns:
            True if deleted, False if file didn't exist
        """
        try:
            bucket_name, key = self._parse_gcs_uri(gcs_uri)

            # Use the correct bucket if different from default
            if bucket_name != self.bucket_name:
                bucket = self.client.bucket(bucket_name)
            else:
                bucket = self.bucket

            blob = bucket.blob(key)
            blob.delete()
            log.info(f"[GCS] Deleted: {gcs_uri}")
            return True
        except NotFound:
            log.warning(f"[GCS] File not found: {gcs_uri}")
            return False
        except Exception as e:
            log.warning(f"[GCS] Failed to delete {gcs_uri}: {e}")
            return False

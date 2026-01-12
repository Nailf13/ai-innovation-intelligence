# src/innovation_intelligence/ingestion/podcasts/chirp_transcription.py
"""
Google Speech API (Chirp) transcription service.

Uses Google Cloud Speech-to-Text v2 API with the Chirp model for audio transcription.
Provides word-level timestamps and automatic punctuation.

Key features:
- Batch transcription via Speech-to-Text v2 API
- Word-level timestamps for precise alignment
- Automatic punctuation and language detection
- Integration with GCS for audio storage
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from google.api_core.client_options import ClientOptions
from google.cloud.speech_v2 import SpeechClient
from google.cloud.speech_v2.types import cloud_speech

from innovation_intelligence.config import settings
from innovation_intelligence.logger import get_logger

log = get_logger(__name__)


class ChirpTranscriptionService:
    """
    Transcription service using Google Cloud Speech-to-Text v2 API with Chirp model.

    This service replaces Vertex AI Whisper with the Chirp model which provides:
    - Better accuracy for longer audio files
    - Word-level timestamps
    - No need to manage GPU endpoints
    - Pay-per-use pricing

    Usage:
        service = ChirpTranscriptionService()

        # Transcribe from GCS URI
        transcript = service.transcribe(gcs_uri="gs://bucket/audio.mp3")

        # Transcribe from local file (uploads to GCS first)
        transcript = service.transcribe_local(local_path, episode_id="123")
    """

    # Default region for Speech API (Chirp is available in us-central1)
    DEFAULT_REGION = "us-central1"

    def __init__(
        self,
        project_id: Optional[str] = None,
        region: Optional[str] = None,
        gcs_bucket: Optional[str] = None,
        language_codes: Optional[List[str]] = None,
    ):
        """
        Initialize the Chirp transcription service.

        Args:
            project_id: GCP project ID (defaults to settings)
            region: GCP region for Speech API (defaults to us-central1)
            gcs_bucket: GCS bucket for audio uploads (defaults to settings)
            language_codes: List of language codes (defaults to ["en-US"])
        """
        self.project_id = project_id or settings.vertex_ai.project_id
        self.region = region or self.DEFAULT_REGION
        self.gcs_bucket = gcs_bucket or settings.vertex_ai.gcs_bucket
        self.language_codes = language_codes or ["en-US"]

        self._client = None
        self._gcs_service = None

        if not self.project_id:
            raise ValueError(
                "VERTEX_PROJECT_ID or GCP_PROJECT_ID must be set in environment or .env file"
            )

    @property
    def client(self) -> SpeechClient:
        """Lazily create Speech client."""
        if self._client is None:
            # Create client with regional endpoint
            self._client = SpeechClient(
                client_options=ClientOptions(
                    api_endpoint=f"{self.region}-speech.googleapis.com"
                )
            )
            log.debug(f"[CHIRP] Initialized Speech client for region: {self.region}")
        return self._client

    @property
    def gcs_service(self):
        """Lazily create GCS service."""
        if self._gcs_service is None:
            from innovation_intelligence.ingestion.gcs_service import GCSStorageService
            self._gcs_service = GCSStorageService(
                bucket_name=self.gcs_bucket,
                project_id=self.project_id,
            )
        return self._gcs_service

    def _get_recognizer_path(self) -> str:
        """Get the recognizer resource path."""
        return f"projects/{self.project_id}/locations/{self.region}/recognizers/_"

    def transcribe(
        self,
        gcs_uri: str,
        language_codes: Optional[List[str]] = None,
        timeout: int = 3600,
    ) -> Dict[str, Any]:
        """
        Transcribe an audio file from GCS using Chirp.

        Args:
            gcs_uri: GCS URI of the audio file (gs://bucket/path)
            language_codes: Override default language codes
            timeout: Timeout in seconds for the operation (default: 1 hour)

        Returns:
            Transcript data with segments and word-level timestamps
        """
        log.info(f"[CHIRP] Transcribing: {gcs_uri}")

        # Build recognition config
        config = cloud_speech.RecognitionConfig(
            auto_decoding_config=cloud_speech.AutoDetectDecodingConfig(),
            language_codes=language_codes or self.language_codes,
            model="chirp",
            features=cloud_speech.RecognitionFeatures(
                enable_automatic_punctuation=True,
                enable_word_time_offsets=True,
            ),
        )

        # Build batch request
        request = cloud_speech.BatchRecognizeRequest(
            recognizer=self._get_recognizer_path(),
            config=config,
            files=[cloud_speech.BatchRecognizeFileMetadata(uri=gcs_uri)],
            recognition_output_config=cloud_speech.RecognitionOutputConfig(
                inline_response_config=cloud_speech.InlineOutputConfig(),
            ),
        )

        # Execute transcription
        log.info(f"[CHIRP] Starting batch transcription...")
        operation = self.client.batch_recognize(request=request)
        response = operation.result(timeout=timeout)

        # Parse response
        file_result = response.results.get(gcs_uri)
        transcript = self._format_transcript(file_result, gcs_uri)

        log.info(
            f"[CHIRP] Transcription complete: {len(transcript.get('segments', []))} segments"
        )

        return transcript

    def transcribe_local(
        self,
        local_path: Path,
        episode_id: str,
        language_codes: Optional[List[str]] = None,
        cleanup_gcs: bool = False,
        timeout: int = 3600,
    ) -> Dict[str, Any]:
        """
        Transcribe a local audio file by uploading to GCS first.

        Args:
            local_path: Path to local audio file
            episode_id: Unique episode identifier for GCS key
            language_codes: Override default language codes
            cleanup_gcs: Whether to delete the GCS file after transcription
            timeout: Timeout in seconds for the operation

        Returns:
            Transcript data with segments and word-level timestamps
        """
        local_path = Path(local_path)

        # Upload to GCS
        gcs_uri = self.gcs_service.upload_podcast_audio(local_path, episode_id)

        try:
            # Transcribe
            transcript = self.transcribe(
                gcs_uri=gcs_uri,
                language_codes=language_codes,
                timeout=timeout,
            )
            return transcript

        finally:
            # Cleanup if requested
            if cleanup_gcs:
                self.gcs_service.delete_file(gcs_uri)

    def _format_transcript(
        self,
        file_result: Optional[Any],
        source_uri: str,
    ) -> Dict[str, Any]:
        """
        Format Chirp response into standard transcript format.

        The output format matches the existing Whisper format for compatibility:
        {
            "segments": [
                {
                    "start": 0.0,
                    "end": 5.0,
                    "text": "Hello world",
                    "words": [
                        {"word": "Hello", "start": 0.0, "end": 0.5},
                        {"word": "world", "start": 0.5, "end": 1.0}
                    ]
                }
            ],
            "text": "Full transcript text",
            "language": "en-US"
        }
        """
        output = {"segments": [], "text": "", "language": ""}

        if not file_result or not file_result.inline_result:
            log.warning(f"[CHIRP] No transcription results for: {source_uri}")
            return output

        all_text_parts = []

        for result in file_result.inline_result.transcript.results:
            if not result.alternatives:
                continue

            alt = result.alternatives[0]

            # Build words list with timestamps
            words_list = []
            for word_info in alt.words:
                words_list.append({
                    "word": word_info.word,
                    "start": round(word_info.start_offset.total_seconds(), 3),
                    "end": round(word_info.end_offset.total_seconds(), 3),
                })

            # Build segment
            if words_list:
                segment = {
                    "start": words_list[0]["start"],
                    "end": words_list[-1]["end"],
                    "text": alt.transcript.strip(),
                    "words": words_list,
                }
                output["segments"].append(segment)
                all_text_parts.append(alt.transcript.strip())

            # Capture language from result
            if result.language_code and not output["language"]:
                output["language"] = result.language_code

        # Set full text
        output["text"] = " ".join(all_text_parts)

        # Default language if not detected
        if not output["language"]:
            output["language"] = self.language_codes[0] if self.language_codes else "en-US"

        return output

    def transcribe_batch(
        self,
        gcs_uris: List[str],
        language_codes: Optional[List[str]] = None,
        timeout: int = 7200,
    ) -> Dict[str, Dict[str, Any]]:
        """
        Transcribe multiple audio files in a single batch request.

        Note: The Speech API processes files in a single batch request which
        can be more efficient for multiple files.

        Args:
            gcs_uris: List of GCS URIs to transcribe
            language_codes: Override default language codes
            timeout: Timeout in seconds for the operation (default: 2 hours)

        Returns:
            Dictionary mapping GCS URIs to transcript data
        """
        log.info(f"[CHIRP] Batch transcribing {len(gcs_uris)} files...")

        # Build recognition config
        config = cloud_speech.RecognitionConfig(
            auto_decoding_config=cloud_speech.AutoDetectDecodingConfig(),
            language_codes=language_codes or self.language_codes,
            model="chirp",
            features=cloud_speech.RecognitionFeatures(
                enable_automatic_punctuation=True,
                enable_word_time_offsets=True,
            ),
        )

        # Build batch request with multiple files
        files = [
            cloud_speech.BatchRecognizeFileMetadata(uri=uri)
            for uri in gcs_uris
        ]

        request = cloud_speech.BatchRecognizeRequest(
            recognizer=self._get_recognizer_path(),
            config=config,
            files=files,
            recognition_output_config=cloud_speech.RecognitionOutputConfig(
                inline_response_config=cloud_speech.InlineOutputConfig(),
            ),
        )

        # Execute batch transcription
        operation = self.client.batch_recognize(request=request)
        response = operation.result(timeout=timeout)

        # Parse all results
        results = {}
        for gcs_uri in gcs_uris:
            file_result = response.results.get(gcs_uri)
            results[gcs_uri] = self._format_transcript(file_result, gcs_uri)

        log.info(f"[CHIRP] Batch transcription complete: {len(results)} files processed")
        return results

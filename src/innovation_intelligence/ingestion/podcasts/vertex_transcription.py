# src/innovation_intelligence/ingestion/podcasts/vertex_transcription.py
"""
Vertex AI Whisper transcription service.

Uses Google Cloud's Model Garden Whisper deployment for audio transcription.
Optimized for cost by managing endpoint lifecycle (deploy on demand, undeploy when done).

Based on: model_garden_pytorch_whisper_large_v3_deployment.ipynb
"""
from __future__ import annotations

import datetime
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from innovation_intelligence.config import PROJECT_ROOT, settings
from innovation_intelligence.logger import get_logger

log = get_logger(__name__)

# Docker image for Whisper serving
SERVE_DOCKER_URI = (
    "us-docker.pkg.dev/deeplearning-platform-release/"
    "vertex-model-garden/pytorch-inference.cu125.0-4.ubuntu2204.py310"
)

# Machine configurations for different accelerators
ACCELERATOR_CONFIGS = {
    "NVIDIA_L4": {
        "machine_type": "g2-standard-8",
        "accelerator_count": 1,
    },
    "NVIDIA_A100_80GB": {
        "machine_type": "a2-ultragpu-1g",
        "accelerator_count": 1,
    },
}


class VertexTranscriptionService:
    """
    Transcription service using Vertex AI Model Garden Whisper.

    Optimized for cost:
    - Reuses existing endpoints if available
    - Deploys on-demand when no endpoint exists
    - Provides explicit undeploy method for cleanup
    - Supports context manager for automatic cleanup

    Usage:
        # Manual lifecycle management
        service = VertexTranscriptionService()
        transcript = service.transcribe(audio_path)
        service.undeploy_endpoint()  # Clean up when done

        # Or with context manager (auto undeploy)
        with VertexTranscriptionService(auto_undeploy=True) as service:
            transcript = service.transcribe(audio_path)
    """

    def __init__(
        self,
        project_id: Optional[str] = None,
        location: Optional[str] = None,
        gcs_bucket: Optional[str] = None,
        model_name: Optional[str] = None,
        accelerator_type: Optional[str] = None,
        auto_undeploy: Optional[bool] = None,
        endpoint_display_name: Optional[str] = None,
    ):
        """
        Initialize the Vertex AI transcription service.

        Args:
            project_id: GCP project ID (defaults to settings)
            location: GCP region (defaults to settings)
            gcs_bucket: GCS bucket for audio uploads (defaults to settings)
            model_name: Whisper model name (defaults to settings)
            accelerator_type: GPU type - "NVIDIA_L4" or "NVIDIA_A100_80GB"
            auto_undeploy: If True, undeploy endpoint on context manager exit
            endpoint_display_name: Custom endpoint name for reuse
        """
        self.project_id = project_id or settings.vertex_ai.project_id
        self.location = location or settings.vertex_ai.location
        self.gcs_bucket = gcs_bucket or settings.vertex_ai.gcs_bucket
        self.model_name = model_name or settings.vertex_ai.whisper_model
        self.accelerator_type = accelerator_type or settings.vertex_ai.accelerator_type
        self.auto_undeploy = auto_undeploy if auto_undeploy is not None else settings.vertex_ai.auto_undeploy
        self.endpoint_display_name = endpoint_display_name or settings.vertex_ai.endpoint_display_name

        self._endpoint = None
        self._model = None
        self._storage_client = None
        self._initialized = False

        if not self.project_id:
            raise ValueError(
                "VERTEX_PROJECT_ID must be set in environment or .env file"
            )
        if not self.gcs_bucket:
            raise ValueError(
                "GCS_BUCKET must be set in environment or .env file"
            )

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - undeploy if auto_undeploy is enabled."""
        if self.auto_undeploy:
            self.undeploy_endpoint()
        return False

    def _init_vertex_ai(self):
        """Initialize Vertex AI SDK."""
        if self._initialized:
            return

        from google.cloud import aiplatform

        aiplatform.init(
            project=self.project_id,
            location=self.location,
        )
        self._initialized = True
        log.info(
            f"[VERTEX] Initialized Vertex AI: project={self.project_id}, "
            f"location={self.location}"
        )

    def _get_storage_client(self):
        """Get or create Google Cloud Storage client with proper credentials handling."""
        if self._storage_client is None:
            from google.cloud import storage
            from google.oauth2 import service_account

            # Check for GOOGLE_APPLICATION_CREDENTIALS env var
            creds_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")

            credentials = None
            if creds_path:
                # Convert to absolute path if relative
                if not Path(creds_path).is_absolute():
                    creds_path = str(PROJECT_ROOT / creds_path)

                # Load credentials from file
                if Path(creds_path).exists():
                    log.debug(f"[VERTEX] Loading credentials from: {creds_path}")
                    credentials = service_account.Credentials.from_service_account_file(
                        creds_path,
                        scopes=["https://www.googleapis.com/auth/cloud-platform"],
                    )
                else:
                    log.warning(f"[VERTEX] Credentials file not found: {creds_path}")

            # Create client with explicit credentials or let it use default auth
            if credentials:
                self._storage_client = storage.Client(
                    project=self.project_id,
                    credentials=credentials,
                )
            else:
                log.debug("[VERTEX] Using default credentials")
                self._storage_client = storage.Client(project=self.project_id)

        return self._storage_client

    def _upload_to_gcs(self, local_path: Path, gcs_prefix: str = "audio") -> str:
        """
        Upload a local file to GCS.

        Args:
            local_path: Path to local file
            gcs_prefix: Prefix/folder in GCS bucket

        Returns:
            GCS URI (gs://bucket/path)
        """
        client = self._get_storage_client()
        bucket = client.bucket(self.gcs_bucket)

        # Add timestamp to avoid collisions
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        blob_name = f"{gcs_prefix}/{timestamp}_{local_path.name}"
        blob = bucket.blob(blob_name)

        log.info(f"[VERTEX] Uploading {local_path.name} to gs://{self.gcs_bucket}/{blob_name}")
        blob.upload_from_filename(str(local_path))

        return f"gs://{self.gcs_bucket}/{blob_name}"

    def _delete_from_gcs(self, gcs_uri: str):
        """Delete a file from GCS."""
        try:
            client = self._get_storage_client()
            # Parse gs://bucket/path
            parts = gcs_uri.replace("gs://", "").split("/", 1)
            bucket_name = parts[0]
            blob_name = parts[1] if len(parts) > 1 else ""

            bucket = client.bucket(bucket_name)
            blob = bucket.blob(blob_name)
            blob.delete()
            log.debug(f"[VERTEX] Deleted {gcs_uri}")
        except Exception as e:
            log.warning(f"[VERTEX] Failed to delete {gcs_uri}: {e}")

    def _find_existing_endpoint(self) -> Optional[Any]:
        """
        Find an existing endpoint by display name.

        Returns:
            Endpoint if found, None otherwise
        """
        from google.cloud import aiplatform

        self._init_vertex_ai()

        # List all endpoints and find matching one
        endpoints = aiplatform.Endpoint.list(
            filter=f'display_name="{self.endpoint_display_name}"',
            order_by="create_time desc",
        )

        for endpoint in endpoints:
            # Check if endpoint has deployed models
            if endpoint.traffic_split:
                log.info(f"[VERTEX] Found existing endpoint: {endpoint.display_name}")
                return endpoint

        return None

    def _deploy_model(self) -> Tuple[Any, Any]:
        """
        Deploy a new Whisper model and endpoint.

        Returns:
            Tuple of (model, endpoint)
        """
        from google.cloud import aiplatform

        self._init_vertex_ai()

        # Get accelerator config
        accel_config = ACCELERATOR_CONFIGS.get(
            self.accelerator_type,
            ACCELERATOR_CONFIGS["NVIDIA_L4"]
        )
        machine_type = accel_config["machine_type"]
        accelerator_count = accel_config["accelerator_count"]

        log.info(
            f"[VERTEX] Deploying Whisper model: {self.model_name} "
            f"on {machine_type} with {self.accelerator_type}"
        )

        # Create endpoint
        endpoint = aiplatform.Endpoint.create(
            display_name=self.endpoint_display_name,
        )
        log.info(f"[VERTEX] Created endpoint: {endpoint.display_name}")

        # Parse model name for model_id
        # Format: "openai/whisper-large@whisper-large-v3-turbo" or "whisper-large-v3-turbo"
        if "/" in self.model_name:
            model_id = self.model_name
        else:
            model_id = f"openai/{self.model_name}"

        # Prepare serving environment
        serving_env = {
            "TASK": "audio2text",
            "MODEL_ID": model_id,
            "DEPLOY_SOURCE": "innovation_intelligence",
        }

        # Upload model
        model = aiplatform.Model.upload(
            display_name=f"whisper-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}",
            serving_container_image_uri=SERVE_DOCKER_URI,
            serving_container_ports=[7080],
            serving_container_predict_route="/pred",
            serving_container_health_route="/ping",
            serving_container_environment_variables=serving_env,
        )
        log.info(f"[VERTEX] Uploaded model: {model.display_name}")

        # Deploy model to endpoint
        model.deploy(
            endpoint=endpoint,
            machine_type=machine_type,
            accelerator_type=self.accelerator_type,
            accelerator_count=accelerator_count,
            deploy_request_timeout=1800,
        )
        log.info("[VERTEX] Model deployed to endpoint successfully")

        return model, endpoint

    def get_or_deploy_endpoint(self):
        """
        Get existing endpoint or deploy a new one.

        Returns:
            Deployed endpoint
        """
        if self._endpoint is not None:
            return self._endpoint

        # Try to find existing endpoint
        self._endpoint = self._find_existing_endpoint()

        if self._endpoint is None:
            # Deploy new endpoint
            self._model, self._endpoint = self._deploy_model()

        return self._endpoint

    def transcribe(
        self,
        audio_path: Path,
        cleanup_gcs: bool = True,
        language: Optional[str] = None,
        return_timestamps: str = "",
    ) -> Dict[str, Any]:
        """
        Transcribe an audio file using Vertex AI Whisper.

        Args:
            audio_path: Path to local audio file
            cleanup_gcs: Whether to delete the GCS file after transcription
            language: Output language (auto-detected if not specified)
            return_timestamps: "word", "sentence", or "" for default

        Returns:
            Transcript data with segments
        """
        audio_path = Path(audio_path)

        # Upload to GCS
        gcs_uri = self._upload_to_gcs(audio_path)

        try:
            # Get endpoint
            endpoint = self.get_or_deploy_endpoint()

            # Prepare request
            instances = [{"audio": gcs_uri}]
            parameters = {}
            if language:
                parameters["language"] = language
            if return_timestamps:
                parameters["return_timestamps"] = return_timestamps

            # Run transcription
            log.info(f"[VERTEX] Transcribing: {audio_path.name}")
            if parameters:
                response = endpoint.predict(instances=instances, parameters=parameters)
            else:
                response = endpoint.predict(instances=instances)

            # Parse response
            prediction = response.predictions[0] if response.predictions else {}

            # Convert to standard format
            transcript = self._format_transcript(prediction, audio_path.name)
            log.info(
                f"[VERTEX] Transcription complete: {len(transcript.get('segments', []))} segments"
            )

            return transcript

        finally:
            # Cleanup GCS
            if cleanup_gcs:
                self._delete_from_gcs(gcs_uri)

    def transcribe_batch(
        self,
        audio_paths: List[Path],
        cleanup_gcs: bool = True,
        language: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Transcribe multiple audio files in batch.

        Args:
            audio_paths: List of paths to audio files
            cleanup_gcs: Whether to delete GCS files after transcription
            language: Output language

        Returns:
            List of transcript data
        """
        gcs_uris = []
        transcripts = []

        try:
            # Upload all files
            for audio_path in audio_paths:
                gcs_uri = self._upload_to_gcs(Path(audio_path))
                gcs_uris.append(gcs_uri)

            # Get endpoint
            endpoint = self.get_or_deploy_endpoint()

            # Prepare batch request
            instances = [{"audio": uri} for uri in gcs_uris]
            parameters = {}
            if language:
                parameters["language"] = language

            # Run batch transcription
            log.info(f"[VERTEX] Batch transcribing {len(audio_paths)} files")
            if parameters:
                response = endpoint.predict(instances=instances, parameters=parameters)
            else:
                response = endpoint.predict(instances=instances)

            # Parse responses
            for i, (audio_path, prediction) in enumerate(
                zip(audio_paths, response.predictions)
            ):
                transcript = self._format_transcript(prediction, audio_path.name)
                transcripts.append(transcript)

            return transcripts

        finally:
            # Cleanup GCS
            if cleanup_gcs:
                for gcs_uri in gcs_uris:
                    self._delete_from_gcs(gcs_uri)

    def _format_transcript(
        self,
        prediction: Any,
        source_name: str,
    ) -> Dict[str, Any]:
        """
        Format Whisper prediction into standard transcript format.

        Args:
            prediction: Raw prediction from Vertex AI
            source_name: Source filename

        Returns:
            Standardized transcript dict
        """
        # Handle different response formats
        if isinstance(prediction, str):
            # Simple text response
            return {
                "text": prediction,
                "segments": [{"text": prediction, "start": 0, "end": 0}],
                "language": "en",
            }

        if isinstance(prediction, dict):
            # Structured response
            segments = []

            # Try to get segments from various possible keys
            raw_segments = (
                prediction.get("segments") or
                prediction.get("chunks") or
                []
            )

            for i, seg in enumerate(raw_segments):
                if isinstance(seg, dict):
                    # Handle timestamp format: can be [start, end] array or separate start/end fields
                    timestamp = seg.get("timestamp")
                    if timestamp is not None and isinstance(timestamp, list) and len(timestamp) >= 2:
                        start = timestamp[0] if timestamp[0] is not None else 0
                        end = timestamp[1] if timestamp[1] is not None else start
                    else:
                        # Use separate start/end fields
                        start = seg.get("start", 0)
                        end = seg.get("end", 0)

                    segments.append({
                        "text": seg.get("text", ""),
                        "start": start,
                        "end": end,
                    })
                elif isinstance(seg, str):
                    segments.append({
                        "text": seg,
                        "start": 0,
                        "end": 0,
                    })

            return {
                "text": prediction.get("text", " ".join(s.get("text", "") for s in segments)),
                "segments": segments,
                "language": prediction.get("language", "en"),
            }

        # Fallback
        return {
            "text": str(prediction),
            "segments": [{"text": str(prediction), "start": 0, "end": 0}],
            "language": "en",
        }

    def undeploy_endpoint(self, delete_endpoint: bool = True, delete_model: bool = True):
        """
        Undeploy the endpoint to save costs.

        Args:
            delete_endpoint: Whether to delete the endpoint after undeploying
            delete_model: Whether to delete the model after undeploying
        """
        if self._endpoint is not None:
            try:
                log.info("[VERTEX] Undeploying endpoint...")
                # Force delete will undeploy all models and delete the endpoint
                if delete_endpoint:
                    self._endpoint.delete(force=True)
                    log.info("[VERTEX] Endpoint deleted")
                else:
                    self._endpoint.undeploy_all()
                    log.info("[VERTEX] All models undeployed from endpoint")
            except Exception as e:
                log.warning(f"[VERTEX] Failed to undeploy endpoint: {e}")
            finally:
                self._endpoint = None

        if delete_model and self._model is not None:
            try:
                self._model.delete()
                log.info("[VERTEX] Model deleted")
            except Exception as e:
                log.warning(f"[VERTEX] Failed to delete model: {e}")
            finally:
                self._model = None

    def get_endpoint_status(self) -> Dict[str, Any]:
        """
        Get the current endpoint status.

        Returns:
            Dict with endpoint information
        """
        if self._endpoint is None:
            # Try to find existing endpoint
            endpoint = self._find_existing_endpoint()
            if endpoint is None:
                return {"status": "not_deployed", "endpoint": None}
            self._endpoint = endpoint

        return {
            "status": "deployed",
            "endpoint_name": self._endpoint.display_name,
            "endpoint_resource_name": self._endpoint.resource_name,
            "traffic_split": self._endpoint.traffic_split,
        }

    @classmethod
    def cleanup_all_endpoints(
        cls,
        project_id: Optional[str] = None,
        location: Optional[str] = None,
        display_name_prefix: str = "whisper",
    ):
        """
        Clean up all Whisper endpoints to save costs.

        Use this to clean up any lingering endpoints.

        Args:
            project_id: GCP project ID
            location: GCP region
            display_name_prefix: Only delete endpoints starting with this prefix
        """
        from google.cloud import aiplatform

        project = project_id or settings.vertex_ai.project_id
        loc = location or settings.vertex_ai.location

        aiplatform.init(project=project, location=loc)

        endpoints = aiplatform.Endpoint.list()
        deleted_count = 0

        for endpoint in endpoints:
            if endpoint.display_name.startswith(display_name_prefix):
                try:
                    log.info(f"[VERTEX] Deleting endpoint: {endpoint.display_name}")
                    endpoint.delete(force=True)
                    deleted_count += 1
                except Exception as e:
                    log.warning(f"[VERTEX] Failed to delete {endpoint.display_name}: {e}")

        # Also clean up orphaned models
        models = aiplatform.Model.list()
        for model in models:
            if model.display_name.startswith(display_name_prefix):
                try:
                    log.info(f"[VERTEX] Deleting model: {model.display_name}")
                    model.delete()
                except Exception as e:
                    log.warning(f"[VERTEX] Failed to delete model {model.display_name}: {e}")

        log.info(f"[VERTEX] Cleanup complete. Deleted {deleted_count} endpoints")
        return deleted_count

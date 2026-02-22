# src/innovation_intelligence/api/services/signed_url_service.py
"""
Service for generating time-limited signed URLs for GCS resources.

Provides:
- Generate signed URLs for audio and document files
- Server-side caching of signed URLs to reduce GCS API calls
- 1-hour expiration for security
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, Tuple

import google.auth
import google.auth.transport.requests
from google.auth import compute_engine, impersonated_credentials
from google.cloud import storage
from google.cloud.exceptions import NotFound

from innovation_intelligence.config import settings
from innovation_intelligence.ingestion.gcs_service import GCSStorageService
from innovation_intelligence.logger import get_logger

log = get_logger(__name__)


class SignedUrlService:
    """
    Service for generating time-limited signed URLs for GCS resources.

    Features:
    - Generates signed URLs with 1-hour expiration
    - Caches URLs for 45 minutes to reduce GCS API calls
    - Validates GCS URIs before generation

    Usage:
        service = SignedUrlService()
        signed_url = service.get_signed_url(
            gcs_uri="gs://bucket/podcasts/raw/episode.mp3",
            content_type="audio"
        )
    """

    # Cache TTL (45 minutes - expires before signed URL)
    CACHE_TTL_MINUTES = 45

    # Signed URL expiration (1 hour)
    SIGNED_URL_EXPIRATION_MINUTES = 60

    def __init__(self):
        """Initialize the signed URL service with GCS client."""
        self.gcs_service = GCSStorageService()

        # Build signing credentials that work with ADC (user credentials)
        self._signing_credentials = self._build_signing_credentials()

        # In-memory cache: {gcs_uri: (signed_url, expires_at)}
        self._cache: Dict[str, Tuple[str, datetime]] = {}

        log.info("[SignedUrlService] Initialized")

    @staticmethod
    def _build_signing_credentials():
        """
        Build credentials capable of signing blobs.

        ADC user credentials cannot sign directly, so we use
        impersonated credentials targeting a service account
        via the IAM signBlob API.

        The SA email can be configured via GCS_SIGNING_SA_EMAIL env var.
        If not set, falls back to the App Engine default SA ({project}@appspot.gserviceaccount.com).
        """
        credentials, project = google.auth.default()

        # If credentials already support signing (e.g. service account key), use as-is
        if hasattr(credentials, "sign_bytes"):
            return credentials

        # Use configurable SA email, or fall back to App Engine default
        sa_email = settings.gcp.gcs_signing_sa_email or f"{project}@appspot.gserviceaccount.com"
        log.info(f"[SignedUrlService] Using SA for signing: {sa_email}")

        signing_credentials = impersonated_credentials.Credentials(
            source_credentials=credentials,
            target_principal=sa_email,
            target_scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        return signing_credentials

    def get_signed_url(self, gcs_uri: str, content_type: str) -> str:
        """
        Generate a signed URL for a GCS resource with caching.

        Args:
            gcs_uri: GCS URI (e.g., gs://bucket/path/to/file.mp3)
            content_type: Content type ('audio' or 'document')

        Returns:
            Signed URL valid for 1 hour

        Raises:
            ValueError: If GCS URI is invalid
            NotFound: If GCS blob does not exist
            Exception: If signed URL generation fails
        """
        # Check cache first
        if gcs_uri in self._cache:
            cached_url, expires_at = self._cache[gcs_uri]
            if datetime.utcnow() < expires_at:
                log.debug(f"[SignedUrlService] Cache hit: {gcs_uri}")
                return cached_url
            else:
                # Cache expired, remove entry
                log.debug(f"[SignedUrlService] Cache expired: {gcs_uri}")
                del self._cache[gcs_uri]

        # Parse GCS URI
        try:
            bucket_name, key = self.gcs_service._parse_gcs_uri(gcs_uri)
        except ValueError as e:
            log.error(f"[SignedUrlService] Invalid GCS URI: {gcs_uri}")
            raise ValueError(f"Invalid GCS URI: {gcs_uri}") from e

        # Get bucket and blob
        try:
            bucket = self.gcs_service.client.bucket(bucket_name)
            blob = bucket.blob(key)

            # Check if blob exists
            if not blob.exists():
                log.error(f"[SignedUrlService] Blob not found: {gcs_uri}")
                raise NotFound(f"GCS blob not found: {gcs_uri}")

            # Generate signed URL with appropriate response headers
            log.info(f"[SignedUrlService] Generating signed URL: {gcs_uri}")

            # Set response headers based on content type
            response_type = None
            if content_type == "document":
                # For PDFs, set content-type to application/pdf to enable inline viewing
                response_type = "application/pdf"

            # Generate signed URL using signing credentials
            sign_kwargs = dict(
                version="v4",
                expiration=timedelta(minutes=self.SIGNED_URL_EXPIRATION_MINUTES),
                method="GET",
                credentials=self._signing_credentials,
            )
            if response_type:
                sign_kwargs["response_type"] = response_type

            signed_url = blob.generate_signed_url(**sign_kwargs)

            # Cache the signed URL
            cache_expiry = datetime.utcnow() + timedelta(minutes=self.CACHE_TTL_MINUTES)
            self._cache[gcs_uri] = (signed_url, cache_expiry)

            log.info(f"[SignedUrlService] Signed URL generated (cached until {cache_expiry.isoformat()})")
            return signed_url

        except NotFound:
            raise
        except Exception as e:
            # Log as warning (not error) — signed URLs may not work with all
            # credential types (e.g. ADC without a valid App Engine SA).
            # Callers should fall back to the proxy endpoint.
            log.warning(f"[SignedUrlService] Signed URL unavailable for {gcs_uri}: {e}")
            raise Exception(f"Failed to generate signed URL: {str(e)}") from e

    def clear_cache(self) -> int:
        """
        Clear all cached signed URLs.

        Returns:
            Number of cache entries cleared
        """
        count = len(self._cache)
        self._cache.clear()
        log.info(f"[SignedUrlService] Cache cleared ({count} entries)")
        return count

    def get_cache_stats(self) -> Dict[str, int]:
        """
        Get cache statistics.

        Returns:
            Dictionary with cache size and expired entries count
        """
        now = datetime.utcnow()
        total = len(self._cache)
        expired = sum(1 for _, expires_at in self._cache.values() if now >= expires_at)

        return {
            "total_entries": total,
            "expired_entries": expired,
            "valid_entries": total - expired
        }

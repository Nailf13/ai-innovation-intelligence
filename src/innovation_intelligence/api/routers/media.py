# src/innovation_intelligence/api/routers/media.py
"""
Media access API endpoints.

Provides:
- Signed URL generation for podcast audio and documents
- Secure access to GCS-stored media files
"""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from google.cloud.exceptions import NotFound
from pydantic import BaseModel
from sqlalchemy.orm import Session

from innovation_intelligence.api.deps import get_db
from innovation_intelligence.api.services.signed_url_service import SignedUrlService
from innovation_intelligence.db.models import PodcastEpisode, Document
from innovation_intelligence.logger import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/media", tags=["media"])

# Singleton service instance
_signed_url_service = None


def get_signed_url_service() -> SignedUrlService:
    """Get or create the singleton SignedUrlService instance."""
    global _signed_url_service
    if _signed_url_service is None:
        _signed_url_service = SignedUrlService()
    return _signed_url_service


class SignedUrlRequest(BaseModel):
    """Request for generating a signed URL."""
    source_type: Literal["podcast", "document"]
    source_id: int


class SignedUrlResponse(BaseModel):
    """Response containing a signed URL."""
    signed_url: str
    expires_in_seconds: int = 3600


@router.post("/signed-url", response_model=SignedUrlResponse)
async def get_signed_url(
    request: SignedUrlRequest,
    db: Session = Depends(get_db),
    service: SignedUrlService = Depends(get_signed_url_service)
):
    """
    Generate a signed URL for podcast audio or document file.

    This endpoint validates that the source exists in the database and has
    a valid GCS URI, then generates a time-limited signed URL for direct access.

    Security:
    - Validates source exists in database
    - Checks for GCS URI availability
    - Returns 1-hour expiration signed URLs
    - Server-side caching reduces GCS API calls

    Args:
        request: SignedUrlRequest with source_type and source_id
        db: Database session
        service: SignedUrlService instance

    Returns:
        SignedUrlResponse with signed_url and expiration time

    Raises:
        HTTPException 404: Source not found or GCS URI missing
        HTTPException 500: Failed to generate signed URL
    """
    try:
        # Fetch source and validate GCS URI
        if request.source_type == "podcast":
            episode = db.get(PodcastEpisode, request.source_id)
            if not episode:
                raise HTTPException(
                    status_code=404,
                    detail=f"Podcast episode {request.source_id} not found"
                )
            if not episode.gcs_audio_uri:
                raise HTTPException(
                    status_code=404,
                    detail=f"Podcast episode {request.source_id} has no audio file in GCS"
                )

            gcs_uri = episode.gcs_audio_uri
            content_type = "audio"
            log.info(f"[Media] Generating signed URL for podcast {request.source_id}: {episode.episode_title}")

        elif request.source_type == "document":
            document = db.get(Document, request.source_id)
            if not document:
                raise HTTPException(
                    status_code=404,
                    detail=f"Document {request.source_id} not found"
                )
            if not document.gcs_document_uri:
                raise HTTPException(
                    status_code=404,
                    detail=f"Document {request.source_id} has no file in GCS"
                )

            gcs_uri = document.gcs_document_uri
            content_type = "document"
            log.info(f"[Media] Generating signed URL for document {request.source_id}: {document.title}")

        else:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid source_type: {request.source_type}"
            )

        # Generate signed URL
        try:
            signed_url = service.get_signed_url(gcs_uri, content_type)
            log.info(f"[Media] Signed URL generated for {request.source_type} {request.source_id}")

            return SignedUrlResponse(
                signed_url=signed_url,
                expires_in_seconds=3600
            )

        except NotFound as e:
            log.error(f"[Media] GCS blob not found: {gcs_uri}")
            raise HTTPException(
                status_code=404,
                detail=f"Media file not found in storage: {str(e)}"
            )

    except HTTPException:
        raise
    except Exception as e:
        log.warning(f"[Media] Signed URL unavailable (proxy fallback active): {e}")
        raise HTTPException(
            status_code=503,
            detail="Signed URLs not available — use proxy endpoint instead"
        )


@router.get("/cache-stats")
async def get_cache_stats(
    service: SignedUrlService = Depends(get_signed_url_service)
):
    """
    Get signed URL cache statistics.

    Returns cache metrics including total entries, expired entries, and valid entries.

    Returns:
        Cache statistics dictionary
    """
    stats = service.get_cache_stats()
    log.debug(f"[Media] Cache stats: {stats}")
    return stats


@router.post("/clear-cache")
async def clear_cache(
    service: SignedUrlService = Depends(get_signed_url_service)
):
    """
    Clear the signed URL cache.

    Forces regeneration of signed URLs on next request.

    Returns:
        Number of cache entries cleared
    """
    count = service.clear_cache()
    log.info(f"[Media] Cache cleared: {count} entries")
    return {"cleared_entries": count}

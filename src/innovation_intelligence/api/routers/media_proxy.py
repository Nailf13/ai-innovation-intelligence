# src/innovation_intelligence/api/routers/media_proxy.py
"""
Media proxy endpoint for streaming documents with caching.

Provides HTTP range request support for efficient PDF streaming.
"""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from fastapi import Depends
import httpx
from typing import Optional

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


@router.get("/proxy/document/{document_id}")
async def proxy_document(
    document_id: int,
    request: Request,
    db: Session = Depends(get_db),
    service: SignedUrlService = Depends(get_signed_url_service),
):
    """
    Proxy document access with range request support for efficient streaming.

    This endpoint:
    - Generates a signed URL for the document
    - Proxies the request to GCS
    - Supports HTTP range requests for PDF streaming
    - Returns proper headers for PDF viewing
    """
    # Get document from database
    document = db.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=404, detail=f"Document {document_id} not found")

    if not document.gcs_document_uri:
        raise HTTPException(
            status_code=404,
            detail=f"Document {document_id} has no file in GCS"
        )

    try:
        # Generate signed URL
        signed_url = service.get_signed_url(document.gcs_document_uri, "document")

        # Get range header from request
        range_header = request.headers.get("range")

        # Prepare headers for GCS request
        headers = {}
        if range_header:
            headers["Range"] = range_header

        # Stream from GCS
        async with httpx.AsyncClient() as client:
            response = await client.get(
                signed_url,
                headers=headers,
                timeout=60.0,
            )

            # Prepare response headers
            response_headers = {
                "Content-Type": "application/pdf",
                "Accept-Ranges": "bytes",
                "Cache-Control": "public, max-age=3600",
            }

            # Copy content-related headers from GCS response
            if "Content-Length" in response.headers:
                response_headers["Content-Length"] = response.headers["Content-Length"]
            if "Content-Range" in response.headers:
                response_headers["Content-Range"] = response.headers["Content-Range"]

            # Return streaming response
            return StreamingResponse(
                content=response.aiter_bytes(chunk_size=8192),
                status_code=response.status_code,
                headers=response_headers,
                media_type="application/pdf",
            )

    except Exception as e:
        log.error(f"[MediaProxy] Failed to proxy document {document_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to load document: {str(e)}"
        )


@router.get("/proxy/podcast/{episode_id}")
async def proxy_podcast(
    episode_id: int,
    request: Request,
    db: Session = Depends(get_db),
    service: SignedUrlService = Depends(get_signed_url_service),
):
    """
    Proxy podcast audio access with range request support.
    """
    # Get episode from database
    episode = db.get(PodcastEpisode, episode_id)
    if not episode:
        raise HTTPException(status_code=404, detail=f"Podcast episode {episode_id} not found")

    if not episode.gcs_audio_uri:
        raise HTTPException(
            status_code=404,
            detail=f"Podcast episode {episode_id} has no audio file in GCS"
        )

    try:
        # Generate signed URL
        signed_url = service.get_signed_url(episode.gcs_audio_uri, "audio")

        # Get range header from request
        range_header = request.headers.get("range")

        # Prepare headers for GCS request
        headers = {}
        if range_header:
            headers["Range"] = range_header

        # Stream from GCS
        async with httpx.AsyncClient() as client:
            response = await client.get(
                signed_url,
                headers=headers,
                timeout=60.0,
            )

            # Prepare response headers
            response_headers = {
                "Content-Type": "audio/mpeg",
                "Accept-Ranges": "bytes",
                "Cache-Control": "public, max-age=3600",
            }

            # Copy content-related headers from GCS response
            if "Content-Length" in response.headers:
                response_headers["Content-Length"] = response.headers["Content-Length"]
            if "Content-Range" in response.headers:
                response_headers["Content-Range"] = response.headers["Content-Range"]

            # Return streaming response
            return StreamingResponse(
                content=response.aiter_bytes(chunk_size=8192),
                status_code=response.status_code,
                headers=response_headers,
                media_type="audio/mpeg",
            )

    except Exception as e:
        log.error(f"[MediaProxy] Failed to proxy episode {episode_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to load audio: {str(e)}"
        )

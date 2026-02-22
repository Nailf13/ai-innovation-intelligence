# src/innovation_intelligence/api/routers/media_proxy.py
"""
Media proxy endpoint for streaming GCS documents/audio directly.

Streams files from GCS through the API server using ADC credentials,
avoiding the need for signed URLs or service account keys.

Supports HTTP Range Requests (RFC 7233) for efficient seeking.

Performance: files are cached locally on first access so that subsequent
requests (including range requests from PDF viewers) are served from disk
instead of making round-trips to GCS.
"""
import hashlib
import os
import re
import tempfile
import time

from fastapi import APIRouter, HTTPException, Request, Depends
from fastapi.responses import StreamingResponse, Response
from sqlalchemy.orm import Session

from innovation_intelligence.api.deps import get_db
from innovation_intelligence.db.models import PodcastEpisode, Document
from innovation_intelligence.ingestion.gcs_service import GCSStorageService
from innovation_intelligence.logger import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/media", tags=["media"])

# Reuse a single GCS client
_gcs_service: GCSStorageService | None = None

# Larger chunks for streaming (64KB)
STREAM_CHUNK_SIZE = 65536

# ---------------------------------------------------------------------------
# Local file cache
# ---------------------------------------------------------------------------
_FILE_CACHE_DIR = os.path.join(tempfile.gettempdir(), "media-proxy-cache")
os.makedirs(_FILE_CACHE_DIR, exist_ok=True)

# gcs_uri → (local_path, file_size, cached_at)
_file_cache: dict[str, tuple[str, int, float]] = {}
_FILE_CACHE_TTL = 3600  # 1 hour


def get_gcs_service() -> GCSStorageService:
    global _gcs_service
    if _gcs_service is None:
        _gcs_service = GCSStorageService()
    return _gcs_service


def _get_local_file(gcs_service: GCSStorageService, gcs_uri: str) -> tuple[str, int]:
    """
    Return (local_path, file_size) for a GCS URI, downloading on first access.

    Cache hit (TTL 1h) + file exists on disk → return immediately.
    Cache miss → download from GCS to a local file, cache, and return.
    """
    now = time.monotonic()

    # Check in-memory cache
    cached = _file_cache.get(gcs_uri)
    if cached is not None:
        local_path, file_size, cached_at = cached
        if now - cached_at < _FILE_CACHE_TTL and os.path.exists(local_path):
            return local_path, file_size

    # Cache miss or expired — download from GCS
    bucket_name, key = gcs_service._parse_gcs_uri(gcs_uri)
    blob = gcs_service.client.bucket(bucket_name).blob(key)

    if not blob.exists():
        _file_cache.pop(gcs_uri, None)
        raise FileNotFoundError(f"Blob not found in GCS: {gcs_uri}")

    # Build local filename: MD5 of URI + original extension
    ext = os.path.splitext(key)[1] or ""
    uri_hash = hashlib.md5(gcs_uri.encode()).hexdigest()
    local_path = os.path.join(_FILE_CACHE_DIR, f"{uri_hash}{ext}")

    log.info(f"[MediaProxy] Downloading {gcs_uri} → {local_path}")
    blob.download_to_filename(local_path)
    file_size = os.path.getsize(local_path)

    _file_cache[gcs_uri] = (local_path, file_size, now)
    log.info(f"[MediaProxy] Cached {gcs_uri} ({file_size} bytes)")

    return local_path, file_size


def _parse_range_header(range_header: str, file_size: int) -> tuple[int, int]:
    """Parse an HTTP Range header and return (start, end) byte positions."""
    match = re.match(r"bytes=(\d*)-(\d*)", range_header)
    if not match:
        raise ValueError(f"Invalid Range header: {range_header}")

    start_str, end_str = match.group(1), match.group(2)

    if start_str and end_str:
        start, end = int(start_str), int(end_str)
    elif start_str:
        start = int(start_str)
        end = file_size - 1
    elif end_str:
        # Suffix range: last N bytes
        start = max(0, file_size - int(end_str))
        end = file_size - 1
    else:
        raise ValueError(f"Invalid Range header: {range_header}")

    # Clamp end to file size
    end = min(end, file_size - 1)

    if start > end or start >= file_size:
        raise ValueError(f"Range not satisfiable: {start}-{end}/{file_size}")

    return start, end


def _build_range_response(local_path: str, start: int, end: int, file_size: int, content_type: str):
    """Build an HTTP 206 response for a range request, reading from local file."""
    content_length = end - start + 1
    headers = {
        "Content-Type": content_type,
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Content-Length": str(content_length),
        "Accept-Ranges": "bytes",
        "Cache-Control": "public, max-age=3600",
    }

    # Read the requested range from disk (fast local I/O)
    with open(local_path, "rb") as f:
        f.seek(start)
        data = f.read(content_length)

    return Response(
        content=data,
        status_code=206,
        headers=headers,
        media_type=content_type,
    )


def _build_full_response(local_path: str, file_size: int, content_type: str):
    """Build an HTTP 200 response streaming a local file."""
    def iter_file():
        with open(local_path, "rb") as f:
            while chunk := f.read(STREAM_CHUNK_SIZE):
                yield chunk

    headers = {
        "Content-Type": content_type,
        "Accept-Ranges": "bytes",
        "Cache-Control": "public, max-age=3600",
    }
    if file_size:
        headers["Content-Length"] = str(file_size)

    return StreamingResponse(
        content=iter_file(),
        status_code=200,
        headers=headers,
        media_type=content_type,
    )


@router.get("/proxy/document/{document_id}")
async def proxy_document(
    document_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Proxy document access by streaming from a locally-cached copy of the GCS file.

    Supports HTTP Range Requests (RFC 7233).
    """
    document = db.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=404, detail=f"Document {document_id} not found")

    if not document.gcs_document_uri:
        raise HTTPException(
            status_code=404,
            detail=f"Document {document_id} has no file in GCS",
        )

    try:
        gcs_service = get_gcs_service()
        local_path, file_size = _get_local_file(gcs_service, document.gcs_document_uri)

        range_header = request.headers.get("range")

        if range_header and file_size > 0:
            try:
                start, end = _parse_range_header(range_header, file_size)
            except ValueError:
                return Response(
                    status_code=416,
                    headers={"Content-Range": f"bytes */{file_size}"},
                )
            return _build_range_response(local_path, start, end, file_size, "application/pdf")

        return _build_full_response(local_path, file_size, "application/pdf")

    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Document file not found in GCS")
    except HTTPException:
        raise
    except Exception as e:
        # Invalidate cache on unexpected errors
        _file_cache.pop(document.gcs_document_uri, None)
        log.error(f"[MediaProxy] Failed to proxy document {document_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to load document: {str(e)}")


@router.get("/proxy/podcast/{episode_id}")
async def proxy_podcast(
    episode_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Proxy podcast audio access by streaming from a locally-cached copy of the GCS file.

    Supports HTTP Range Requests (RFC 7233) for efficient seeking.
    """
    episode = db.get(PodcastEpisode, episode_id)
    if not episode:
        raise HTTPException(status_code=404, detail=f"Podcast episode {episode_id} not found")

    if not episode.gcs_audio_uri:
        raise HTTPException(
            status_code=404,
            detail=f"Podcast episode {episode_id} has no audio file in GCS",
        )

    try:
        gcs_service = get_gcs_service()
        local_path, file_size = _get_local_file(gcs_service, episode.gcs_audio_uri)

        range_header = request.headers.get("range")

        if range_header and file_size > 0:
            try:
                start, end = _parse_range_header(range_header, file_size)
            except ValueError:
                return Response(
                    status_code=416,
                    headers={"Content-Range": f"bytes */{file_size}"},
                )
            return _build_range_response(local_path, start, end, file_size, "audio/mpeg")

        return _build_full_response(local_path, file_size, "audio/mpeg")

    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Audio file not found in GCS")
    except HTTPException:
        raise
    except Exception as e:
        # Invalidate cache on unexpected errors
        _file_cache.pop(episode.gcs_audio_uri, None)
        log.error(f"[MediaProxy] Failed to proxy episode {episode_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to load audio: {str(e)}")

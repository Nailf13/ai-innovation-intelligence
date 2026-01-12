# src/innovation_intelligence/api/routers/podcasts.py
"""
Podcast-related API endpoints.

Handles:
- Searching for podcasts via Podcast Index API
- Listing episodes from a podcast
- Selecting episodes for processing
- Downloading audio files
"""
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
import requests
import uuid

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
import asyncio
import json

from innovation_intelligence.api.deps import get_db
from innovation_intelligence.api.schemas import (
    PodcastSearchRequest,
    PodcastSearchResponse,
    PodcastSearchResult,
    EpisodeListResponse,
    PodcastEpisodeInfo,
    EpisodeSelectRequest,
    PodcastEpisodeDB,
    ProcessingStatusResponse,
    TaskStatus,
    SuccessResponse,
    ErrorResponse,
)
from innovation_intelligence.api.sse_broadcaster import get_broadcaster
from innovation_intelligence.config import settings
from innovation_intelligence.db.models import PodcastEpisode
from innovation_intelligence.ingestion.podcasts.podcast_index_client import PodcastIndexClient
from innovation_intelligence.logger import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/podcasts", tags=["podcasts"])

# In-memory task tracking for processing tasks
_processing_tasks: Dict[str, Dict[str, Any]] = {}


def get_podcast_client() -> PodcastIndexClient:
    """Get configured podcast index client."""
    return PodcastIndexClient(
        api_key=settings.podcast_index.api_key,
        api_secret=settings.podcast_index.api_secret,
    )


@router.post("/search", response_model=PodcastSearchResponse)
def search_podcasts(request: PodcastSearchRequest):
    """
    Search for podcasts by term using Podcast Index API.
    """
    try:
        client = get_podcast_client()
        
        # Get raw results from API
        url = f"{client.BASE_URL}/search/byterm"
        params = {"q": request.query}
        
        r = requests.get(url, headers=client._headers(), params=params, timeout=20)
        r.raise_for_status()
        feeds = r.json().get("feeds", [])
        
        results = []
        for feed in feeds[:20]:  # Limit to 20 results
            results.append(PodcastSearchResult(
                feed_id=feed.get("id", 0),
                title=feed.get("title", "Unknown"),
                author=feed.get("author"),
                description=feed.get("description"),
                image_url=feed.get("image"),
                url=feed.get("url"),
                episode_count=feed.get("episodeCount"),
            ))
        
        return PodcastSearchResponse(results=results, count=len(results))
    
    except requests.RequestException as e:
        log.error(f"Podcast search failed: {e}")
        raise HTTPException(status_code=503, detail=f"Podcast Index API error: {str(e)}")
    except Exception as e:
        log.error(f"Podcast search error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/episodes/{feed_id}", response_model=EpisodeListResponse)
def list_episodes(feed_id: int, limit: int = 20, offset: int = 0):
    """
    List episodes from a podcast feed with offset-based pagination.

    Args:
        feed_id: The podcast feed ID
        limit: Maximum number of episodes to return (default 20)
        offset: Number of episodes to skip (for pagination)
    """
    try:
        client = get_podcast_client()
        # Fetch enough episodes to cover offset + limit + 1 (to check for more)
        total_needed = offset + limit + 1
        episodes_data = client.get_episodes(feed_id, limit=total_needed)

        # Apply offset
        episodes_data = episodes_data[offset:]

        # Check if there are more episodes
        has_more = len(episodes_data) > limit
        if has_more:
            episodes_data = episodes_data[:limit]

        episodes = []
        for ep in episodes_data:
            date_published = None
            ep_timestamp = ep.get("datePublished")
            if ep_timestamp:
                try:
                    date_published = datetime.fromtimestamp(ep_timestamp)
                except (ValueError, TypeError):
                    pass

            episodes.append(PodcastEpisodeInfo(
                id=ep.get("id", 0),
                title=ep.get("title", "Unknown"),
                description=ep.get("description"),
                date_published=date_published,
                duration=ep.get("duration"),
                audio_url=ep.get("enclosureUrl", ""),
                image_url=ep.get("image"),
            ))

        return EpisodeListResponse(
            feed_id=feed_id,
            episodes=episodes,
            count=len(episodes),
            has_more=has_more,
            next_offset=offset + len(episodes) if has_more else None,
        )

    except Exception as e:
        log.error(f"Episode list error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/select", response_model=List[PodcastEpisodeDB])
def select_episodes(
    request: EpisodeSelectRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    Select episodes for processing and optionally start download.
    
    Creates PodcastEpisode records in the database and
    triggers background download of audio files.
    """
    try:
        client = get_podcast_client()
        episodes_data = client.get_episodes(request.feed_id, limit=100)
        
        # Filter to selected episodes
        selected = {ep["id"]: ep for ep in episodes_data if ep["id"] in request.episode_ids}
        
        if not selected:
            raise HTTPException(status_code=404, detail="No matching episodes found")
        
        created_episodes = []
        
        for ep_id in request.episode_ids:
            if ep_id not in selected:
                continue
            
            ep_data = selected[ep_id]
            
            # Check if already exists
            existing = db.query(PodcastEpisode).filter(
                PodcastEpisode.audio_url == ep_data.get("enclosureUrl")
            ).first()
            
            if existing:
                created_episodes.append(existing)
                continue
            
            # Parse date
            episode_date = None
            if ep_data.get("datePublished"):
                try:
                    episode_date = datetime.fromtimestamp(ep_data["datePublished"])
                except (ValueError, TypeError):
                    pass
            
            # Create episode record
            episode = PodcastEpisode(
                podcast_name=request.podcast_name,
                episode_title=ep_data.get("title", f"Episode {ep_id}"),
                audio_url=ep_data.get("enclosureUrl", ""),
                gcs_audio_uri="",  # Will be populated after background download
                episode_date=episode_date,
            )
            db.add(episode)
            db.flush()
            
            created_episodes.append(episode)
            
            # Queue download in background
            background_tasks.add_task(
                download_episode_audio,
                episode.id,
                ep_data.get("enclosureUrl", ""),
            )
        
        db.commit()
        
        # Refresh to get final state
        for ep in created_episodes:
            db.refresh(ep)
        
        return [PodcastEpisodeDB.model_validate(ep) for ep in created_episodes]
    
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Episode selection error: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


def download_episode_audio(episode_id: int, audio_url: str):
    """
    Background task to download episode audio and upload to GCS.

    Uses GCS-first architecture with automatic cleanup of temporary files.
    """
    from innovation_intelligence.db.session import SessionLocal
    from innovation_intelligence.ingestion.gcs_service import GCSStorageService
    import requests
    import tempfile
    import os

    db = SessionLocal()
    try:
        episode = db.query(PodcastEpisode).get(episode_id)
        if not episode:
            log.error(f"Episode {episode_id} not found for download")
            return

        log.info(f"Downloading audio for episode {episode_id}: {audio_url}")

        # Download audio to temp file
        gcs_service = GCSStorageService()

        with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as tmp_file:
            response = requests.get(audio_url, stream=True, timeout=120)
            response.raise_for_status()

            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    tmp_file.write(chunk)

            tmp_path = tmp_file.name

        try:
            # Upload to GCS
            gcs_uri = gcs_service.upload_podcast_audio(tmp_path, episode_id=str(episode_id))

            # Update episode record with GCS URI
            episode.gcs_audio_uri = gcs_uri
            db.commit()

            log.info(f"Downloaded and uploaded audio for episode {episode_id}: {gcs_uri}")
        finally:
            # Clean up temp file
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    except Exception as e:
        log.error(f"Failed to download episode {episode_id}: {e}")
        db.rollback()
    finally:
        db.close()


@router.get("/", response_model=List[PodcastEpisodeDB])
def list_podcast_episodes(
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """
    List all podcast episodes in the database.

    Note: To check if an episode has been analyzed, check if it has UnitInsights
    via the /podcasts/{episode_id}/analyzed endpoint.
    """
    episodes = db.query(PodcastEpisode).offset(skip).limit(limit).all()
    return [PodcastEpisodeDB.model_validate(ep) for ep in episodes]


# ---------------------------------------------------------------------
# Server-Sent Events (SSE) for real-time status updates
# IMPORTANT: This route must come BEFORE /{episode_id} to avoid path conflicts
# ---------------------------------------------------------------------

@router.get("/events")
async def episode_events_stream(request: Request):
    """
    SSE endpoint for real-time episode status updates.

    Clients connect to this endpoint to receive real-time notifications about:
    - Episode processing state changes (downloading → transcribing → indexing → ready)
    - Analysis state changes (ready → analyzing → analyzed)

    Returns a stream of events in SSE format:
    ```
    data: {"type": "episode_status", "episode_id": 123, "status": "downloading", ...}
    ```
    """
    broadcaster = get_broadcaster()

    async def event_generator():
        # Register client
        queue = await broadcaster.connect()
        try:
            # Send initial connection confirmation
            yield f"data: {json.dumps({'type': 'connected', 'message': 'SSE connection established'})}\n\n"

            # Stream events
            while True:
                # Check if client is still connected
                if await request.is_disconnected():
                    break

                try:
                    # Wait for events with timeout to check disconnection
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    # Send keepalive ping every 30 seconds
                    yield f": keepalive\n\n"

        finally:
            # Unregister client on disconnect
            await broadcaster.disconnect(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        }
    )


@router.get("/{episode_id}", response_model=PodcastEpisodeDB)
def get_podcast_episode(episode_id: int, db: Session = Depends(get_db)):
    """
    Get a specific podcast episode by ID.
    """
    episode = db.query(PodcastEpisode).filter(PodcastEpisode.id == episode_id).first()
    if not episode:
        raise HTTPException(status_code=404, detail="Episode not found")
    return PodcastEpisodeDB.model_validate(episode)


@router.get("/{episode_id}/analyzed")
def check_episode_analyzed(episode_id: int, db: Session = Depends(get_db)):
    """
    Check if an episode has been fully analyzed (has UnitInsights with dimension assessments).

    Returns: {"analyzed": true/false, "insight_count": N, "dimensions_count": M}
    """
    from innovation_intelligence.db.models import UnitInsight, InsightDimension
    from sqlalchemy import func

    episode = db.query(PodcastEpisode).filter(PodcastEpisode.id == episode_id).first()
    if not episode:
        raise HTTPException(status_code=404, detail="Episode not found")

    insight_count = db.query(func.count(UnitInsight.id)).filter(
        UnitInsight.episode_id == episode_id
    ).scalar()

    # Count dimensions across all insights for this episode
    dimensions_count = db.query(func.count(InsightDimension.id)).join(
        UnitInsight, InsightDimension.unit_insight_id == UnitInsight.id
    ).filter(
        UnitInsight.episode_id == episode_id
    ).scalar()

    return {
        "analyzed": dimensions_count > 0,  # Only analyzed if has dimensions
        "insight_count": insight_count or 0,
        "dimensions_count": dimensions_count or 0
    }


@router.get("/{episode_id}/processing-status")
def check_episode_processing_status(episode_id: int, db: Session = Depends(get_db)):
    """
    Check the processing status of an episode.

    Returns: {"status": "needs_processing" | "downloading" | "transcribing" | "indexing" | "ready" | "analyzing" | "analyzed"}

    Status flow:
    - needs_processing: No audio downloaded yet
    - downloading: Download in progress (check active tasks)
    - transcribing: Audio downloaded, transcription in progress
    - indexing: Transcript ready, indexing in progress
    - ready: Indexed and ready for analysis
    - analyzing: Analysis in progress
    - analyzed: Analysis complete (has insights)
    """
    from innovation_intelligence.db.models import UnitInsight, PodcastChunkVector
    from innovation_intelligence.api.routers.analysis import _analysis_tasks

    episode = db.query(PodcastEpisode).filter(PodcastEpisode.id == episode_id).first()
    if not episode:
        raise HTTPException(status_code=404, detail="Episode not found")

    # Check for active processing task
    active_task = None
    for task_id, task_info in _processing_tasks.items():
        if (task_info.get("entity_id") == episode_id and
            task_info.get("status") == TaskStatus.RUNNING):
            active_task = task_info
            break

    # If there's an active task, return its current stage
    if active_task:
        current_stage = active_task.get("current_stage")
        if current_stage == "download":
            return {"status": "downloading"}
        elif current_stage == "transcribe":
            return {"status": "transcribing"}
        elif current_stage == "index":
            return {"status": "indexing"}

    # Check if analyzed (has insights with dimension assessments)
    from innovation_intelligence.db.models import InsightDimension

    insights = db.query(UnitInsight).filter(UnitInsight.episode_id == episode_id).all()
    if insights:
        # Check if at least one insight has dimension assessments
        has_dimensions = False
        for insight in insights:
            dimension_count = db.query(InsightDimension).filter(
                InsightDimension.unit_insight_id == insight.id
            ).count()
            if dimension_count > 0:
                has_dimensions = True
                break

        if has_dimensions:
            return {"status": "analyzed"}

    # Check for active analysis task - only return "analyzing" if this episode is ready to be analyzed
    for task_id, task_info in _analysis_tasks.items():
        if task_info.get("status") == TaskStatus.RUNNING:
            # Check if this episode is in "ready" state (indexed but no insights)
            if episode.gcs_transcript_uri:
                source = f"{episode.podcast_name} - {episode.episode_title}"
                has_chunks = db.query(PodcastChunkVector).filter(
                    PodcastChunkVector.source == source
                ).first() is not None

                if has_chunks:
                    # Episode is ready and analysis is running - mark as analyzing
                    return {"status": "analyzing"}
            # Episode is not ready for analysis, fall through to check actual status
            break

    # Check if indexed (ready for analysis)
    if episode.gcs_transcript_uri:
        source = f"{episode.podcast_name} - {episode.episode_title}"
        has_chunks = db.query(PodcastChunkVector).filter(PodcastChunkVector.source == source).first() is not None
        if has_chunks:
            return {"status": "ready"}
        # Has transcript but not indexed yet
        return {"status": "indexing"}

    # Check if audio is uploaded (but not transcribed yet)
    if episode.gcs_audio_uri:
        return {"status": "transcribing"}

    # Needs processing
    return {"status": "needs_processing"}


@router.delete("/{episode_id}", response_model=SuccessResponse)
def delete_podcast_episode(episode_id: int, db: Session = Depends(get_db)):
    """
    Delete a podcast episode.
    """
    episode = db.query(PodcastEpisode).filter(PodcastEpisode.id == episode_id).first()
    if not episode:
        raise HTTPException(status_code=404, detail="Episode not found")

    db.delete(episode)
    db.commit()

    return SuccessResponse(message=f"Episode {episode_id} deleted")


@router.post("/download/{episode_id}", response_model=SuccessResponse)
def trigger_download(
    episode_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    Trigger audio download for an episode that hasn't been downloaded yet.
    """
    episode = db.query(PodcastEpisode).filter(PodcastEpisode.id == episode_id).first()
    if not episode:
        raise HTTPException(status_code=404, detail="Episode not found")

    if episode.gcs_audio_uri:
        return SuccessResponse(message="Audio already downloaded and uploaded to GCS")

    if not episode.audio_url:
        raise HTTPException(status_code=400, detail="No audio URL available for this episode")

    # Queue download in background
    background_tasks.add_task(
        download_episode_audio,
        episode.id,
        episode.audio_url,
    )

    return SuccessResponse(message=f"Download started for episode {episode_id}")


# ---------------------------------------------------------------------
# Unified Processing Endpoints (download → transcribe → index)
# ---------------------------------------------------------------------

@router.post("/process/{episode_id}", response_model=ProcessingStatusResponse)
async def process_episode(
    episode_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    Process podcast episode: download → transcribe → index.

    Chains all three operations in a single background task.
    Returns task_id for status polling.
    """
    # Verify episode exists
    episode = db.query(PodcastEpisode).filter(PodcastEpisode.id == episode_id).first()
    if not episode:
        raise HTTPException(status_code=404, detail="Episode not found")

    # Create task
    task_id = str(uuid.uuid4())
    _processing_tasks[task_id] = {
        "task_id": task_id,
        "task_type": "podcast_processing",
        "status": TaskStatus.PENDING,
        "entity_id": episode_id,
        "current_stage": None,
        "progress": 0.0,
        "error": None,
        "result": None,
    }

    # Run in background
    background_tasks.add_task(_run_podcast_processing, episode_id, task_id)

    return ProcessingStatusResponse(**_processing_tasks[task_id])


@router.get("/process/status/{task_id}", response_model=ProcessingStatusResponse)
def get_processing_status(task_id: str):
    """Get status of a processing task."""
    task = _processing_tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return ProcessingStatusResponse(**task)


def _run_podcast_processing(episode_id: int, task_id: str):
    """
    Background task to process a podcast episode through all stages.

    Stages:
    1. Download audio to GCS (if not already done)
    2. Transcribe using Chirp (if no transcript exists)
    3. Index transcript into vector database
    """
    from innovation_intelligence.db.session import SessionLocal
    from innovation_intelligence.ingestion.podcasts.chirp_transcription import ChirpTranscriptionService
    from innovation_intelligence.ingestion.gcs_service import GCSStorageService
    from innovation_intelligence.db.models import PodcastChunkVector
    from innovation_intelligence.api.sse_broadcaster import broadcast_episode_status

    session = SessionLocal()
    try:
        _processing_tasks[task_id]["status"] = TaskStatus.RUNNING

        # Fetch episode
        episode = session.get(PodcastEpisode, episode_id)
        if not episode:
            raise ValueError(f"Episode {episode_id} not found")

        log.info(f"[PROCESSING] Starting processing for episode {episode_id}")

        # Stage 1: Download (skip if already downloaded)
        if not episode.gcs_audio_uri:
            _processing_tasks[task_id]["current_stage"] = "download"
            _processing_tasks[task_id]["progress"] = 0.1
            log.info(f"[PROCESSING] Stage 1/3: Downloading audio for episode {episode_id}")

            # Broadcast status change
            broadcast_episode_status(episode_id, "downloading")

            # Download using URL and upload to GCS
            from innovation_intelligence.ingestion.gcs_service import GCSStorageService
            import requests
            import tempfile

            gcs_service = GCSStorageService()

            # Download audio to temp file
            with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as tmp_file:
                response = requests.get(episode.audio_url, stream=True, timeout=120)
                response.raise_for_status()

                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        tmp_file.write(chunk)

                tmp_path = tmp_file.name

            try:
                # Upload to GCS
                gcs_uri = gcs_service.upload_podcast_audio(tmp_path, episode_id=str(episode_id))
                episode.gcs_audio_uri = gcs_uri
                session.commit()
                log.info(f"[PROCESSING] Download complete: {gcs_uri}")
            finally:
                # Clean up temp file
                import os
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
        else:
            log.info(f"[PROCESSING] Skipping download (already exists): {episode.gcs_audio_uri}")

        # Stage 2: Transcribe (skip if already transcribed)
        if not episode.gcs_transcript_uri:
            if not episode.gcs_audio_uri:
                raise ValueError("No audio file available for transcription")

            _processing_tasks[task_id]["current_stage"] = "transcribe"
            _processing_tasks[task_id]["progress"] = 0.4
            log.info(f"[PROCESSING] Stage 2/3: Transcribing audio for episode {episode_id}")

            # Broadcast status change
            broadcast_episode_status(episode_id, "transcribing")

            chirp_service = ChirpTranscriptionService()
            gcs_service = GCSStorageService()

            transcript_data = chirp_service.transcribe(gcs_uri=episode.gcs_audio_uri)
            gcs_transcript_uri = gcs_service.store_transcript(
                data=transcript_data,
                content_id=str(episode_id),
                content_type="podcast"
            )

            episode.gcs_transcript_uri = gcs_transcript_uri
            session.commit()
            log.info(f"[PROCESSING] Transcription complete: {gcs_transcript_uri}")
        else:
            log.info(f"[PROCESSING] Skipping transcription (already exists): {episode.gcs_transcript_uri}")

        # Stage 3: Index
        _processing_tasks[task_id]["current_stage"] = "index"
        _processing_tasks[task_id]["progress"] = 0.7
        log.info(f"[PROCESSING] Stage 3/3: Indexing transcript for episode {episode_id}")

        # Broadcast status change
        broadcast_episode_status(episode_id, "indexing")

        # Check if already indexed
        source = f"{episode.podcast_name} - {episode.episode_title}"
        existing_chunks = session.query(PodcastChunkVector).filter(
            PodcastChunkVector.source == source
        ).count()

        if existing_chunks == 0:
            if not episode.gcs_transcript_uri:
                raise ValueError("No transcript available for indexing")

            # Index the podcast - import here to avoid circular dependencies
            from innovation_intelligence.ingestion.indexing.pipeline import VectorIndexingPipeline

            pipeline = VectorIndexingPipeline(session=session)
            chunks_created = pipeline.index_podcast_episode_from_gcs(
                gcs_transcript_uri=episode.gcs_transcript_uri,
                podcast_name=episode.podcast_name,
                episode_title=episode.episode_title,
                episode_date=episode.episode_date.isoformat() if episode.episode_date else None,
            )

            log.info(f"[PROCESSING] Indexing complete: {chunks_created} chunks created")
            _processing_tasks[task_id]["result"] = {"chunks_created": chunks_created}
        else:
            log.info(f"[PROCESSING] Skipping indexing (already indexed): {existing_chunks} chunks exist")
            _processing_tasks[task_id]["result"] = {"chunks_created": 0, "skipped": True}

        # Complete
        _processing_tasks[task_id]["status"] = TaskStatus.COMPLETED
        _processing_tasks[task_id]["progress"] = 1.0
        _processing_tasks[task_id]["current_stage"] = "completed"
        log.info(f"[PROCESSING] Processing complete for episode {episode_id}")

        # Broadcast final status - ready for analysis
        broadcast_episode_status(episode_id, "ready")

    except Exception as e:
        log.error(f"[PROCESSING] Processing failed for episode {episode_id}: {e}")
        _processing_tasks[task_id]["status"] = TaskStatus.FAILED
        _processing_tasks[task_id]["error"] = str(e)
    finally:
        session.close()

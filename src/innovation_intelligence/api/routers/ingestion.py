# src/innovation_intelligence/api/routers/ingestion.py
"""Ingestion endpoints for transcription and vector indexing."""
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from innovation_intelligence.api.deps import get_db
from innovation_intelligence.api.schemas import (
    IngestionRequest,
    IngestionStatusResponse,
    SuccessResponse,
    TaskStatus,
)
from innovation_intelligence.config import settings
from innovation_intelligence.db.models import Document, PodcastEpisode
from innovation_intelligence.logger import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/ingestion", tags=["ingestion"])

# In-memory task tracking (for POC - use Redis/DB in production)
_ingestion_tasks: dict[str, dict] = {}


def _run_podcast_transcription(episode_id: int, task_id: str):
    """Background task for podcast transcription using Chirp (Google Speech API)."""
    from innovation_intelligence.db.session import SessionLocal
    from innovation_intelligence.ingestion.podcasts.chirp_transcription import ChirpTranscriptionService
    from innovation_intelligence.ingestion.gcs_service import GCSStorageService

    session = SessionLocal()
    try:
        _ingestion_tasks[task_id]["status"] = TaskStatus.RUNNING

        episode = session.get(PodcastEpisode, episode_id)
        if not episode:
            _ingestion_tasks[task_id]["status"] = TaskStatus.FAILED
            _ingestion_tasks[task_id]["error"] = f"Episode {episode_id} not found"
            return

        # Check if audio file exists in GCS
        if not episode.gcs_audio_uri:
            _ingestion_tasks[task_id]["status"] = TaskStatus.FAILED
            _ingestion_tasks[task_id]["error"] = "No audio file available in GCS"
            return

        # Run transcription from GCS using Chirp
        log.info("[INGESTION] Starting Chirp transcription for episode %d from GCS", episode_id)

        chirp_service = ChirpTranscriptionService()
        gcs_service = GCSStorageService()

        # Transcribe from GCS using Chirp
        transcript_data = chirp_service.transcribe(gcs_uri=episode.gcs_audio_uri)

        # Store transcript in GCS
        gcs_transcript_uri = gcs_service.store_transcript(
            data=transcript_data,
            content_id=str(episode_id),
            content_type="podcast"
        )

        # Update episode with transcript GCS URI
        episode.gcs_transcript_uri = gcs_transcript_uri
        session.commit()

        _ingestion_tasks[task_id]["status"] = TaskStatus.COMPLETED
        _ingestion_tasks[task_id]["result"] = {"gcs_transcript_uri": gcs_transcript_uri}
        log.info("[INGESTION] Transcription completed for episode %d: %s", episode_id, gcs_transcript_uri)

    except Exception as e:
        log.error("[INGESTION] Transcription failed: %s", e)
        _ingestion_tasks[task_id]["status"] = TaskStatus.FAILED
        _ingestion_tasks[task_id]["error"] = str(e)
    finally:
        session.close()


def _run_vector_indexing(task_id: str, podcasts_only: bool, documents_only: bool):
    """Background task for vector indexing."""
    from innovation_intelligence.db.session import SessionLocal
    from innovation_intelligence.ingestion.indexing.pipeline import run_indexing_pipeline

    session = SessionLocal()
    try:
        _ingestion_tasks[task_id]["status"] = TaskStatus.RUNNING

        log.info("[INGESTION] Starting vector indexing")
        result = run_indexing_pipeline(
            session,
            podcasts_only=podcasts_only,
            documents_only=documents_only,
        )

        _ingestion_tasks[task_id]["status"] = TaskStatus.COMPLETED
        _ingestion_tasks[task_id]["result"] = {
            "podcast_chunks": result.get("podcast_chunks", 0),
            "document_chunks": result.get("document_chunks", 0),
        }
        log.info("[INGESTION] Vector indexing completed")

    except Exception as e:
        log.error("[INGESTION] Vector indexing failed: %s", e)
        _ingestion_tasks[task_id]["status"] = TaskStatus.FAILED
        _ingestion_tasks[task_id]["error"] = str(e)
    finally:
        session.close()


@router.post("/transcribe/{episode_id}", response_model=IngestionStatusResponse)
async def transcribe_episode(
    episode_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    Start transcription for a podcast episode.

    Uses Google Speech-to-Text API (Chirp model) for audio transcription.
    Transcribes directly from GCS without local file downloads.
    """
    episode = db.get(PodcastEpisode, episode_id)
    if not episode:
        raise HTTPException(status_code=404, detail="Episode not found")

    if not episode.gcs_audio_uri:
        raise HTTPException(status_code=400, detail="Episode has no audio file in GCS. Download first.")

    import uuid
    task_id = f"transcribe_{episode_id}_{uuid.uuid4().hex[:8]}"

    _ingestion_tasks[task_id] = {
        "task_id": task_id,
        "task_type": "transcription",
        "status": TaskStatus.PENDING,
        "entity_id": episode_id,
        "error": None,
        "result": None,
    }

    background_tasks.add_task(_run_podcast_transcription, episode_id, task_id)

    return IngestionStatusResponse(
        task_id=task_id,
        task_type="transcription",
        status=TaskStatus.PENDING,
        entity_id=episode_id,
    )


@router.post("/index", response_model=IngestionStatusResponse)
async def run_vector_indexing(
    request: IngestionRequest,
    background_tasks: BackgroundTasks,
):
    """
    Run vector indexing pipeline.

    Creates embeddings for podcast transcripts and documents, stores in pgvector.
    """
    import uuid
    task_id = f"index_{uuid.uuid4().hex[:8]}"

    _ingestion_tasks[task_id] = {
        "task_id": task_id,
        "task_type": "vector_indexing",
        "status": TaskStatus.PENDING,
        "entity_id": None,
        "error": None,
        "result": None,
    }

    background_tasks.add_task(
        _run_vector_indexing,
        task_id,
        request.podcasts_only,
        request.documents_only,
    )

    return IngestionStatusResponse(
        task_id=task_id,
        task_type="vector_indexing",
        status=TaskStatus.PENDING,
    )


@router.get("/status/{task_id}", response_model=IngestionStatusResponse)
def get_ingestion_status(task_id: str):
    """Get status of an ingestion task."""
    if task_id not in _ingestion_tasks:
        raise HTTPException(status_code=404, detail="Task not found")

    task = _ingestion_tasks[task_id]
    return IngestionStatusResponse(
        task_id=task["task_id"],
        task_type=task["task_type"],
        status=task["status"],
        entity_id=task.get("entity_id"),
        error=task.get("error"),
        result=task.get("result"),
    )


@router.get("/tasks", response_model=List[IngestionStatusResponse])
def list_ingestion_tasks(
    status: Optional[TaskStatus] = None,
    task_type: Optional[str] = None,
):
    """List all ingestion tasks with optional filtering."""
    tasks = list(_ingestion_tasks.values())

    if status:
        tasks = [t for t in tasks if t["status"] == status]
    if task_type:
        tasks = [t for t in tasks if t["task_type"] == task_type]

    return [
        IngestionStatusResponse(
            task_id=t["task_id"],
            task_type=t["task_type"],
            status=t["status"],
            entity_id=t.get("entity_id"),
            error=t.get("error"),
            result=t.get("result"),
        )
        for t in tasks
    ]


@router.delete("/tasks/{task_id}", response_model=SuccessResponse)
def delete_task(task_id: str):
    """Delete a completed or failed task from the task list."""
    if task_id not in _ingestion_tasks:
        raise HTTPException(status_code=404, detail="Task not found")

    task = _ingestion_tasks[task_id]
    if task["status"] == TaskStatus.RUNNING:
        raise HTTPException(status_code=400, detail="Cannot delete a running task")

    del _ingestion_tasks[task_id]
    return SuccessResponse(message=f"Task {task_id} deleted")

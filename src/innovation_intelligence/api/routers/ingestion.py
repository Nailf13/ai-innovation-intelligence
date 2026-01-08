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
    """Background task for podcast transcription."""
    import json
    from pathlib import Path

    from innovation_intelligence.config import settings
    from innovation_intelligence.db.session import SessionLocal

    session = SessionLocal()
    try:
        _ingestion_tasks[task_id]["status"] = TaskStatus.RUNNING

        episode = session.get(PodcastEpisode, episode_id)
        if not episode:
            _ingestion_tasks[task_id]["status"] = TaskStatus.FAILED
            _ingestion_tasks[task_id]["error"] = f"Episode {episode_id} not found"
            return

        # Check if audio file exists
        if not episode.audio_path:
            _ingestion_tasks[task_id]["status"] = TaskStatus.FAILED
            _ingestion_tasks[task_id]["error"] = "No audio file available"
            return

        audio_path = Path(episode.audio_path)
        if not audio_path.exists():
            _ingestion_tasks[task_id]["status"] = TaskStatus.FAILED
            _ingestion_tasks[task_id]["error"] = f"Audio file not found: {audio_path}"
            return

        # Run transcription via Modal (lookup deployed app)
        import modal

        log.info("[INGESTION] Starting transcription for episode %d", episode_id)

        # Read audio file and send to Modal for transcription
        audio_bytes = audio_path.read_bytes()

        # Lookup the deployed Modal function directly
        Model = modal.Cls.from_name(
            "whisperx-podcast-pipeline", "Model"
        )
        model_instance = Model()
        transcript_json = model_instance.transcribe_with_diarization.remote(audio_bytes)

        # Parse the JSON result
        transcript_data = json.loads(transcript_json)

        # Add metadata
        transcript_data["_metadata"] = {
            "episode_id": episode_id,
            "episode_title": episode.episode_title,
            "podcast_name": episode.podcast_name,
        }

        # Save transcript to file
        transcript_dir = settings.paths.transcripts_dir
        transcript_dir.mkdir(parents=True, exist_ok=True)
        transcript_path = transcript_dir / f"episode_{episode_id}.json"

        with open(transcript_path, "w", encoding="utf-8") as f:
            json.dump(transcript_data, f, ensure_ascii=False, indent=2)

        # Update episode with transcript path
        episode.transcript_path = str(transcript_path)
        session.commit()

        _ingestion_tasks[task_id]["status"] = TaskStatus.COMPLETED
        _ingestion_tasks[task_id]["result"] = {"transcript_path": str(transcript_path)}
        log.info("[INGESTION] Transcription completed for episode %d", episode_id)

    except Exception as e:
        log.error("[INGESTION] Transcription failed: %s", e)
        _ingestion_tasks[task_id]["status"] = TaskStatus.FAILED
        _ingestion_tasks[task_id]["error"] = str(e)
    finally:
        session.close()


def _run_vector_indexing(task_id: str, podcasts_only: bool, documents_only: bool, with_speaker_id: bool):
    """Background task for vector indexing."""
    from innovation_intelligence.db.session import SessionLocal
    from innovation_intelligence.ingestion.vector_indexing.pipeline import run_indexing_pipeline

    session = SessionLocal()
    try:
        _ingestion_tasks[task_id]["status"] = TaskStatus.RUNNING

        log.info("[INGESTION] Starting vector indexing")
        result = run_indexing_pipeline(
            session,
            podcasts_only=podcasts_only,
            documents_only=documents_only,
            with_speaker_identification=with_speaker_id,
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

    Runs WhisperX transcription via Modal (requires Modal setup and HF_TOKEN).
    """
    episode = db.get(PodcastEpisode, episode_id)
    if not episode:
        raise HTTPException(status_code=404, detail="Episode not found")

    if not episode.audio_path:
        raise HTTPException(status_code=400, detail="Episode has no audio file. Download first.")

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
        request.with_speaker_identification,
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

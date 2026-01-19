# src/innovation_intelligence/api/routers/documents.py
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
import uuid
import asyncio
import json

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from innovation_intelligence.api.deps import get_db
from innovation_intelligence.api.schemas import (
    DocumentDB,
    DocumentListResponse,
    DocumentUploadResponse,
    ProcessingStatusResponse,
    TaskStatus,
    SuccessResponse,
)
from innovation_intelligence.api.sse_broadcaster import get_broadcaster
from innovation_intelligence.config import settings
from innovation_intelligence.db.models import Document, DocumentStatus
from innovation_intelligence.logger import get_logger
from innovation_intelligence.ingestion.documents.document_service import DocumentService  # NEW

log = get_logger(__name__)
router = APIRouter(prefix="/documents", tags=["documents"])

# In-memory task tracking for processing tasks
_processing_tasks: Dict[str, Dict[str, Any]] = {}

ALLOWED_EXTENSIONS = {".pdf", ".txt", ".md", ".docx"}


@router.post("/upload", response_model=DocumentUploadResponse)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    title: Optional[str] = Form(None),
    source_type: Optional[str] = Form(None),
    document_date: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"File type {ext} not allowed")

    # Parse document date
    doc_date = None
    if document_date:
        try:
            doc_date = datetime.fromisoformat(document_date)
        except ValueError:
            pass

    # ✅ FIX: Pass file_format explicitly from extension
    file_format = ext.lstrip(".")  # ".pdf" → "pdf"

    doc_title = title or Path(file.filename).stem.replace("_", " ")

    service = DocumentService(session=db)
    result = service.upload_document(
        file=file.file,
        filename=file.filename,      # ✅ Pass filename for format detection
        title=doc_title,
        document_date=doc_date,
        source_type=source_type,     # Business category (report, article...)
        file_format=file_format,     # ✅ Explicit format (pdf, txt...)
        extract_text=True,
    )

    if not result.success:
        raise HTTPException(status_code=500, detail=result.error or "Upload failed")

    document = db.query(Document).filter(Document.id == result.document_id).first()

    # Set initial status and trigger automatic indexing
    if document.gcs_transcript_uri:
        # Text extraction succeeded, start automatic indexing
        document.status = DocumentStatus.UPLOADING
        db.commit()

        # Trigger automatic indexing in background
        background_tasks.add_task(_run_document_indexing_background, document.id)
    else:
        # Upload succeeded but extraction failed
        document.status = DocumentStatus.FAILED
        db.commit()

    return DocumentUploadResponse(
        id=document.id,
        title=document.title,
        gcs_document_uri=document.gcs_document_uri,
        gcs_transcript_uri=document.gcs_transcript_uri,
        source_type=document.source_type,
        created_at=document.created_at,
    )


@router.get("/", response_model=DocumentListResponse)
def list_documents(skip: int = 0, limit: int = 50, source_type: Optional[str] = None, db: Session = Depends(get_db)):
    query = db.query(Document)
    if source_type:
        query = query.filter(Document.source_type == source_type)
    total = query.count()
    documents = query.offset(skip).limit(limit).all()
    return DocumentListResponse(documents=[DocumentDB.model_validate(doc) for doc in documents], count=total)


# ---------------------------------------------------------------------
# Server-Sent Events (SSE) for real-time status updates
# IMPORTANT: This route must come BEFORE /{document_id} to avoid path conflicts
# ---------------------------------------------------------------------

@router.get("/events")
async def document_events_stream(request: Request):
    """
    SSE endpoint for real-time document status updates.

    Clients connect to this endpoint to receive real-time notifications about:
    - Document processing state changes (uploading → indexing → ready)
    - Analysis state changes (ready → analyzing → analyzed)

    Returns a stream of events in SSE format:
    ```
    data: {"type": "document_status", "document_id": 123, "status": "indexing", ...}
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


@router.get("/{document_id}", response_model=DocumentDB)
def get_document(document_id: int, db: Session = Depends(get_db)):
    document = db.query(Document).filter(Document.id == document_id).first()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    return DocumentDB.model_validate(document)


@router.get("/{document_id}/analyzed")
def check_document_analyzed(document_id: int, db: Session = Depends(get_db)):
    """
    Check if a document has been analyzed (has UnitInsight records).

    Returns: {"analyzed": true/false, "insight_count": N}
    """
    from innovation_intelligence.db.models import UnitInsight
    from sqlalchemy import func

    document = db.query(Document).filter(Document.id == document_id).first()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    insight_count = db.query(func.count(UnitInsight.id)).filter(
        UnitInsight.document_id == document_id
    ).scalar()

    return {
        "analyzed": insight_count > 0,
        "insight_count": insight_count or 0
    }


@router.get("/{document_id}/processing-status")
def check_document_processing_status(document_id: int, db: Session = Depends(get_db)):
    """
    Check the processing status of a document.

    Returns: {"status": "uploading" | "processing" | "ready" | "analyzed"}

    States:
    - uploading: Document uploaded but text not extracted yet (shouldn't happen with sync extraction)
    - processing: Text extracted but not indexed yet
    - ready: Indexed and ready for analysis
    - analyzed: Analysis complete (has insights)
    """
    from innovation_intelligence.db.models import UnitInsight, DocumentChunkVector

    document = db.query(Document).filter(Document.id == document_id).first()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    # Check if analyzed
    has_insights = db.query(UnitInsight).filter(UnitInsight.document_id == document_id).first() is not None
    if has_insights:
        return {"status": "analyzed"}

    # Check if indexed (ready for analysis)
    if document.gcs_transcript_uri:
        has_chunks = db.query(DocumentChunkVector).filter(DocumentChunkVector.source == document.title).first() is not None
        if has_chunks:
            return {"status": "ready"}
        # Has transcript but not indexed yet - needs indexing
        return {"status": "processing"}

    # Check if document is uploaded (but not extracted yet)
    if document.gcs_document_uri:
        return {"status": "uploading"}

    # Shouldn't reach here if document exists
    return {"status": "uploading"}


@router.delete("/{document_id}", response_model=SuccessResponse)
def delete_document(document_id: int, delete_from_gcs: bool = False, db: Session = Depends(get_db)):
    """
    Delete a document (GCS-first mode).

    Args:
        document_id: Document ID to delete
        delete_from_gcs: Whether to also delete files from GCS (default: False)
    """
    document = db.query(Document).filter(Document.id == document_id).first()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    # Delete from GCS if requested
    if delete_from_gcs:
        from innovation_intelligence.ingestion.gcs_service import GCSStorageService
        gcs_service = GCSStorageService()

        # Delete document from GCS
        if document.gcs_document_uri:
            try:
                gcs_service.delete_file(document.gcs_document_uri)
                log.info(f"Deleted document from GCS: {document.gcs_document_uri}")
            except Exception as e:
                log.warning(f"Failed to delete document from GCS: {e}")
                # Continue anyway - DB record should be deleted

        # Delete transcript from GCS
        if document.gcs_transcript_uri:
            try:
                gcs_service.delete_file(document.gcs_transcript_uri)
                log.info(f"Deleted transcript from GCS: {document.gcs_transcript_uri}")
            except Exception as e:
                log.warning(f"Failed to delete transcript from GCS: {e}")
                # Continue anyway - DB record should be deleted

    # Delete from database
    db.delete(document)
    db.commit()
    return SuccessResponse(message=f"Document {document_id} deleted")


# ---------------------------------------------------------------------
# Unified Processing Endpoints (index documents for vector search)
# ---------------------------------------------------------------------

@router.post("/process/{document_id}", response_model=ProcessingStatusResponse)
async def process_document(
    document_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    Process document: index into vector database.

    Note: Text extraction happens during upload, so this only needs indexing.
    Returns task_id for status polling.
    """
    # Verify document exists
    document = db.query(Document).filter(Document.id == document_id).first()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    if not document.gcs_transcript_uri:
        raise HTTPException(
            status_code=400,
            detail="Document has no transcript. Upload may have failed."
        )

    # Create task
    task_id = str(uuid.uuid4())
    _processing_tasks[task_id] = {
        "task_id": task_id,
        "task_type": "document_processing",
        "status": TaskStatus.PENDING,
        "entity_id": document_id,
        "current_stage": None,
        "progress": 0.0,
        "error": None,
        "result": None,
    }

    # Run in background
    background_tasks.add_task(_run_document_processing, document_id, task_id)

    return ProcessingStatusResponse(**_processing_tasks[task_id])


@router.get("/process/status/{task_id}", response_model=ProcessingStatusResponse)
def get_processing_status(task_id: str):
    """Get status of a processing task."""
    task = _processing_tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return ProcessingStatusResponse(**task)


def _run_document_indexing_background(document_id: int):
    """
    Background task to automatically index a document after upload.

    Flow: uploading → indexing → ready
    """
    from innovation_intelligence.db.session import SessionLocal
    from innovation_intelligence.db.models import DocumentChunkVector
    from innovation_intelligence.api.sse_broadcaster import broadcast_document_status
    import time

    session = SessionLocal()
    try:
        document = session.get(Document, document_id)
        if not document:
            log.error(f"[AUTO-INDEX] Document {document_id} not found")
            return

        if not document.gcs_transcript_uri:
            log.warning(f"[AUTO-INDEX] Document {document_id} has no transcript, skipping indexing")
            return

        # Document is already in UPLOADING status from upload endpoint
        # Broadcast uploading status
        broadcast_document_status(document_id, "uploading")
        log.info(f"[AUTO-INDEX] Document {document_id} in uploading state")

        # Delay to ensure uploading state is visible
        time.sleep(1.5)

        # Update status to indexing
        document.status = DocumentStatus.INDEXING
        session.commit()
        broadcast_document_status(document_id, "indexing")
        log.info(f"[AUTO-INDEX] Starting indexing for document {document_id}")

        # Check if already indexed
        existing_chunks = session.query(DocumentChunkVector).filter(
            DocumentChunkVector.source == document.title
        ).count()

        if existing_chunks == 0:
            log.info(f"[AUTO-INDEX] Indexing document {document_id}")

            # Index document using the pipeline
            from innovation_intelligence.ingestion.indexing.pipeline import VectorIndexingPipeline

            pipeline = VectorIndexingPipeline(session=session)
            chunks_created = pipeline.index_document_from_gcs(
                gcs_transcript_uri=document.gcs_transcript_uri,
                source=document.title,
                document_date=document.document_date.isoformat() if document.document_date else None,
            )

            log.info(f"[AUTO-INDEX] Indexing complete: {chunks_created} chunks created for document {document_id}")
        else:
            log.info(f"[AUTO-INDEX] Document {document_id} already indexed with {existing_chunks} chunks")

        # Update status to ready
        document.status = DocumentStatus.READY
        session.commit()
        broadcast_document_status(document_id, "ready")
        log.info(f"[AUTO-INDEX] Document {document_id} is ready")

    except Exception as e:
        log.error(f"[AUTO-INDEX] Indexing failed for document {document_id}: {e}")
        document = session.get(Document, document_id)
        if document:
            document.status = DocumentStatus.FAILED
            session.commit()
            broadcast_document_status(document_id, "failed", error=str(e))
    finally:
        session.close()


def _run_document_processing(document_id: int, task_id: str):
    """Background task to index a document (manual trigger via Process button)."""
    from innovation_intelligence.db.session import SessionLocal
    from innovation_intelligence.db.models import DocumentChunkVector
    from innovation_intelligence.api.sse_broadcaster import broadcast_document_status
    import time

    session = SessionLocal()
    try:
        _processing_tasks[task_id]["status"] = TaskStatus.RUNNING
        _processing_tasks[task_id]["current_stage"] = "uploading"
        _processing_tasks[task_id]["progress"] = 0.0

        # Fetch document
        document = session.get(Document, document_id)
        if not document:
            raise ValueError(f"Document {document_id} not found")

        log.info(f"[PROCESSING] Starting processing for document {document_id}")

        if not document.gcs_transcript_uri:
            raise ValueError("Document has no transcript")

        # Update status to uploading (simulating upload to processing queue)
        document.status = DocumentStatus.UPLOADING
        session.commit()
        broadcast_document_status(document_id, "uploading")

        # Delay to ensure uploading state is visible
        time.sleep(1.5)

        # Update status to indexing
        _processing_tasks[task_id]["current_stage"] = "indexing"
        _processing_tasks[task_id]["progress"] = 0.5
        document.status = DocumentStatus.INDEXING
        session.commit()
        broadcast_document_status(document_id, "indexing")

        # Check if already indexed
        existing_chunks = session.query(DocumentChunkVector).filter(
            DocumentChunkVector.source == document.title
        ).count()

        if existing_chunks == 0:
            log.info(f"[PROCESSING] Indexing document {document_id}")

            # Index document using the pipeline
            from innovation_intelligence.ingestion.indexing.pipeline import VectorIndexingPipeline

            pipeline = VectorIndexingPipeline(session=session)
            chunks_created = pipeline.index_document_from_gcs(
                gcs_transcript_uri=document.gcs_transcript_uri,
                source=document.title,
                document_date=document.document_date.isoformat() if document.document_date else None,
            )

            log.info(f"[PROCESSING] Indexing complete: {chunks_created} chunks created")
            _processing_tasks[task_id]["result"] = {"chunks_created": chunks_created}
        else:
            log.info(f"[PROCESSING] Skipping indexing (already indexed): {existing_chunks} chunks exist")
            _processing_tasks[task_id]["result"] = {"chunks_created": 0, "skipped": True}

        # Complete
        _processing_tasks[task_id]["status"] = TaskStatus.COMPLETED
        _processing_tasks[task_id]["progress"] = 1.0

        # Update status to ready
        document.status = DocumentStatus.READY
        session.commit()
        broadcast_document_status(document_id, "ready")

        log.info(f"[PROCESSING] Processing complete for document {document_id}")

    except Exception as e:
        log.error(f"[PROCESSING] Processing failed for document {document_id}: {e}")
        _processing_tasks[task_id]["status"] = TaskStatus.FAILED
        _processing_tasks[task_id]["error"] = str(e)

        # Update status to failed
        document = session.get(Document, document_id)
        if document:
            document.status = DocumentStatus.FAILED
            session.commit()
            broadcast_document_status(document_id, "failed", error=str(e))
    finally:
        session.close()

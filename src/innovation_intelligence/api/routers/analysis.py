# src/innovation_intelligence/api/routers/analysis.py
"""Analysis pipeline endpoints."""
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from innovation_intelligence.api.deps import get_db
from innovation_intelligence.api.schemas import (
    AnalysisRequest,
    AnalysisStatusResponse,
    PipelineStatsResponse,
    SuccessResponse,
    TaskStatus,
)
from innovation_intelligence.logger import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/analysis", tags=["analysis"])

# In-memory task tracking
_analysis_tasks: dict[str, dict] = {}

# Track the latest pipeline run metadata
_latest_pipeline_run: dict[str, Any] = {
    "timestamp": None,
    "unit_insight_ids": [],  # IDs of unit insights created in the latest run
}


def _determine_episode_status(episode, session):
    """
    Determine the current processing status of an episode based on database state.

    Returns one of: "analyzed", "ready", "indexing", "transcribing", "downloading", "needs_processing"

    Note: An episode is only "analyzed" if its insights have dimension assessments.
    """
    from innovation_intelligence.db.models import UnitInsight, PodcastChunkVector, InsightDimension, EpisodeStatus

    # First check the explicit status field
    if hasattr(episode, 'status') and episode.status:
        # If status is explicitly set and not analyzing, trust it
        if episode.status in [EpisodeStatus.DOWNLOADING, EpisodeStatus.TRANSCRIBING, EpisodeStatus.INDEXING, EpisodeStatus.NEEDS_PROCESSING, EpisodeStatus.FAILED]:
            return episode.status.value

    # Check if episode has insights with dimension assessments (fully analyzed)
    insights = session.query(UnitInsight).filter(
        UnitInsight.episode_id == episode.id
    ).all()

    if insights:
        # Check if at least one insight has dimension assessments
        has_dimensions = False
        for insight in insights:
            dimension_count = session.query(InsightDimension).filter(
                InsightDimension.unit_insight_id == insight.id
            ).count()
            if dimension_count > 0:
                has_dimensions = True
                break

        if has_dimensions:
            return "analyzed"
        # Has insights but no dimensions yet - still being analyzed or ready
        # Return ready as fallback (dimensions might be optional)

    if episode.gcs_transcript_uri:
        # Has transcript - check if indexed
        source = f"{episode.podcast_name} - {episode.episode_title}"
        has_chunks = session.query(PodcastChunkVector).filter(
            PodcastChunkVector.source == source
        ).first() is not None

        if has_chunks:
            return "ready"
        else:
            return "indexing"
    elif episode.gcs_audio_uri and episode.gcs_audio_uri != "":
        return "transcribing"
    else:
        return "needs_processing"


def _run_analysis_pipeline(task_id: str, config: dict):
    """Background task for running the analysis pipeline."""
    from innovation_intelligence.db.session import SessionLocal
    from innovation_intelligence.analysis.pipelines.batch_analysis import (
        run_full_analysis,
        run_analysis_from_insights,
        run_clustering_only,
        AnalysisPipelineConfig,
    )
    from innovation_intelligence.api.sse_broadcaster import broadcast_analysis_status, broadcast_episode_status, broadcast_document_status
    from innovation_intelligence.db.models import (
        PodcastEpisode,
        Document,
        UnitInsight,
        PodcastChunkVector,
        DocumentChunkVector,
        EpisodeStatus,
        DocumentStatus,
    )

    session = SessionLocal()
    try:
        _analysis_tasks[task_id]["status"] = TaskStatus.RUNNING

        # Broadcast that analysis has started
        broadcast_analysis_status(task_id, TaskStatus.RUNNING)

        # Capture unit insight IDs before the pipeline run
        existing_insight_ids = set(
            row[0] for row in session.query(UnitInsight.id).all()
        )

        # Set "analyzing" status for episodes and documents that are in "ready" state
        # (have indexed transcripts but no insights yet)

        episodes = session.query(PodcastEpisode).filter(
            PodcastEpisode.gcs_transcript_uri.isnot(None)
        ).all()

        analyzing_episode_ids = []
        for episode in episodes:
            # Only mark as "analyzing" if the episode is in "ready" state
            current_status = _determine_episode_status(episode, session)
            if current_status == "ready":
                # Update database status
                episode.status = EpisodeStatus.ANALYZING
                analyzing_episode_ids.append(episode.id)

        # Set "analyzing" status for documents that are in "ready" state
        # Use the status field directly - it's the source of truth
        documents = session.query(Document).filter(
            Document.status == DocumentStatus.READY
        ).all()

        analyzing_document_ids = []
        for document in documents:
            # Update database status
            document.status = DocumentStatus.ANALYZING
            analyzing_document_ids.append(document.id)
            log.info(f"[ANALYSIS] Setting document {document.id} ('{document.title}') to analyzing")

        # CRITICAL: Commit status updates BEFORE broadcasting
        # This ensures the database is updated before SSE triggers frontend refresh
        session.commit()

        # Small delay to ensure transaction is fully committed
        import time
        time.sleep(0.1)

        log.info(f"[ANALYSIS] Set {len(analyzing_episode_ids)} episodes and {len(analyzing_document_ids)} documents to 'analyzing' status")

        # Now broadcast the status changes (after commit)
        for episode_id in analyzing_episode_ids:
            broadcast_episode_status(episode_id, "analyzing")
            log.info(f"[ANALYSIS] Broadcasted 'analyzing' status for episode {episode_id}")

        for document_id in analyzing_document_ids:
            broadcast_document_status(document_id, "analyzing")
            log.info(f"[ANALYSIS] Broadcasted 'analyzing' status for document {document_id}")

        # Build pipeline config
        pipeline_config = AnalysisPipelineConfig(
            run_extraction=config.get("run_extraction", True),
            run_dimension_assessment=config.get("run_dimension_assessment", True),
            run_macro_discovery=config.get("run_macro_discovery", True),
            run_strategic_clustering=config.get("run_strategic_clustering", True),
            force_reextract=config.get("force_reextract", False),
            macro_similarity_threshold=config.get("macro_similarity_threshold", 0.7),
            cluster_similarity_threshold=config.get("cluster_similarity_threshold", 0.5),
            use_llm_naming=config.get("use_llm_naming", True),
            generate_macro_descriptions=config.get("generate_macro_descriptions", False),
            continue_on_error=config.get("continue_on_error", True),
        )

        # Determine which pipeline to run
        if config.get("clustering_only"):
            log.info("[ANALYSIS] Running clustering only")
            result = run_clustering_only(
                session,
                macro_similarity_threshold=pipeline_config.macro_similarity_threshold,
                cluster_similarity_threshold=pipeline_config.cluster_similarity_threshold,
            )
        elif config.get("skip_extraction"):
            log.info("[ANALYSIS] Running from existing insights")
            result = run_analysis_from_insights(session, config=pipeline_config)
        else:
            log.info("[ANALYSIS] Running full pipeline")
            result = run_full_analysis(session, config=pipeline_config)

        # Build stages dict for response
        # result.stages is now a Dict[str, StageResult]
        stages_response = {}
        for name, stage in result.stages.items():
            stages_response[name] = {
                "success": stage.success,
                "items_processed": stage.items_processed,
                "items_created": stage.items_created,
                "duration_seconds": stage.duration_seconds,
                "error": stage.error,  # Uses the property that returns first error
            }

        _analysis_tasks[task_id]["status"] = TaskStatus.COMPLETED if result.success else TaskStatus.FAILED
        _analysis_tasks[task_id]["result"] = {
            "success": result.success,
            "stages": stages_response,
            "total_duration_seconds": result.total_duration_seconds,
        }
        log.info("[ANALYSIS] Pipeline completed: success=%s", result.success)

        # CRITICAL: Commit the transaction before broadcasting status
        # This ensures all insights and dimensions are visible in other sessions
        session.commit()
        log.info("[ANALYSIS] Transaction committed")

        # Capture newly created unit insight IDs
        all_insight_ids = set(
            row[0] for row in session.query(UnitInsight.id).all()
        )
        new_insight_ids = sorted(list(all_insight_ids - existing_insight_ids))

        # Update global latest pipeline run tracker
        _latest_pipeline_run["timestamp"] = datetime.now().isoformat()
        _latest_pipeline_run["unit_insight_ids"] = new_insight_ids

        log.info(f"[ANALYSIS] Pipeline created {len(new_insight_ids)} new unit insights: {new_insight_ids}")

        # Update episode statuses in database and broadcast
        all_episodes = session.query(PodcastEpisode).all()
        for episode in all_episodes:
            status = _determine_episode_status(episode, session)
            # Update database status if it changed
            if status == "analyzed":
                episode.status = EpisodeStatus.ANALYZED
            elif status == "ready":
                episode.status = EpisodeStatus.READY

        # Update document statuses in database and broadcast
        all_documents = session.query(Document).all()
        for document in all_documents:
            # Check if document has insights with dimension assessments (fully analyzed)
            insights = session.query(UnitInsight).filter(
                UnitInsight.document_id == document.id
            ).all()

            if insights:
                # Check if at least one insight has dimension assessments
                has_dimensions = False
                for insight in insights:
                    from innovation_intelligence.db.models import InsightDimension
                    dimension_count = session.query(InsightDimension).filter(
                        InsightDimension.unit_insight_id == insight.id
                    ).count()
                    if dimension_count > 0:
                        has_dimensions = True
                        break

                if has_dimensions:
                    document.status = DocumentStatus.ANALYZED
                else:
                    # Has insights but no dimensions - consider it ready
                    document.status = DocumentStatus.READY

        # Commit status updates
        session.commit()
        log.info("[ANALYSIS] Updated episode and document statuses in database")

        # Broadcast completion
        broadcast_analysis_status(
            task_id,
            TaskStatus.COMPLETED if result.success else TaskStatus.FAILED,
            result=_analysis_tasks[task_id]["result"]
        )

        # Broadcast final status for ALL episodes
        for episode in all_episodes:
            status = episode.status.value if hasattr(episode, 'status') and episode.status else _determine_episode_status(episode, session)
            broadcast_episode_status(episode.id, status)

        # Broadcast final status for ALL documents
        for document in all_documents:
            status = document.status.value if hasattr(document, 'status') and document.status else "ready"
            broadcast_document_status(document.id, status)

    except Exception as e:
        log.exception("[ANALYSIS] Pipeline failed: %s", e)
        _analysis_tasks[task_id]["status"] = TaskStatus.FAILED
        _analysis_tasks[task_id]["error"] = str(e)

        # Rollback any uncommitted changes
        session.rollback()
        log.info("[ANALYSIS] Transaction rolled back due to error")

        # Broadcast failure
        broadcast_analysis_status(task_id, TaskStatus.FAILED, error=str(e))

        # Restore episode statuses to their correct state after failure
        all_episodes = session.query(PodcastEpisode).all()
        for episode in all_episodes:
            status = _determine_episode_status(episode, session)
            broadcast_episode_status(episode.id, status)

        # Restore document statuses to their correct state after failure
        all_documents = session.query(Document).all()
        for document in all_documents:
            # Determine correct status based on database state
            insights = session.query(UnitInsight).filter(
                UnitInsight.document_id == document.id
            ).all()

            if insights:
                from innovation_intelligence.db.models import InsightDimension
                has_dimensions = any(
                    session.query(InsightDimension).filter(
                        InsightDimension.unit_insight_id == insight.id
                    ).count() > 0
                    for insight in insights
                )
                status = "analyzed" if has_dimensions else "ready"
            else:
                # Check if indexed
                has_chunks = session.query(DocumentChunkVector).filter(
                    DocumentChunkVector.source == document.title
                ).first() is not None
                status = "ready" if has_chunks else "indexing"

            broadcast_document_status(document.id, status)
    finally:
        session.close()


@router.post("/run", response_model=AnalysisStatusResponse)
async def run_analysis(
    request: AnalysisRequest,
    background_tasks: BackgroundTasks,
):
    """
    Run the analysis pipeline.

    The pipeline has 4 stages:
    1. Insight Extraction - Extract unit insights from transcripts/documents
    2. Dimension Assessment - Assess adoption, expectation, progress dimensions
    3. Macro Insight Discovery - Cluster unit insights into macro insights
    4. Strategic Clustering - Assign macro insights to strategic clusters
    """
    import uuid
    task_id = f"analysis_{uuid.uuid4().hex[:8]}"

    config = {
        "run_extraction": request.run_extraction,
        "run_dimension_assessment": request.run_dimension_assessment,
        "run_macro_discovery": request.run_macro_discovery,
        "run_strategic_clustering": request.run_strategic_clustering,
        "skip_extraction": request.skip_extraction,
        "clustering_only": request.clustering_only,
        "force_reextract": request.force_reextract,
        "macro_similarity_threshold": request.macro_similarity_threshold,
        "cluster_similarity_threshold": request.cluster_similarity_threshold,
        "use_llm_naming": request.use_llm_naming,
        "generate_macro_descriptions": request.generate_macro_descriptions,
        "continue_on_error": request.continue_on_error,
    }

    _analysis_tasks[task_id] = {
        "task_id": task_id,
        "status": TaskStatus.PENDING,
        "config": config,
        "error": None,
        "result": None,
    }

    background_tasks.add_task(_run_analysis_pipeline, task_id, config)

    return AnalysisStatusResponse(
        task_id=task_id,
        status=TaskStatus.PENDING,
    )


@router.get("/status/{task_id}", response_model=AnalysisStatusResponse)
def get_analysis_status(task_id: str):
    """Get status of an analysis task."""
    if task_id not in _analysis_tasks:
        raise HTTPException(status_code=404, detail="Task not found")

    task = _analysis_tasks[task_id]
    return AnalysisStatusResponse(
        task_id=task["task_id"],
        status=task["status"],
        error=task.get("error"),
        result=task.get("result"),
    )


@router.get("/tasks", response_model=List[AnalysisStatusResponse])
def list_analysis_tasks(status: Optional[TaskStatus] = None):
    """List all analysis tasks with optional status filtering."""
    tasks = list(_analysis_tasks.values())

    if status:
        tasks = [t for t in tasks if t["status"] == status]

    return [
        AnalysisStatusResponse(
            task_id=t["task_id"],
            status=t["status"],
            error=t.get("error"),
            result=t.get("result"),
        )
        for t in tasks
    ]


@router.get("/stats", response_model=PipelineStatsResponse)
def get_pipeline_stats(db: Session = Depends(get_db)):
    """Get current pipeline statistics."""
    from innovation_intelligence.analysis.pipelines.batch_analysis import get_pipeline_statistics

    stats = get_pipeline_statistics(db)
    return PipelineStatsResponse(**stats)


@router.delete("/tasks/{task_id}", response_model=SuccessResponse)
def delete_analysis_task(task_id: str):
    """Delete a completed or failed analysis task."""
    if task_id not in _analysis_tasks:
        raise HTTPException(status_code=404, detail="Task not found")

    task = _analysis_tasks[task_id]
    if task["status"] == TaskStatus.RUNNING:
        raise HTTPException(status_code=400, detail="Cannot delete a running task")

    del _analysis_tasks[task_id]
    return SuccessResponse(message=f"Task {task_id} deleted")


@router.post("/reset", response_model=SuccessResponse)
def reset_analysis_data(
    reset_dimensions: bool = False,
    reset_clustering: bool = False,
    reset_insights: bool = False,
    reset_all: bool = False,
    db: Session = Depends(get_db),
):
    """
    Reset analysis data (dangerous operation).

    Use with caution - this permanently deletes data.
    """
    from innovation_intelligence.db.models import (
        UnitInsight,
        MacroInsight,
        Cluster,
        InsightDimension,
        DimensionEvidence,
    )

    counts = {}

    if reset_all or reset_dimensions:
        counts["evidence"] = db.query(DimensionEvidence).delete()
        counts["dimensions"] = db.query(InsightDimension).delete()

    if reset_all or reset_clustering:
        db.query(MacroInsight).update({MacroInsight.cluster_id: None})
        counts["macro_insights"] = db.query(MacroInsight).delete()
        counts["clusters"] = db.query(Cluster).delete()

    if reset_all or reset_insights:
        db.query(UnitInsight).update({UnitInsight.macro_insight_id: None})
        counts["unit_insights"] = db.query(UnitInsight).delete()

    db.commit()

    deleted_items = ", ".join(f"{k}: {v}" for k, v in counts.items() if v > 0)
    return SuccessResponse(message=f"Reset complete. Deleted: {deleted_items or 'nothing'}")
# src/innovation_intelligence/analysis/pipelines/batch_analysis.py
"""
Full analysis pipeline for Innovation Intelligence (GCS-first mode).

This module orchestrates the complete insight analysis workflow:
1. **Insight Extraction**: Extract unit insights from transcripts via LLM
2. **Dimension Assessment**: Assess adoption/expectation/progress via RAG
3. **Macro Insight Discovery**: Cluster unit insights semantically
4. **Strategic Clustering**: Assign macro insights to strategic clusters

Transcript sources:
- GCS URIs only: gs://bucket/podcasts/transcripts/episode.json
- All transcripts must be stored in GCS
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Union

from sqlalchemy.orm import Session

from innovation_intelligence.analysis.insights.insight_extraction import TranscriptSource
from innovation_intelligence.db.models import (
    Cluster,
    Document,
    MacroInsight,
    PodcastEpisode,
    UnitInsight,
)
from innovation_intelligence.logger import get_logger

log = get_logger(__name__)

SourceEntity = Union[PodcastEpisode, Document]


# ---------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------
@dataclass
class AnalysisPipelineConfig:
    """Configuration for the analysis pipeline.

    All defaults are derived from the central AnalysisConfig.
    Use ``AnalysisPipelineConfig.from_analysis_config()`` for canonical defaults.
    """

    # Stage toggles
    run_extraction: bool = True
    run_dimension_assessment: bool = True
    run_macro_discovery: bool = True
    run_strategic_clustering: bool = True

    # Extraction settings
    force_reextract: bool = False

    # Dimension assessment settings (RAG retrieval parameters)
    dimension_top_k: int = 3
    dimension_min_similarity: float = 0.4

    # Macro insight discovery settings
    macro_similarity_threshold: float = 0.72
    use_llm_naming: bool = True
    generate_macro_descriptions: bool = True

    # Strategic clustering settings
    cluster_similarity_threshold: float = 0.5

    # Deduplication threshold
    dedup_threshold: float = 0.90

    # Processing options
    batch_size: int = 10
    continue_on_error: bool = True
    max_workers: int = 3

    @classmethod
    def from_analysis_config(cls, cfg: "AnalysisConfig") -> "AnalysisPipelineConfig":
        """Construct from the central AnalysisConfig."""
        return cls(
            run_extraction=cfg.run_extraction,
            run_dimension_assessment=cfg.run_dimension_assessment,
            run_macro_discovery=cfg.run_macro_discovery,
            run_strategic_clustering=cfg.run_strategic_clustering,
            force_reextract=cfg.force_reextract,
            dimension_top_k=cfg.rag.top_k,
            dimension_min_similarity=cfg.rag.min_similarity,
            macro_similarity_threshold=cfg.thresholds.macro_discovery,
            use_llm_naming=cfg.llm_naming.use_llm_naming,
            generate_macro_descriptions=cfg.llm_naming.generate_descriptions,
            cluster_similarity_threshold=cfg.thresholds.strategic_clustering,
            dedup_threshold=cfg.thresholds.dedup,
            batch_size=cfg.processing.batch_size,
            continue_on_error=cfg.processing.continue_on_error,
            max_workers=cfg.processing.max_workers,
        )


@dataclass
class StageResult:
    """Result of a single pipeline stage."""
    stage_name: str
    success: bool
    items_processed: int = 0
    items_created: int = 0
    errors: List[str] = field(default_factory=list)
    duration_seconds: float = 0.0
    details: Dict[str, Any] = field(default_factory=dict)

    @property
    def error(self) -> Optional[str]:
        """Return first error or None."""
        return self.errors[0] if self.errors else None


@dataclass
class PipelineResult:
    """Aggregate result of the full pipeline run."""
    started_at: datetime = field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None
    stages: Dict[str, StageResult] = field(default_factory=dict)  # Changed to Dict
    total_errors: int = 0

    def add_stage(self, result: StageResult) -> None:
        """Add a stage result."""
        self.stages[result.stage_name] = result
        self.total_errors += len(result.errors)

    def complete(self) -> None:
        """Mark pipeline as complete."""
        self.completed_at = datetime.now()

    @property
    def total_duration_seconds(self) -> float:
        """Total duration in seconds."""
        if self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return 0.0

    # Alias for backwards compatibility
    @property
    def duration_seconds(self) -> float:
        return self.total_duration_seconds

    @property
    def success(self) -> bool:
        """Whether all stages succeeded."""
        return all(s.success for s in self.stages.values())

    def summary(self) -> str:
        """Generate a summary string."""
        lines = [
            "=" * 60,
            "ANALYSIS PIPELINE RESULTS",
            "=" * 60,
            f"Started:  {self.started_at.strftime('%Y-%m-%d %H:%M:%S')}",
            f"Duration: {self.total_duration_seconds:.1f}s",
            f"Status:   {'SUCCESS' if self.success else 'FAILED'}",
            "-" * 60,
        ]

        for name, stage in self.stages.items():
            status = "OK" if stage.success else "FAIL"
            lines.append(
                f"  {name:30s} [{status}] "
                f"{stage.items_created:4d} created ({stage.duration_seconds:.1f}s)"
            )
            if stage.errors:
                for err in stage.errors[:3]:
                    lines.append(f"    - {err[:60]}...")

        lines.append("-" * 60)
        lines.append(f"Total errors: {self.total_errors}")
        lines.append("=" * 60)

        return "\n".join(lines)


# ---------------------------------------------------------------------
# Stage 1: Insight Extraction (Parallelized)
# ---------------------------------------------------------------------
def _extract_single_source(
    source_id: int,
    source_type: str,
    gcs_uri: str,
    force: bool,
    client: "GeminiClient",
) -> Dict[str, Any]:
    """
    Worker function for parallel extraction. Runs in a thread.

    Performs I/O-bound work (GCS load + LLM extraction + embedding) without
    any DB writes. Returns a dict with results for sequential persistence.

    Args:
        source_id: ID of the source entity
        source_type: "episode" or "document"
        gcs_uri: GCS URI for the transcript
        force: Whether to force re-extraction
        client: Shared GeminiClient (thread-safe)

    Returns:
        Dict with extraction results or error info
    """
    from innovation_intelligence.analysis.insights.insight_extraction import (
        extract_insights_from_transcript,
        _get_existing_insights_count,
        _is_episode,
        Insight,
    )
    from innovation_intelligence.analysis.insights.embedder import embed_text
    from innovation_intelligence.db.session import SessionLocal
    from innovation_intelligence.db.models import PodcastEpisode, Document

    result_dict: Dict[str, Any] = {
        "source_id": source_id,
        "source_type": source_type,
        "error": None,
        "extraction": None,
        "embeddings": [],  # List of (insight_index, embedding) pairs
        "skipped": False,
    }

    # Check if already extracted using a thread-local session
    local_session = SessionLocal()
    try:
        if source_type == "episode":
            source = local_session.get(PodcastEpisode, source_id)
        else:
            source = local_session.get(Document, source_id)

        if source is None:
            result_dict["error"] = f"Source {source_type}:{source_id} not found"
            return result_dict

        existing_count = _get_existing_insights_count(local_session, source)
        if existing_count > 0 and not force:
            log.info(
                f"[PIPELINE] {existing_count} insights already exist for "
                f"{source_type}:{source_id}, skipping"
            )
            result_dict["skipped"] = True
            return result_dict
    finally:
        local_session.close()

    # Phase 1: I/O-bound work (GCS + LLM + embeddings) — no DB writes
    try:
        extraction = extract_insights_from_transcript(
            gcs_uri=gcs_uri,
            client=client,
        )
        result_dict["extraction"] = extraction

        # Compute embeddings for each insight
        for i, ins in enumerate(extraction.insights):
            embedding = embed_text(f"{ins.name}. {ins.description}")
            result_dict["embeddings"].append((i, embedding))

        log.info(
            f"[PIPELINE] Extracted {len(extraction.insights)} insights "
            f"from {source_type}:{source_id}"
        )

    except Exception as e:
        result_dict["error"] = f"Extraction failed for {source_type}:{source_id}: {e}"
        log.error(f"[PIPELINE] {result_dict['error']}")

    return result_dict


def _persist_extracted_insights(
    session: Session,
    extraction_results: List[Dict[str, Any]],
    sources_by_id: Dict[int, SourceEntity],
    dedup_threshold: float = 0.90,
) -> tuple[int, int]:
    """
    Persist extracted insights sequentially in the main thread.

    Handles deduplication by checking similarity against all existing
    insights (including those just inserted from previous sources).

    Args:
        session: Main-thread DB session
        extraction_results: List of dicts from _extract_single_source()
        sources_by_id: Map of source.id -> SourceEntity
        dedup_threshold: Similarity threshold for deduplication

    Returns:
        Tuple of (sources_processed, insights_created)
    """
    from innovation_intelligence.analysis.insights.insight_extraction import (
        _find_most_similar_insight,
        _merge_sources,
        _is_episode,
    )

    sources_processed = 0
    insights_created = 0

    for res in extraction_results:
        if res["error"] or res["skipped"] or res["extraction"] is None:
            continue

        source = sources_by_id.get(res["source_id"])
        if source is None:
            continue

        extraction = res["extraction"]
        embeddings_map = dict(res["embeddings"])  # index -> embedding

        created = 0
        updated = 0

        for i, ins in enumerate(extraction.insights):
            embedding = embeddings_map.get(i)
            if embedding is None:
                continue

            # Dedup check — sees all previously committed insights
            similar_insight, similarity = _find_most_similar_insight(
                session, embedding, threshold=dedup_threshold
            )

            if similar_insight is not None:
                log.info(
                    "[PIPELINE] Dedup: '%.50s' ~ '%.50s' (%.3f) -> merging",
                    ins.name,
                    similar_insight.name,
                    similarity,
                )
                _merge_sources(similar_insight, source)
                updated += 1
            else:
                unit_insight = UnitInsight(
                    name=ins.name,
                    description=ins.description,
                    type=ins.type,
                    embedding=embedding,
                    episode_id=source.id if _is_episode(source) else None,
                    document_id=source.id if not _is_episode(source) else None,
                )
                session.add(unit_insight)
                created += 1

        session.commit()
        sources_processed += 1
        insights_created += created + updated

        log.info(
            f"[PIPELINE] Persisted insights for {source}: "
            f"{created} created, {updated} deduplicated"
        )

    return sources_processed, insights_created


def _run_extraction_stage(
    session: Session,
    sources: List[SourceEntity],
    transcript_sources: Dict[int, TranscriptSource],
    config: AnalysisPipelineConfig,
) -> StageResult:
    """
    Run the insight extraction stage with parallel workers.

    Phase 1 (parallel): GCS load + LLM extraction + embedding in threads.
    Phase 2 (sequential): Dedup + DB persistence in the main thread.
    """
    import time
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from innovation_intelligence.analysis.insights.insight_extraction import (
        _get_default_client,
        _is_episode,
    )

    start = time.time()
    result = StageResult(stage_name="1. Insight Extraction", success=True)

    # Build work items
    work_items = []
    sources_by_id: Dict[int, SourceEntity] = {}
    for source in sources:
        gcs_uri = transcript_sources.get(source.id)
        if not gcs_uri:
            result.errors.append(f"No transcript for source {source.id}")
            continue
        source_type = "episode" if _is_episode(source) else "document"
        work_items.append((source.id, source_type, gcs_uri))
        sources_by_id[source.id] = source

    if not work_items:
        result.duration_seconds = time.time() - start
        return result

    # Shared GeminiClient (thread-safe)
    client = _get_default_client()

    # Phase 1: Parallel extraction
    num_workers = min(config.max_workers, len(work_items))
    log.info(
        f"[PIPELINE] Starting parallel extraction: "
        f"{len(work_items)} sources, {num_workers} workers"
    )

    extraction_results: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=num_workers) as executor:
        future_to_source = {
            executor.submit(
                _extract_single_source,
                source_id,
                source_type,
                gcs_uri,
                config.force_reextract,
                client,
            ): source_id
            for source_id, source_type, gcs_uri in work_items
        }

        for future in as_completed(future_to_source):
            source_id = future_to_source[future]
            try:
                res = future.result()
                extraction_results.append(res)

                if res["error"]:
                    result.errors.append(res["error"])
                    if not config.continue_on_error:
                        # Cancel remaining futures
                        for f in future_to_source:
                            f.cancel()
                        result.success = False
                        break

            except Exception as e:
                error_msg = f"Worker exception for source {source_id}: {e}"
                log.error(f"[PIPELINE] {error_msg}")
                result.errors.append(error_msg)
                if not config.continue_on_error:
                    for f in future_to_source:
                        f.cancel()
                    result.success = False
                    break

    # Phase 2: Sequential persistence with dedup
    log.info("[PIPELINE] Persisting extracted insights (sequential dedup)...")
    sources_processed, insights_created = _persist_extracted_insights(
        session, extraction_results, sources_by_id,
        dedup_threshold=config.dedup_threshold,
    )

    result.items_processed = sources_processed
    result.items_created = insights_created
    result.duration_seconds = time.time() - start
    result.details["sources_processed"] = sources_processed
    result.details["insights_extracted"] = insights_created

    if result.errors and not config.continue_on_error:
        result.success = False

    return result


# ---------------------------------------------------------------------
# Stage 2: Macro Insight Discovery
# ---------------------------------------------------------------------
def _run_macro_discovery_stage(
    session: Session,
    config: AnalysisPipelineConfig,
) -> StageResult:
    """
    Run the macro insight discovery stage.
    """
    import time
    from innovation_intelligence.analysis.insights.macro_insight_discovery import (
        discover_and_persist_macro_insights,
    )

    start = time.time()
    result = StageResult(stage_name="2. Macro Insight Discovery", success=True)

    try:
        # Count unassigned unit insights before
        unassigned_before = session.query(UnitInsight).filter(
            UnitInsight.macro_insight_id.is_(None)
        ).count()

        result.items_processed = unassigned_before

        # Run discovery
        macro_insights = discover_and_persist_macro_insights(
            session,
            similarity_threshold=config.macro_similarity_threshold,
            use_llm_naming=config.use_llm_naming,
            generate_descriptions=config.generate_macro_descriptions,
        )

        result.items_created = len(macro_insights)

        # Calculate cluster sizes
        cluster_sizes = [
            session.query(UnitInsight).filter(
                UnitInsight.macro_insight_id == mi.id
            ).count()
            for mi in macro_insights
        ]

        result.details["macro_insights_created"] = len(macro_insights)
        result.details["avg_cluster_size"] = (
            sum(cluster_sizes) / len(cluster_sizes) if cluster_sizes else 0
        )
        result.details["max_cluster_size"] = max(cluster_sizes) if cluster_sizes else 0

        log.info(
            f"[PIPELINE] Created {len(macro_insights)} macro insights "
            f"from {unassigned_before} unit insights"
        )

    except Exception as e:
        error_msg = f"Macro discovery failed: {e}"
        log.error(f"[PIPELINE] {error_msg}")
        result.errors.append(error_msg)
        result.success = False

    result.duration_seconds = time.time() - start

    return result


# ---------------------------------------------------------------------
# Stage 3: Strategic Clustering
# ---------------------------------------------------------------------
def _run_strategic_clustering_stage(
    session: Session,
    config: AnalysisPipelineConfig,
) -> StageResult:
    """
    Run the strategic clustering stage.

    This stage performs two types of clustering:
    1. Assign MacroInsights to strategic clusters
    2. Assign orphan UnitInsights (without MacroInsight) directly to strategic clusters
    """
    import time
    from innovation_intelligence.analysis.clustering.clustering import (
        assign_and_persist_macro_insights,
        assign_and_persist_orphan_unit_insights,
    )

    start = time.time()
    result = StageResult(stage_name="3. Strategic Clustering", success=True)

    try:
        # Part 1: Cluster MacroInsights
        log.info("[PIPELINE] Clustering MacroInsights to strategic clusters...")

        # Count unassigned macro insights before
        unassigned_macros = session.query(MacroInsight).filter(
            MacroInsight.cluster_id.is_(None)
        ).count()

        # Run macro insight clustering
        macro_clustering_result = assign_and_persist_macro_insights(
            session,
            similarity_threshold=config.cluster_similarity_threshold,
        )

        log.info(
            f"[PIPELINE] Assigned {macro_clustering_result.total_processed} macro insights "
            f"to {len(macro_clustering_result.clusters_used)} clusters"
        )

        # Part 2: Cluster orphan UnitInsights (without MacroInsight)
        log.info("[PIPELINE] Clustering orphan UnitInsights to strategic clusters...")

        # Count orphan unit insights
        orphan_units = session.query(UnitInsight).filter(
            UnitInsight.macro_insight_id.is_(None),
            UnitInsight.cluster_id.is_(None)
        ).count()

        # Run orphan unit insight clustering
        unit_clustering_result = assign_and_persist_orphan_unit_insights(
            session,
            similarity_threshold=config.cluster_similarity_threshold,
        )

        log.info(
            f"[PIPELINE] Assigned {unit_clustering_result.total_processed} orphan unit insights "
            f"to {len(unit_clustering_result.clusters_used)} clusters"
        )

        # Combine results
        result.items_processed = unassigned_macros + orphan_units
        result.items_created = macro_clustering_result.total_processed + unit_clustering_result.total_processed

        # Merge cluster usage statistics
        all_clusters_used = {}
        for cluster_name, count in macro_clustering_result.clusters_used.items():
            all_clusters_used[cluster_name] = all_clusters_used.get(cluster_name, 0) + count
        for cluster_name, count in unit_clustering_result.clusters_used.items():
            all_clusters_used[cluster_name] = all_clusters_used.get(cluster_name, 0) + count

        result.details["clusters_used"] = all_clusters_used
        result.details["macro_insights_assigned"] = macro_clustering_result.total_processed
        result.details["unit_insights_assigned"] = unit_clustering_result.total_processed
        result.details["assigned_to_other"] = (
            macro_clustering_result.total_assigned_to_other +
            unit_clustering_result.total_assigned_to_other
        )

        log.info(
            f"[PIPELINE] Strategic clustering complete: "
            f"{macro_clustering_result.total_processed} macro insights + "
            f"{unit_clustering_result.total_processed} orphan unit insights assigned "
            f"to {len(all_clusters_used)} clusters"
        )

    except Exception as e:
        error_msg = f"Strategic clustering failed: {e}"
        log.error(f"[PIPELINE] {error_msg}")
        result.errors.append(error_msg)
        result.success = False

    result.duration_seconds = time.time() - start

    return result


# ---------------------------------------------------------------------
# Stage 4: Dimension Assessment (Parallelized)
# ---------------------------------------------------------------------
def _assess_single_insight(
    insight_id: int,
    assessment_config: "AssessmentConfig",
    gemini_client: "GeminiClient",
) -> Dict[str, Any]:
    """
    Worker function for parallel dimension assessment. Runs in a thread.

    Each worker gets its own DB session and DimensionAssessmentService.
    Persistence happens directly in the worker (no cross-insight dedup needed).

    Args:
        insight_id: ID of the UnitInsight to assess
        assessment_config: Shared assessment configuration
        gemini_client: Shared GeminiClient (thread-safe)

    Returns:
        Dict with assessment results or error info
    """
    from innovation_intelligence.analysis.dimensions import (
        DimensionAssessmentService,
    )
    from innovation_intelligence.db.session import SessionLocal

    result_dict: Dict[str, Any] = {
        "insight_id": insight_id,
        "dimensions_created": 0,
        "error": None,
    }

    local_session = SessionLocal()
    try:
        insight = local_session.get(UnitInsight, insight_id)
        if insight is None:
            result_dict["error"] = f"Insight {insight_id} not found"
            return result_dict

        # Each worker gets its own service with its own session + RAG engine
        service = DimensionAssessmentService(
            local_session, assessment_config, gemini_client
        )

        assessment = service.assess_insight(insight)

        # Count dimensions created
        dims = 0
        if hasattr(assessment, 'adoption'):
            dims += sum([
                1 if assessment.adoption else 0,
                1 if assessment.expectation else 0,
                1 if assessment.progress else 0,
            ])
        elif hasattr(assessment, 'criticality'):
            dims += sum([
                1 if assessment.criticality else 0,
                1 if assessment.urgency else 0,
                1 if assessment.actionability else 0,
            ])

        result_dict["dimensions_created"] = dims
        log.info(
            f"[PIPELINE] Assessed {dims} dimensions for insight {insight_id}"
        )

    except Exception as e:
        result_dict["error"] = (
            f"Assessment failed for insight {insight_id}: {e}"
        )
        log.error(f"[PIPELINE] {result_dict['error']}")
    finally:
        local_session.close()

    return result_dict


def _run_dimension_assessment_stage(
    session: Session,
    config: AnalysisPipelineConfig,
) -> StageResult:
    """
    Run the dimension assessment stage with parallel workers.

    Each insight is assessed independently in its own thread with its own
    DB session and DimensionAssessmentService instance.
    """
    import time
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from innovation_intelligence.analysis.dimensions import (
        AssessmentConfig,
        RAGEngineConfig,
    )
    from innovation_intelligence.analysis.insights.insight_extraction import (
        _get_default_client,
    )
    from innovation_intelligence.db.models import InsightDimension

    start = time.time()
    result = StageResult(stage_name="4. Dimension Assessment", success=True)

    try:
        # Configure assessment
        rag_config = RAGEngineConfig(
            top_k=config.dimension_top_k,
            min_similarity=config.dimension_min_similarity,
        )
        assessment_config = AssessmentConfig(
            rag_config=rag_config,
            persist_results=True,
            skip_existing=True,
        )

        # Query insight IDs needing assessment (main thread)
        insight_ids = [
            row[0]
            for row in session.query(UnitInsight.id)
            .outerjoin(InsightDimension)
            .filter(InsightDimension.id.is_(None))
            .all()
        ]

        if not insight_ids:
            log.info("[PIPELINE] No insights needing dimension assessment")
            result.duration_seconds = time.time() - start
            return result

        # Shared GeminiClient (thread-safe)
        client = _get_default_client()

        num_workers = min(config.max_workers, len(insight_ids))
        log.info(
            f"[PIPELINE] Starting parallel assessment: "
            f"{len(insight_ids)} insights, {num_workers} workers"
        )

        # Parallel assessment
        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            future_to_id = {
                executor.submit(
                    _assess_single_insight,
                    iid,
                    assessment_config,
                    client,
                ): iid
                for iid in insight_ids
            }

            completed = 0
            for future in as_completed(future_to_id):
                iid = future_to_id[future]
                completed += 1
                try:
                    res = future.result()

                    if res["error"]:
                        result.errors.append(res["error"])
                        if not config.continue_on_error:
                            for f in future_to_id:
                                f.cancel()
                            result.success = False
                            break
                    else:
                        result.items_processed += 1
                        result.items_created += res["dimensions_created"]

                    if completed % 5 == 0 or completed == len(insight_ids):
                        log.info(
                            f"[PIPELINE] Assessment progress: "
                            f"{completed}/{len(insight_ids)}"
                        )

                except Exception as e:
                    error_msg = f"Worker exception for insight {iid}: {e}"
                    log.error(f"[PIPELINE] {error_msg}")
                    result.errors.append(error_msg)
                    if not config.continue_on_error:
                        for f in future_to_id:
                            f.cancel()
                        result.success = False
                        break

        log.info(
            f"[PIPELINE] Assessed {result.items_created} dimensions "
            f"for {result.items_processed} insights"
        )

    except Exception as e:
        error_msg = f"Dimension assessment failed: {e}"
        log.error(f"[PIPELINE] {error_msg}")
        result.errors.append(error_msg)
        result.success = False

    result.duration_seconds = time.time() - start
    result.details["insights_assessed"] = result.items_processed
    result.details["dimensions_created"] = result.items_created

    return result


# ---------------------------------------------------------------------
# Source Discovery
# ---------------------------------------------------------------------
def _discover_sources(
    session: Session,
    force_reextract: bool = False,
) -> tuple[List[SourceEntity], Dict[int, TranscriptSource]]:
    """
    Discover sources ready for analysis (GCS-first mode).

    Only returns sources that are "ready for analysis":
    - Have GCS transcript URI
    - Have indexed chunks in vector store
    - Do NOT have insights yet (unless force_reextract=True)

    Args:
        session: Database session
        force_reextract: If True, include already-analyzed sources

    Returns:
        Tuple of (sources list, transcript_sources dict mapping source.id to GCS URI)
    """
    from sqlalchemy import exists, and_
    from innovation_intelligence.db.models import PodcastChunkVector, DocumentChunkVector

    sources: List[SourceEntity] = []
    transcript_sources: Dict[int, TranscriptSource] = {}

    # Build query for podcast episodes READY FOR ANALYSIS
    episodes_query = session.query(PodcastEpisode).filter(
        and_(
            PodcastEpisode.gcs_transcript_uri.isnot(None),
            # Has indexed chunks
            exists().where(
                PodcastChunkVector.source == (
                    PodcastEpisode.podcast_name + " - " + PodcastEpisode.episode_title
                )
            ),
        )
    )

    # Exclude already-analyzed episodes unless force_reextract
    if not force_reextract:
        episodes_query = episodes_query.filter(
            ~exists().where(UnitInsight.episode_id == PodcastEpisode.id)
        )

    episodes = episodes_query.all()

    for episode in episodes:
        if episode.gcs_transcript_uri:
            log.debug(f"[PIPELINE] Using GCS transcript for episode {episode.id}: {episode.gcs_transcript_uri}")
            sources.append(episode)
            transcript_sources[episode.id] = episode.gcs_transcript_uri

    podcast_count = len(episodes)
    log.info(f"[PIPELINE] Found {podcast_count} podcast episodes ready for analysis")

    # Build query for documents READY FOR ANALYSIS
    documents_query = session.query(Document).filter(
        and_(
            Document.gcs_transcript_uri.isnot(None),
            # Has indexed chunks
            exists().where(
                DocumentChunkVector.source == Document.title
            ),
        )
    )

    # Exclude already-analyzed documents unless force_reextract
    if not force_reextract:
        documents_query = documents_query.filter(
            ~exists().where(UnitInsight.document_id == Document.id)
        )

    documents = documents_query.all()

    for doc in documents:
        if doc.gcs_transcript_uri:
            log.debug(f"[PIPELINE] Using GCS transcript for document {doc.id}: {doc.gcs_transcript_uri}")
            sources.append(doc)
            transcript_sources[doc.id] = doc.gcs_transcript_uri

    document_count = len(documents)
    log.info(f"[PIPELINE] Total sources ready for analysis: {len(sources)}")

    return sources, transcript_sources


# ---------------------------------------------------------------------
# Main Pipeline Entry Points
# ---------------------------------------------------------------------
def run_full_analysis(
    session: Session,
    *,
    config: Optional[AnalysisPipelineConfig] = None,
    sources: Optional[List[SourceEntity]] = None,
    transcript_sources: Optional[Dict[int, TranscriptSource]] = None,
) -> PipelineResult:
    """
    Run the complete analysis pipeline (GCS-first mode).

    Stages:
    1. Insight Extraction - Extract unit insights from transcripts (GCS only)
    2. Dimension Assessment - Assess adoption/expectation/progress via RAG
    3. Macro Insight Discovery - Cluster unit insights semantically
    4. Strategic Clustering - Assign macro insights to strategic clusters

    Args:
        session: Database session
        config: Pipeline configuration
        sources: Optional list of sources to process
        transcript_sources: Optional dict mapping source.id to GCS URI
    """
    config = config or AnalysisPipelineConfig()
    result = PipelineResult()

    log.info("=" * 60)
    log.info("[PIPELINE] Starting full analysis pipeline")
    log.info("=" * 60)

    # Discover sources if not provided
    if sources is None or transcript_sources is None:
        sources, transcript_sources = _discover_sources(session, config.force_reextract)

    if not sources:
        log.warning("[PIPELINE] No sources ready for analysis")
        result.complete()
        return result

    log.info(f"[PIPELINE] Processing {len(sources)} sources")

    # Stage 1: Insight Extraction
    if config.run_extraction:
        log.info("-" * 60)
        log.info("[PIPELINE] Stage 1: Insight Extraction")
        stage_result = _run_extraction_stage(
            session, sources, transcript_sources, config
        )
        result.add_stage(stage_result)

        if not stage_result.success and not config.continue_on_error:
            result.complete()
            return result

    # Stage 2: Macro Insight Discovery
    if config.run_macro_discovery:
        log.info("-" * 60)
        log.info("[PIPELINE] Stage 2: Macro Insight Discovery")
        stage_result = _run_macro_discovery_stage(session, config)
        result.add_stage(stage_result)

        if not stage_result.success and not config.continue_on_error:
            result.complete()
            return result

    # Stage 3: Strategic Clustering
    if config.run_strategic_clustering:
        log.info("-" * 60)
        log.info("[PIPELINE] Stage 3: Strategic Clustering")
        stage_result = _run_strategic_clustering_stage(session, config)
        result.add_stage(stage_result)

    # Stage 4: Dimension Assessment
    if config.run_dimension_assessment:
        log.info("-" * 60)
        log.info("[PIPELINE] Stage 4: Dimension Assessment")
        stage_result = _run_dimension_assessment_stage(session, config)
        result.add_stage(stage_result)

        if not stage_result.success and not config.continue_on_error:
            result.complete()
            return result

    result.complete()

    log.info("\n" + result.summary())

    return result


def run_analysis_from_insights(
    session: Session,
    *,
    config: Optional[AnalysisPipelineConfig] = None,
) -> PipelineResult:
    """
    Run analysis pipeline starting from existing insights (skip extraction).
    """
    config = config or AnalysisPipelineConfig()
    config.run_extraction = False

    return run_full_analysis(session, config=config, sources=[], transcript_sources={})


def run_clustering_only(
    session: Session,
    *,
    macro_similarity_threshold: Optional[float] = None,
    cluster_similarity_threshold: Optional[float] = None,
) -> PipelineResult:
    """
    Run only the clustering stages (macro discovery + strategic clustering).
    """
    from innovation_intelligence.analysis.analysis_config import get_analysis_config
    defaults = get_analysis_config()
    config = AnalysisPipelineConfig(
        run_extraction=False,
        run_dimension_assessment=False,
        run_macro_discovery=True,
        run_strategic_clustering=True,
        macro_similarity_threshold=macro_similarity_threshold if macro_similarity_threshold is not None else defaults.thresholds.macro_discovery,
        cluster_similarity_threshold=cluster_similarity_threshold if cluster_similarity_threshold is not None else defaults.thresholds.strategic_clustering,
    )

    return run_full_analysis(session, config=config, sources=[], transcript_sources={})


# ---------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------
def get_pipeline_statistics(session: Session) -> Dict[str, Any]:
    """
    Get current statistics for all pipeline entities.

    Updated to:
    1. Count only content NOT YET ANALYZED (no UnitInsight records)
    2. Separate "ready for analysis" from "analyzed"
    """
    from sqlalchemy import func, exists, and_
    from innovation_intelligence.db.models import InsightDimension, PodcastChunkVector, DocumentChunkVector

    # Count total podcasts and documents
    podcast_episodes_total = session.query(PodcastEpisode).count()
    documents_total = session.query(Document).count()

    # Count podcasts READY FOR ANALYSIS (transcribed + indexed BUT no insights yet)
    podcasts_ready = (
        session.query(PodcastEpisode)
        .filter(
            and_(
                PodcastEpisode.gcs_transcript_uri.isnot(None),
                # Has indexed chunks
                exists().where(
                    PodcastChunkVector.source == (
                        PodcastEpisode.podcast_name + " - " + PodcastEpisode.episode_title
                    )
                ),
                # Does NOT have insights yet (key change)
                ~exists().where(UnitInsight.episode_id == PodcastEpisode.id)
            )
        )
        .count()
    )

    # Count documents READY FOR ANALYSIS (transcribed + indexed BUT no insights yet)
    documents_ready = (
        session.query(Document)
        .filter(
            and_(
                Document.gcs_transcript_uri.isnot(None),
                # Has indexed chunks
                exists().where(
                    DocumentChunkVector.source == Document.title
                ),
                # Does NOT have insights yet (key change)
                ~exists().where(UnitInsight.document_id == Document.id)
            )
        )
        .count()
    )

    # Count ANALYZED items (have UnitInsight records)
    podcasts_analyzed = (
        session.query(PodcastEpisode.id)
        .filter(
            exists().where(UnitInsight.episode_id == PodcastEpisode.id)
        )
        .distinct()
        .count()
    )

    documents_analyzed = (
        session.query(Document.id)
        .filter(
            exists().where(UnitInsight.document_id == Document.id)
        )
        .distinct()
        .count()
    )

    # Get unit insights statistics with type breakdown
    total_unit_insights = session.query(UnitInsight).count()

    trends_count = session.query(UnitInsight).filter(
        UnitInsight.type == "trend"
    ).count()

    health_stakes_count = session.query(UnitInsight).filter(
        UnitInsight.type == "health_stake"
    ).count()

    unit_insights_with_macro = session.query(UnitInsight).filter(
        UnitInsight.macro_insight_id.isnot(None)
    ).count()

    # Count orphan unit insights with direct cluster assignment
    unit_insights_with_cluster = session.query(UnitInsight).filter(
        UnitInsight.macro_insight_id.is_(None),
        UnitInsight.cluster_id.isnot(None)
    ).count()

    # Get macro insights statistics
    total_macro_insights = session.query(MacroInsight).count()
    macro_with_cluster = session.query(MacroInsight).filter(
        MacroInsight.cluster_id.isnot(None)
    ).count()

    # Get cluster statistics
    total_clusters = session.query(Cluster).count()

    # Get dimensions assessed
    dimensions_assessed = session.query(InsightDimension).count()

    stats = {
        # Pipeline stats (ready for analysis - NOT analyzed yet)
        "podcast_episodes": podcasts_ready,
        "podcast_episodes_total": podcast_episodes_total,
        "podcasts_analyzed": podcasts_analyzed,  # NEW
        "documents": documents_ready,
        "documents_total": documents_total,
        "documents_analyzed": documents_analyzed,  # NEW

        # Unit insights stats with type breakdown
        "unit_insights": {
            "total": total_unit_insights,
            "trends": trends_count,
            "health_stakes": health_stakes_count,
            "with_macro": unit_insights_with_macro,
            "orphans_with_cluster": unit_insights_with_cluster,
            "orphans_without_cluster": total_unit_insights - unit_insights_with_macro - unit_insights_with_cluster,
        },

        # Macro insights stats
        "macro_insights": {
            "total": total_macro_insights,
            "with_cluster": macro_with_cluster,
            "without_cluster": total_macro_insights - macro_with_cluster,
        },

        # Cluster stats
        "clusters": {
            "total": total_clusters,
            "by_name": dict(
                session.query(Cluster.name, func.count(MacroInsight.id))
                .outerjoin(MacroInsight)
                .group_by(Cluster.name)
                .all()
            ),
        },

        "dimensions_assessed": dimensions_assessed,
    }

    return stats
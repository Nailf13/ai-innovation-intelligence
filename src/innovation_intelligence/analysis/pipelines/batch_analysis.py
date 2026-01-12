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
    """Configuration for the analysis pipeline."""

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
    macro_similarity_threshold: float = 0.7
    use_llm_naming: bool = True
    generate_macro_descriptions: bool = True

    # Strategic clustering settings
    cluster_similarity_threshold: float = 0.5

    # Processing options
    batch_size: int = 10  # Process N sources at a time
    continue_on_error: bool = True  # Continue if one source fails


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
# Stage 1: Insight Extraction
# ---------------------------------------------------------------------
def _run_extraction_stage(
    session: Session,
    sources: List[SourceEntity],
    transcript_sources: Dict[int, TranscriptSource],
    config: AnalysisPipelineConfig,
) -> StageResult:
    """
    Run the insight extraction stage.

    GCS-first mode: Only GCS URIs are supported as transcript sources.
    """
    import time
    from innovation_intelligence.analysis.insights.insight_extraction import (
        extract_and_persist_insights,
    )

    start = time.time()
    result = StageResult(stage_name="1. Insight Extraction", success=True)

    for source in sources:
        transcript_source = transcript_sources.get(source.id)
        if not transcript_source:
            result.errors.append(f"No transcript for source {source.id}")
            continue

        try:
            extraction, unit_insights = extract_and_persist_insights(
                gcs_uri=transcript_source,
                session=session,
                source=source,
                force=config.force_reextract,
            )

            result.items_processed += 1
            result.items_created += len(unit_insights)

            log.info(
                f"[PIPELINE] Extracted {len(unit_insights)} insights from {source}"
            )

        except Exception as e:
            error_msg = f"Extraction failed for {source}: {e}"
            log.error(f"[PIPELINE] {error_msg}")
            result.errors.append(error_msg)

            if not config.continue_on_error:
                result.success = False
                break

    result.duration_seconds = time.time() - start
    result.details["sources_processed"] = result.items_processed
    result.details["insights_extracted"] = result.items_created

    if result.errors and not config.continue_on_error:
        result.success = False

    return result


# ---------------------------------------------------------------------
# Stage 2: Dimension Assessment
# ---------------------------------------------------------------------
def _run_dimension_assessment_stage(
    session: Session,
    config: AnalysisPipelineConfig,
) -> StageResult:
    """
    Run the dimension assessment stage.
    """
    import time
    from innovation_intelligence.analysis.dimensions import (
        DimensionAssessmentService,
        AssessmentConfig,
        RAGEngineConfig,
    )

    start = time.time()
    result = StageResult(stage_name="2. Dimension Assessment", success=True)

    try:
        # Configure RAG
        rag_config = RAGEngineConfig(
            top_k=config.dimension_top_k,
            min_similarity=config.dimension_min_similarity,
        )

        assessment_config = AssessmentConfig(
            rag_config=rag_config,
            persist_results=True,
            skip_existing=True,
        )

        service = DimensionAssessmentService(session, assessment_config)

        # Get insights needing assessment
        assessments = service.assess_new_insights()

        result.items_processed = len(assessments)

        # Count dimensions created (handle both TrendDimensions and StakeDimensions)
        dimensions_count = 0
        for a in assessments:
            # Check if it's a TrendDimensions (has adoption attribute)
            if hasattr(a, 'adoption'):
                dimensions_count += sum([
                    1 if a.adoption else 0,
                    1 if a.expectation else 0,
                    1 if a.progress else 0,
                ])
            # Otherwise it's a StakeDimensions (has criticality attribute)
            elif hasattr(a, 'criticality'):
                dimensions_count += sum([
                    1 if a.criticality else 0,
                    1 if a.urgency else 0,
                    1 if a.actionability else 0,
                ])

        result.items_created = dimensions_count

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
# Stage 3: Macro Insight Discovery
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
    result = StageResult(stage_name="3. Macro Insight Discovery", success=True)

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
# Stage 4: Strategic Clustering
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
    result = StageResult(stage_name="4. Strategic Clustering", success=True)

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
    log.info(f"[PIPELINE] Found {document_count} documents ready for analysis")
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
    macro_similarity_threshold: float = 0.7,
    cluster_similarity_threshold: float = 0.5,
) -> PipelineResult:
    """
    Run only the clustering stages (macro discovery + strategic clustering).
    """
    config = AnalysisPipelineConfig(
        run_extraction=False,
        run_dimension_assessment=False,
        run_macro_discovery=True,
        run_strategic_clustering=True,
        macro_similarity_threshold=macro_similarity_threshold,
        cluster_similarity_threshold=cluster_similarity_threshold,
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
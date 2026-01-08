# src/innovation_intelligence/analysis/pipelines/batch_analysis.py
"""
Full analysis pipeline for Innovation Intelligence.

This module orchestrates the complete insight analysis workflow:
1. **Insight Extraction**: Extract unit insights from transcripts via LLM
2. **Dimension Assessment**: Assess adoption/expectation/progress via RAG
3. **Macro Insight Discovery**: Cluster unit insights semantically
4. **Strategic Clustering**: Assign macro insights to strategic clusters
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from sqlalchemy.orm import Session

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

    # Dimension assessment settings
    dimension_top_k: int = 10
    dimension_min_similarity: float = 0.3

    # Macro insight discovery settings
    macro_similarity_threshold: float = 0.72
    use_llm_naming: bool = True
    generate_macro_descriptions: bool = True

    # Strategic clustering settings
    cluster_similarity_threshold: float = 0.75

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
    transcript_paths: Dict[int, Path],
    config: AnalysisPipelineConfig,
) -> StageResult:
    """
    Run the insight extraction stage.
    """
    import time
    from innovation_intelligence.analysis.insights.insight_extraction import (
        extract_and_persist_insights,
    )

    start = time.time()
    result = StageResult(stage_name="1. Insight Extraction", success=True)

    for source in sources:
        transcript_path = transcript_paths.get(source.id)
        if not transcript_path:
            result.errors.append(f"No transcript for source {source.id}")
            continue

        try:
            extraction, unit_insights = extract_and_persist_insights(
                transcript_path=transcript_path,
                session=session,
                source=source,
                force=config.force_reextract,
                assess_dimensions=False,  # Handled in stage 2
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
        result.items_created = sum(
            (1 if a.adoption else 0) +
            (1 if a.expectation else 0) +
            (1 if a.progress else 0)
            for a in assessments
        )

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
    """
    import time
    from innovation_intelligence.analysis.clustering.clustering import (
        assign_and_persist_macro_insights,
    )

    start = time.time()
    result = StageResult(stage_name="4. Strategic Clustering", success=True)

    try:
        # Count unassigned macro insights before
        unassigned_before = session.query(MacroInsight).filter(
            MacroInsight.cluster_id.is_(None)
        ).count()

        result.items_processed = unassigned_before

        # Run clustering
        clustering_result = assign_and_persist_macro_insights(
            session,
            similarity_threshold=config.cluster_similarity_threshold,
        )

        result.items_created = clustering_result.total_processed

        result.details["clusters_used"] = clustering_result.clusters_used
        result.details["assigned_to_other"] = clustering_result.total_assigned_to_other

        log.info(
            f"[PIPELINE] Assigned {clustering_result.total_processed} macro insights "
            f"to {len(clustering_result.clusters_used)} clusters"
        )

    except Exception as e:
        error_msg = f"Strategic clustering failed: {e}"
        log.error(f"[PIPELINE] {error_msg}")
        result.errors.append(error_msg)
        result.success = False

    result.duration_seconds = time.time() - start

    return result


# ---------------------------------------------------------------------
# Source Discovery (FIXED)
# ---------------------------------------------------------------------
def _discover_sources(
    session: Session,
    transcripts_dir: Optional[Path] = None,
    podcast_transcripts_dir: Optional[Path] = None,
    document_transcripts_dir: Optional[Path] = None,
) -> tuple[List[SourceEntity], Dict[int, Path]]:
    """
    Discover sources and their transcript paths from the database.

    Uses the transcript_path stored in the database, not file discovery.
    """
    from innovation_intelligence.config import settings

    sources: List[SourceEntity] = []
    transcript_paths: Dict[int, Path] = {}

    # Discover podcast episodes with transcripts
    episodes = session.query(PodcastEpisode).filter(
        PodcastEpisode.transcript_path.isnot(None)
    ).all()

    for episode in episodes:
        transcript_file = Path(episode.transcript_path)
        if transcript_file.exists():
            sources.append(episode)
            transcript_paths[episode.id] = transcript_file
        else:
            log.warning(f"[PIPELINE] Transcript file not found for episode {episode.id}: {transcript_file}")

    podcast_count = len([s for s in sources if isinstance(s, PodcastEpisode)])
    log.info(f"[PIPELINE] Found {podcast_count} podcast episodes with transcripts")

    # Discover documents with transcripts
    documents = session.query(Document).filter(
        Document.transcript_path.isnot(None)
    ).all()

    for doc in documents:
        transcript_file = Path(doc.transcript_path)
        if transcript_file.exists():
            sources.append(doc)
            transcript_paths[doc.id] = transcript_file
        else:
            log.warning(f"[PIPELINE] Transcript file not found for document {doc.id}: {transcript_file}")

    document_count = len([s for s in sources if isinstance(s, Document)])
    log.info(f"[PIPELINE] Found {document_count} documents with transcripts")

    return sources, transcript_paths


# ---------------------------------------------------------------------
# Main Pipeline Entry Points
# ---------------------------------------------------------------------
def run_full_analysis(
    session: Session,
    transcripts_dir: Optional[Path] = None,
    *,
    config: Optional[AnalysisPipelineConfig] = None,
    sources: Optional[List[SourceEntity]] = None,
    transcript_paths: Optional[Dict[int, Path]] = None,
) -> PipelineResult:
    """
    Run the complete analysis pipeline.

    Stages:
    1. Insight Extraction - Extract unit insights from transcripts
    2. Dimension Assessment - Assess adoption/expectation/progress via RAG
    3. Macro Insight Discovery - Cluster unit insights semantically
    4. Strategic Clustering - Assign macro insights to strategic clusters
    """
    config = config or AnalysisPipelineConfig()
    result = PipelineResult()

    log.info("=" * 60)
    log.info("[PIPELINE] Starting full analysis pipeline")
    log.info("=" * 60)

    # Discover sources if not provided
    if sources is None or transcript_paths is None:
        sources, transcript_paths = _discover_sources(session, transcripts_dir)

    if not sources:
        log.warning("[PIPELINE] No sources found to process")
        result.complete()
        return result

    log.info(f"[PIPELINE] Processing {len(sources)} sources")

    # # Stage 1: Insight Extraction
    # if config.run_extraction:
    #     log.info("-" * 60)
    #     log.info("[PIPELINE] Stage 1: Insight Extraction")
    #     stage_result = _run_extraction_stage(
    #         session, sources, transcript_paths, config
    #     )
    #     result.add_stage(stage_result)

    #     if not stage_result.success and not config.continue_on_error:
    #         result.complete()
    #         return result

    # Stage 2: Macro Insight Discovery
    if config.run_macro_discovery:
        log.info("-" * 60)
        log.info("[PIPELINE] Stage 3: Macro Insight Discovery")
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

    # # Stage 4: Dimension Assessment
    # if config.run_dimension_assessment:
    #     log.info("-" * 60)
    #     log.info("[PIPELINE] Stage 4: Dimension Assessment")
    #     stage_result = _run_dimension_assessment_stage(session, config)
    #     result.add_stage(stage_result)

    #     if not stage_result.success and not config.continue_on_error:
    #         result.complete()
    #         return result

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

    return run_full_analysis(session, config=config, sources=[], transcript_paths={})


def run_clustering_only(
    session: Session,
    *,
    macro_similarity_threshold: float = 0.72,
    cluster_similarity_threshold: float = 0.75,
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

    return run_full_analysis(session, config=config, sources=[], transcript_paths={})


# ---------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------
def get_pipeline_statistics(session: Session) -> Dict[str, Any]:
    """
    Get current statistics for all pipeline entities.
    """
    from sqlalchemy import func
    from innovation_intelligence.db.models import InsightDimension

    stats = {
        "unit_insights": {
            "total": session.query(UnitInsight).count(),
            "with_macro": session.query(UnitInsight).filter(
                UnitInsight.macro_insight_id.isnot(None)
            ).count(),
            "without_macro": session.query(UnitInsight).filter(
                UnitInsight.macro_insight_id.is_(None)
            ).count(),
        },
        "dimensions": {
            "total": session.query(InsightDimension).count(),
            "by_type": dict(
                session.query(
                    InsightDimension.dimension_type,
                    func.count(InsightDimension.id)
                ).group_by(InsightDimension.dimension_type).all()
            ),
        },
        "macro_insights": {
            "total": session.query(MacroInsight).count(),
            "with_cluster": session.query(MacroInsight).filter(
                MacroInsight.cluster_id.isnot(None)
            ).count(),
            "without_cluster": session.query(MacroInsight).filter(
                MacroInsight.cluster_id.is_(None)
            ).count(),
        },
        "clusters": {
            "total": session.query(Cluster).count(),
            "by_name": dict(
                session.query(Cluster.name, func.count(MacroInsight.id))
                .outerjoin(MacroInsight)
                .group_by(Cluster.name)
                .all()
            ),
        },
        "sources": {
            "podcast_episodes": session.query(PodcastEpisode).count(),
            "documents": session.query(Document).count(),
        },
    }

    return stats
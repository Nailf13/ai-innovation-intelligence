# src/innovation_intelligence/analysis/dimensions/assessment_service.py
"""
Dimension assessment service for insights.

This module provides:
- Full dimension assessment pipeline (RAG retrieval + LLM assessment)
- Batch processing of multiple insights
- Database persistence of results
- Integration with the insight extraction pipeline
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from innovation_intelligence.analysis.dimensions.rag_engine import (
    RAGEngine,
    RAGEngineConfig,
    get_rag_engine,
)
from innovation_intelligence.db.models import (
    UnitInsight,
    InsightDimension,
    DimensionEvidence,
)
from innovation_intelligence.llm.bedrock_client import BedrockClient
from innovation_intelligence.llm.tools.dimension_tool import (
    DimensionType,
    DimensionResult,
    DimensionEvidence as DimEvidence,
    assess_dimension,
)
from innovation_intelligence.config import settings
from innovation_intelligence.logger import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------
@dataclass
class InsightDimensions:
    """Complete dimension assessment for an insight."""
    insight_id: int
    insight_name: str
    adoption: Optional[DimensionResult] = None
    expectation: Optional[DimensionResult] = None
    progress: Optional[DimensionResult] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        result = {
            "insight_id": self.insight_id,
            "insight_name": self.insight_name,
            "dimensions": {},
        }

        if self.adoption:
            result["dimensions"]["adoption"] = {
                "stage": self.adoption.value,
                "confidence": self.adoption.confidence,
                "evidence": [
                    {
                        "text": e.text,
                        "source": e.source,
                        "speaker": e.speaker,
                        "page": e.page,
                        "start": e.start_time,
                        "end": e.end_time,
                    }
                    for e in self.adoption.evidence
                ],
            }

        if self.expectation:
            result["dimensions"]["expectation"] = {
                "level": self.expectation.value,
                "confidence": self.expectation.confidence,
                "evidence": [
                    {
                        "text": e.text,
                        "source": e.source,
                        "speaker": e.speaker,
                        "page": e.page,
                        "start": e.start_time,
                        "end": e.end_time,
                    }
                    for e in self.expectation.evidence
                ],
            }

        if self.progress:
            result["dimensions"]["progress"] = {
                "horizon": self.progress.value,
                "confidence": self.progress.confidence,
                "evidence": [
                    {
                        "text": e.text,
                        "source": e.source,
                        "speaker": e.speaker,
                        "page": e.page,
                        "start": e.start_time,
                        "end": e.end_time,
                    }
                    for e in self.progress.evidence
                ],
            }

        return result


@dataclass
class AssessmentConfig:
    """Configuration for dimension assessment."""
    # Which dimensions to assess
    assess_adoption: bool = True
    assess_expectation: bool = True
    assess_progress: bool = True

    # RAG configuration
    rag_config: RAGEngineConfig = field(default_factory=RAGEngineConfig)

    # Whether to persist results to database
    persist_results: bool = True

    # Whether to skip insights that already have dimensions
    skip_existing: bool = True

    # Analysis period label
    period: Optional[str] = None


# ---------------------------------------------------------------------
# Assessment service
# ---------------------------------------------------------------------
class DimensionAssessmentService:
    """
    Service for assessing dimensions of extracted insights.

    Orchestrates:
    1. RAG retrieval of relevant context
    2. LLM-based dimension assessment
    3. Database persistence of results
    """

    def __init__(
        self,
        session: Session,
        config: Optional[AssessmentConfig] = None,
        bedrock_client: Optional[BedrockClient] = None,
    ):
        """
        Initialize the assessment service.

        Args:
            session: SQLAlchemy database session
            config: Optional assessment configuration
            bedrock_client: Optional pre-configured BedrockClient
        """
        self.session = session
        self.config = config or AssessmentConfig()
        self.rag_engine = get_rag_engine(session, self.config.rag_config)
        self.bedrock_client = bedrock_client or BedrockClient(
            model_id=settings.aws.bedrock_model_id,
            region=settings.aws.region,
            profile=settings.aws.profile,
        )

        # Log RAG stats on init
        rag_stats = self.rag_engine.get_stats()
        log.info(f"[ASSESS] RAG engine initialized: "
                 f"{rag_stats['podcast_chunks']} podcast chunks, "
                 f"{rag_stats['document_chunks']} document chunks")

    def assess_insight(
        self,
        insight: UnitInsight,
        *,
        dimensions: Optional[List[DimensionType]] = None,
        source_filter: Optional[str] = None,
    ) -> InsightDimensions:
        """
        Assess all configured dimensions for a single insight.

        Args:
            insight: The UnitInsight to assess
            dimensions: Override which dimensions to assess
            source_filter: Optional filter to specific source

        Returns:
            InsightDimensions with assessment results
        """
        log.info(f"[ASSESS] Processing insight: {insight.name[:60]}...")

        # Determine which dimensions to assess
        dims_to_assess = dimensions or self._get_dimensions_to_assess(insight)

        if not dims_to_assess:
            log.warning(f"[ASSESS] No dimensions to assess for insight {insight.id} (already assessed or config excludes all)")

        log.info(f"[ASSESS] Will assess {len(dims_to_assess)} dimensions: {[d.value for d in dims_to_assess]}")

        result = InsightDimensions(
            insight_id=insight.id,
            insight_name=insight.name,
        )

        for dim in dims_to_assess:
            try:
                dim_result = self._assess_single_dimension(
                    insight=insight,
                    dimension=dim,
                    source_filter=source_filter,
                )

                if dim == DimensionType.ADOPTION:
                    result.adoption = dim_result
                elif dim == DimensionType.EXPECTATION:
                    result.expectation = dim_result
                elif dim == DimensionType.PROGRESS:
                    result.progress = dim_result

            except Exception as e:
                log.error(f"[ASSESS] Failed to assess {dim.value} for {insight.name}: {e}")

        # Persist if configured
        if self.config.persist_results:
            self._persist_dimensions(insight, result)

        return result

    def assess_insights_batch(
        self,
        insights: List[UnitInsight],
        *,
        source_filter: Optional[str] = None,
    ) -> List[InsightDimensions]:
        """
        Assess dimensions for multiple insights.

        Args:
            insights: List of UnitInsight objects to assess
            source_filter: Optional filter to specific source

        Returns:
            List of InsightDimensions results
        """
        total = len(insights)
        log.info(f"[ASSESS] Batch processing {total} insights")

        results: List[InsightDimensions] = []

        for i, insight in enumerate(insights, 1):
            log.info(f"[ASSESS] Progress: {i}/{total}")

            try:
                result = self.assess_insight(
                    insight=insight,
                    source_filter=source_filter,
                )
                results.append(result)

            except Exception as e:
                log.error(f"[ASSESS] Failed to process insight {insight.id}: {e}")
                # Add empty result to maintain order
                results.append(InsightDimensions(
                    insight_id=insight.id,
                    insight_name=insight.name,
                ))

        log.info(f"[ASSESS] Completed {len(results)}/{total} insights")
        return results

    def assess_new_insights(
        self,
        source_filter: Optional[str] = None,
    ) -> List[InsightDimensions]:
        """
        Assess dimensions for all insights that don't have them yet.

        Args:
            source_filter: Optional filter to specific source

        Returns:
            List of InsightDimensions for newly assessed insights
        """
        # Query insights without dimensions
        query = self.session.query(UnitInsight)

        if self.config.skip_existing:
            # Left join to find insights without dimensions
            query = query.outerjoin(InsightDimension).filter(
                InsightDimension.id.is_(None)
            )

        insights = query.all()
        log.info(f"[ASSESS] Found {len(insights)} insights needing assessment")

        if not insights:
            return []

        return self.assess_insights_batch(insights, source_filter=source_filter)

    def _get_dimensions_to_assess(
        self,
        insight: UnitInsight,
    ) -> List[DimensionType]:
        """
        Determine which dimensions need assessment for an insight.

        Args:
            insight: The insight to check

        Returns:
            List of dimensions to assess
        """
        dims: List[DimensionType] = []

        # Check existing dimensions if skip_existing is enabled
        existing_types = set()
        if self.config.skip_existing and insight.dimensions:
            existing_types = {d.dimension_type for d in insight.dimensions}
            log.debug(f"[ASSESS] Existing dimensions for insight {insight.id}: {existing_types}")

        log.debug(f"[ASSESS] Config: adoption={self.config.assess_adoption}, "
                  f"expectation={self.config.assess_expectation}, "
                  f"progress={self.config.assess_progress}, "
                  f"skip_existing={self.config.skip_existing}")

        if self.config.assess_adoption and "adoption" not in existing_types:
            dims.append(DimensionType.ADOPTION)

        if self.config.assess_expectation and "expectation" not in existing_types:
            dims.append(DimensionType.EXPECTATION)

        if self.config.assess_progress and "progress" not in existing_types:
            dims.append(DimensionType.PROGRESS)

        return dims

    def _assess_single_dimension(
        self,
        insight: UnitInsight,
        dimension: DimensionType,
        source_filter: Optional[str] = None,
    ) -> DimensionResult:
        """
        Assess a single dimension for an insight.

        Args:
            insight: The insight to assess
            dimension: Which dimension to assess
            source_filter: Optional source filter

        Returns:
            DimensionResult with value and evidence
        """
        log.info(f"[ASSESS] Retrieving context for {dimension.value}...")

        # Reuse the precomputed embedding from UnitInsight if available
        # This avoids redundant embedding computation
        precomputed_embedding = None
        if insight.embedding:
            # UnitInsight.embedding is stored as JSONB (list)
            precomputed_embedding = insight.embedding

        # Retrieve context via RAG
        rag_context = self.rag_engine.retrieve_for_insight(
            insight_name=insight.name,
            insight_description=insight.description,
            dimension=dimension,
            source_filter=source_filter,
            precomputed_embedding=precomputed_embedding,
        )

        log.info(
            f"[ASSESS] Retrieved {len(rag_context.chunks)} chunks "
            f"({rag_context.total_chars} chars) from {len(rag_context.sources)} sources"
        )

        # Assess dimension via LLM
        result = assess_dimension(
            insight_name=insight.name,
            insight_description=insight.description,
            dimension=dimension,
            context=rag_context.formatted_text,
            sources=rag_context.sources,
            client=self.bedrock_client,
            period=self.config.period,
        )

        return result

    def _persist_dimensions(
        self,
        insight: UnitInsight,
        dimensions: InsightDimensions,
    ) -> None:
        """
        Persist dimension results to database.

        Args:
            insight: The assessed insight
            dimensions: Assessment results to persist
        """
        persisted = 0

        for dim_result, dim_type in [
            (dimensions.adoption, DimensionType.ADOPTION),
            (dimensions.expectation, DimensionType.EXPECTATION),
            (dimensions.progress, DimensionType.PROGRESS),
        ]:
            if dim_result is None:
                continue

            # Create InsightDimension record
            db_dim = InsightDimension(
                unit_insight_id=insight.id,
                dimension_type=dim_type.value,
                value=dim_result.value,
                confidence=dim_result.confidence,
            )
            self.session.add(db_dim)
            self.session.flush()  # Get ID

            # Create evidence records
            for ev in dim_result.evidence:
                # Build source reference
                source_parts = [ev.source]
                if ev.speaker:
                    source_parts.append(ev.speaker)
                if ev.page is not None:
                    source_parts.append(f"p{ev.page}")
                if ev.start_time is not None:
                    source_parts.append(f"{ev.start_time:.1f}s")

                db_evidence = DimensionEvidence(
                    dimension_id=db_dim.id,
                    chunk_text=ev.text,
                    similarity_score=ev.similarity_score,
                    source_ref=" | ".join(source_parts),
                )
                self.session.add(db_evidence)

            persisted += 1

        self.session.commit()
        log.info(f"[ASSESS] Persisted {persisted} dimensions for insight {insight.id}")


# ---------------------------------------------------------------------
# Convenience functions
# ---------------------------------------------------------------------
def assess_insight_dimensions(
    session: Session,
    insight: UnitInsight,
    *,
    config: Optional[AssessmentConfig] = None,
) -> InsightDimensions:
    """
    Convenience function to assess all dimensions for a single insight.

    Args:
        session: Database session
        insight: The insight to assess
        config: Optional configuration

    Returns:
        InsightDimensions with results
    """
    service = DimensionAssessmentService(session, config)
    return service.assess_insight(insight)


def assess_batch_dimensions(
    session: Session,
    insights: List[UnitInsight],
    *,
    config: Optional[AssessmentConfig] = None,
) -> List[InsightDimensions]:
    """
    Convenience function to assess dimensions for multiple insights.

    Args:
        session: Database session
        insights: List of insights to assess
        config: Optional configuration

    Returns:
        List of InsightDimensions results
    """
    service = DimensionAssessmentService(session, config)
    return service.assess_insights_batch(insights)


def get_assessment_service(
    session: Session,
    config: Optional[AssessmentConfig] = None,
) -> DimensionAssessmentService:
    """
    Factory function for DimensionAssessmentService.

    Args:
        session: Database session
        config: Optional configuration

    Returns:
        Configured service instance
    """
    return DimensionAssessmentService(session, config)

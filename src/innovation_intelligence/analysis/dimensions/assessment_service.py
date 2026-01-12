"""
Dimension assessment service for insights.

This module provides:
- Full dimension assessment pipeline (RAG retrieval + LLM assessment)
- Dispatches to trend or stake assessment based on insight type
- Batch processing of multiple insights
- Database persistence of results
- Integration with the insight extraction pipeline

TRENDS use dimensions: adoption, expectation, progress
HEALTH STAKES use dimensions: criticality, urgency, actionability
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

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
from innovation_intelligence.llm.gemini_client import GeminiClient
from innovation_intelligence.llm.tools.trend_dimension_tool import (
    TrendDimensionType,
    TrendDimensionResult,
    TrendDimensionEvidence,
    assess_trend_dimension,
    TREND_DIMENSION_HINTS,
)
from innovation_intelligence.llm.tools.stake_dimension_tool import (
    StakeDimensionType,
    StakeAssessmentResult,
    StakeEvidence,
    assess_health_stake,
    STAKE_DIMENSION_HINTS,
)
from innovation_intelligence.config import settings
from innovation_intelligence.logger import get_logger

log = get_logger(__name__)

# Type aliases for dimension results (union of both types)
DimensionResultType = Union[TrendDimensionResult, StakeAssessmentResult]


# ---------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------
@dataclass
class TrendDimensions:
    """Dimension assessment for a TREND insight (adoption, expectation, progress)."""
    insight_id: int
    insight_name: str
    adoption: Optional[TrendDimensionResult] = None
    expectation: Optional[TrendDimensionResult] = None
    progress: Optional[TrendDimensionResult] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        result = {
            "insight_id": self.insight_id,
            "insight_name": self.insight_name,
            "insight_type": "trend",
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
class StakeDimensions:
    """Dimension assessment for a HEALTH STAKE (criticality, urgency, actionability)."""
    insight_id: int
    insight_name: str
    criticality: Optional[StakeAssessmentResult] = None
    urgency: Optional[StakeAssessmentResult] = None
    actionability: Optional[StakeAssessmentResult] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        result = {
            "insight_id": self.insight_id,
            "insight_name": self.insight_name,
            "insight_type": "health_stake",
            "dimensions": {},
        }

        if self.criticality:
            result["dimensions"]["criticality"] = {
                "level": self.criticality.value,
                "confidence": self.criticality.confidence,
                "evidence": [
                    {
                        "text": e.text,
                        "source": e.source,
                        "speaker": e.speaker,
                        "page": e.page,
                        "start": e.start_time,
                        "end": e.end_time,
                    }
                    for e in self.criticality.evidence
                ],
            }

        if self.urgency:
            result["dimensions"]["urgency"] = {
                "level": self.urgency.value,
                "confidence": self.urgency.confidence,
                "evidence": [
                    {
                        "text": e.text,
                        "source": e.source,
                        "speaker": e.speaker,
                        "page": e.page,
                        "start": e.start_time,
                        "end": e.end_time,
                    }
                    for e in self.urgency.evidence
                ],
            }

        if self.actionability:
            result["dimensions"]["actionability"] = {
                "level": self.actionability.value,
                "confidence": self.actionability.confidence,
                "evidence": [
                    {
                        "text": e.text,
                        "source": e.source,
                        "speaker": e.speaker,
                        "page": e.page,
                        "start": e.start_time,
                        "end": e.end_time,
                    }
                    for e in self.actionability.evidence
                ],
            }

        return result


# Union type for all insight dimensions
InsightDimensions = Union[TrendDimensions, StakeDimensions]


@dataclass
class AssessmentConfig:
    """Configuration for dimension assessment."""
    # Trend dimensions (only used for trends)
    assess_adoption: bool = True
    assess_expectation: bool = True
    assess_progress: bool = True

    # Stake dimensions (only used for health stakes)
    assess_criticality: bool = True
    assess_urgency: bool = True
    assess_actionability: bool = True

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

    Dispatches to appropriate assessment tool based on insight type:
    - TRENDS: assessed on adoption, expectation, progress
    - HEALTH STAKES: assessed on criticality, urgency, actionability

    Orchestrates:
    1. RAG retrieval of relevant context
    2. LLM-based dimension assessment (trend or stake)
    3. Database persistence of results
    """

    def __init__(
        self,
        session: Session,
        config: Optional[AssessmentConfig] = None,
        gemini_client: Optional[GeminiClient] = None,
    ):
        """
        Initialize the assessment service.

        Args:
            session: SQLAlchemy database session
            config: Optional assessment configuration
            gemini_client: Optional pre-configured GeminiClient
        """
        self.session = session
        self.config = config or AssessmentConfig()
        self.rag_engine = get_rag_engine(session, self.config.rag_config)
        self.gemini_client = gemini_client or GeminiClient(
            project=settings.gcp.project_id,
            location=settings.gcp.location,
            model_id=settings.gcp.gemini_model_id,
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
        source_filter: Optional[str] = None,
    ) -> InsightDimensions:
        """
        Assess all configured dimensions for a single insight.

        Dispatches to trend or stake assessment based on insight.type.

        Args:
            insight: The UnitInsight to assess
            source_filter: Optional filter to specific source

        Returns:
            TrendDimensions or StakeDimensions based on insight type
        """
        log.info(f"[ASSESS] Processing insight: {insight.name[:60]}... (type={insight.type})")

        if insight.type == "trend":
            return self._assess_trend(insight, source_filter)
        elif insight.type == "health_stake":
            return self._assess_stake(insight, source_filter)
        else:
            log.warning(f"[ASSESS] Unknown insight type '{insight.type}', defaulting to trend assessment")
            return self._assess_trend(insight, source_filter)

    def _assess_trend(
        self,
        insight: UnitInsight,
        source_filter: Optional[str] = None,
    ) -> TrendDimensions:
        """Assess a TREND insight on adoption, expectation, progress."""
        dims_to_assess = self._get_trend_dimensions_to_assess(insight)

        if not dims_to_assess:
            log.warning(f"[ASSESS] No trend dimensions to assess for insight {insight.id}")

        log.info(f"[ASSESS] Will assess {len(dims_to_assess)} trend dimensions: {[d.value for d in dims_to_assess]}")

        result = TrendDimensions(
            insight_id=insight.id,
            insight_name=insight.name,
        )

        for dim in dims_to_assess:
            try:
                dim_result = self._assess_single_trend_dimension(
                    insight=insight,
                    dimension=dim,
                    source_filter=source_filter,
                )

                if dim == TrendDimensionType.ADOPTION:
                    result.adoption = dim_result
                elif dim == TrendDimensionType.EXPECTATION:
                    result.expectation = dim_result
                elif dim == TrendDimensionType.PROGRESS:
                    result.progress = dim_result

            except Exception as e:
                log.error(f"[ASSESS] Failed to assess trend {dim.value} for {insight.name}: {e}")

        # Persist if configured
        if self.config.persist_results:
            self._persist_trend_dimensions(insight, result)

        return result

    def _assess_stake(
        self,
        insight: UnitInsight,
        source_filter: Optional[str] = None,
    ) -> StakeDimensions:
        """Assess a HEALTH STAKE insight on criticality, urgency, actionability."""
        dims_to_assess = self._get_stake_dimensions_to_assess(insight)

        if not dims_to_assess:
            log.warning(f"[ASSESS] No stake dimensions to assess for insight {insight.id}")

        log.info(f"[ASSESS] Will assess {len(dims_to_assess)} stake dimensions: {[d.value for d in dims_to_assess]}")

        result = StakeDimensions(
            insight_id=insight.id,
            insight_name=insight.name,
        )

        for dim in dims_to_assess:
            try:
                dim_result = self._assess_single_stake_dimension(
                    insight=insight,
                    dimension=dim,
                    source_filter=source_filter,
                )

                if dim == StakeDimensionType.CRITICALITY:
                    result.criticality = dim_result
                elif dim == StakeDimensionType.URGENCY:
                    result.urgency = dim_result
                elif dim == StakeDimensionType.ACTIONABILITY:
                    result.actionability = dim_result

            except Exception as e:
                log.error(f"[ASSESS] Failed to assess stake {dim.value} for {insight.name}: {e}")

        # Persist if configured
        if self.config.persist_results:
            self._persist_stake_dimensions(insight, result)

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
            List of TrendDimensions or StakeDimensions results
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
                # Add empty result to maintain order based on type
                if insight.type == "health_stake":
                    results.append(StakeDimensions(
                        insight_id=insight.id,
                        insight_name=insight.name,
                    ))
                else:
                    results.append(TrendDimensions(
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

    def _get_trend_dimensions_to_assess(
        self,
        insight: UnitInsight,
    ) -> List[TrendDimensionType]:
        """Determine which TREND dimensions need assessment."""
        dims: List[TrendDimensionType] = []

        # Check existing dimensions if skip_existing is enabled
        existing_types = set()
        if self.config.skip_existing and insight.dimensions:
            existing_types = {d.dimension_type for d in insight.dimensions}
            log.debug(f"[ASSESS] Existing dimensions for insight {insight.id}: {existing_types}")

        if self.config.assess_adoption and "adoption" not in existing_types:
            dims.append(TrendDimensionType.ADOPTION)

        if self.config.assess_expectation and "expectation" not in existing_types:
            dims.append(TrendDimensionType.EXPECTATION)

        if self.config.assess_progress and "progress" not in existing_types:
            dims.append(TrendDimensionType.PROGRESS)

        return dims

    def _get_stake_dimensions_to_assess(
        self,
        insight: UnitInsight,
    ) -> List[StakeDimensionType]:
        """Determine which STAKE dimensions need assessment."""
        dims: List[StakeDimensionType] = []

        # Check existing dimensions if skip_existing is enabled
        existing_types = set()
        if self.config.skip_existing and insight.dimensions:
            existing_types = {d.dimension_type for d in insight.dimensions}
            log.debug(f"[ASSESS] Existing dimensions for insight {insight.id}: {existing_types}")

        if self.config.assess_criticality and "criticality" not in existing_types:
            dims.append(StakeDimensionType.CRITICALITY)

        if self.config.assess_urgency and "urgency" not in existing_types:
            dims.append(StakeDimensionType.URGENCY)

        if self.config.assess_actionability and "actionability" not in existing_types:
            dims.append(StakeDimensionType.ACTIONABILITY)

        return dims

    def _assess_single_trend_dimension(
        self,
        insight: UnitInsight,
        dimension: TrendDimensionType,
        source_filter: Optional[str] = None,
    ) -> TrendDimensionResult:
        """Assess a single TREND dimension."""
        log.info(f"[ASSESS] Retrieving context for trend {dimension.value}...")

        # Reuse the precomputed embedding from UnitInsight if available
        precomputed_embedding = insight.embedding if insight.embedding else None

        # Retrieve context via RAG with trend dimension hints
        rag_context = self.rag_engine.retrieve_for_trend(
            trend_name=insight.name,
            trend_description=insight.description,
            dimension=dimension,
            source_filter=source_filter,
            precomputed_embedding=precomputed_embedding,
        )

        log.info(
            f"[ASSESS] Retrieved {len(rag_context.chunks)} chunks "
            f"({rag_context.total_chars} chars) from {len(rag_context.sources)} sources"
        )

        # Assess dimension via LLM
        result = assess_trend_dimension(
            trend_name=insight.name,
            trend_description=insight.description,
            dimension=dimension,
            context=rag_context.formatted_text,
            sources=rag_context.sources,
            client=self.gemini_client,
            period=self.config.period,
        )

        return result

    def _assess_single_stake_dimension(
        self,
        insight: UnitInsight,
        dimension: StakeDimensionType,
        source_filter: Optional[str] = None,
    ) -> StakeAssessmentResult:
        """Assess a single STAKE dimension."""
        log.info(f"[ASSESS] Retrieving context for stake {dimension.value}...")

        # Reuse the precomputed embedding from UnitInsight if available
        precomputed_embedding = insight.embedding if insight.embedding else None

        # Retrieve context via RAG with stake dimension hints
        rag_context = self.rag_engine.retrieve_for_stake(
            stake_name=insight.name,
            stake_description=insight.description,
            dimension=dimension,
            source_filter=source_filter,
            precomputed_embedding=precomputed_embedding,
        )

        log.info(
            f"[ASSESS] Retrieved {len(rag_context.chunks)} chunks "
            f"({rag_context.total_chars} chars) from {len(rag_context.sources)} sources"
        )

        # Assess dimension via LLM
        result = assess_health_stake(
            stake_name=insight.name,
            stake_description=insight.description,
            dimension=dimension,
            context=rag_context.formatted_text,
            sources=rag_context.sources,
            client=self.gemini_client,
            period=self.config.period,
        )

        return result

    def _persist_trend_dimensions(
        self,
        insight: UnitInsight,
        dimensions: TrendDimensions,
    ) -> None:
        """Persist TREND dimension results to database."""
        persisted = 0

        for dim_result, dim_type in [
            (dimensions.adoption, TrendDimensionType.ADOPTION),
            (dimensions.expectation, TrendDimensionType.EXPECTATION),
            (dimensions.progress, TrendDimensionType.PROGRESS),
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
                source_parts = [ev.source]
                if ev.speaker:
                    source_parts.append(ev.speaker)
                if ev.page is not None:
                    source_parts.append(f"p{ev.page}")
                if ev.start_time is not None:
                    source_parts.append(f"{ev.start_time:.1f}s")

                # Build metadata dict from evidence
                chunk_metadata = {}
                if ev.start_time is not None:
                    chunk_metadata["start_time"] = ev.start_time
                if ev.end_time is not None:
                    chunk_metadata["end_time"] = ev.end_time
                if ev.page is not None:
                    chunk_metadata["page"] = ev.page

                db_evidence = DimensionEvidence(
                    dimension_id=db_dim.id,
                    chunk_text=ev.text,
                    similarity_score=ev.similarity_score,
                    source_ref=" | ".join(source_parts),
                    chunk_metadata=chunk_metadata if chunk_metadata else None,
                )
                self.session.add(db_evidence)

            persisted += 1

        self.session.commit()
        log.info(f"[ASSESS] Persisted {persisted} trend dimensions for insight {insight.id}")

    def _persist_stake_dimensions(
        self,
        insight: UnitInsight,
        dimensions: StakeDimensions,
    ) -> None:
        """Persist STAKE dimension results to database."""
        persisted = 0

        for dim_result, dim_type in [
            (dimensions.criticality, StakeDimensionType.CRITICALITY),
            (dimensions.urgency, StakeDimensionType.URGENCY),
            (dimensions.actionability, StakeDimensionType.ACTIONABILITY),
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
                source_parts = [ev.source]
                if ev.speaker:
                    source_parts.append(ev.speaker)
                if ev.page is not None:
                    source_parts.append(f"p{ev.page}")
                if ev.start_time is not None:
                    source_parts.append(f"{ev.start_time:.1f}s")

                # Build metadata dict from evidence
                chunk_metadata = {}
                if ev.start_time is not None:
                    chunk_metadata["start_time"] = ev.start_time
                if ev.end_time is not None:
                    chunk_metadata["end_time"] = ev.end_time
                if ev.page is not None:
                    chunk_metadata["page"] = ev.page

                db_evidence = DimensionEvidence(
                    dimension_id=db_dim.id,
                    chunk_text=ev.text,
                    similarity_score=ev.similarity_score,
                    source_ref=" | ".join(source_parts),
                    chunk_metadata=chunk_metadata if chunk_metadata else None,
                )
                self.session.add(db_evidence)

            persisted += 1

        self.session.commit()
        log.info(f"[ASSESS] Persisted {persisted} stake dimensions for insight {insight.id}")


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
# src/innovation_intelligence/analysis/dimensions/__init__.py
"""
Dimension assessment module for insights.

This module provides RAG-based assessment of dimensions:

TRENDS use:
- Adoption: Implementation stage (Nascent → Established)
- Expectation: Sentiment/outlook level (Low → High)
- Progress: Timeline horizon (Near-term → Long-term)

HEALTH STAKES use:
- Criticality: How strategically/clinically important (Low → High)
- Urgency: How time-pressing (Long-term → Immediate)
- Actionability: How feasible to act (Hard → Highly addressable)

Usage:
    from innovation_intelligence.analysis.dimensions import (
        DimensionAssessmentService,
        assess_insight_dimensions,
        TrendDimensionType,
        StakeDimensionType,
    )

    # Assess a single insight (dispatches based on insight.type)
    result = assess_insight_dimensions(session, insight)

    # Or use the service for batch processing
    service = DimensionAssessmentService(session)
    results = service.assess_insights_batch(insights)
"""
from innovation_intelligence.analysis.dimensions.assessment_service import (
    DimensionAssessmentService,
    AssessmentConfig,
    TrendDimensions,
    StakeDimensions,
    InsightDimensions,
    assess_insight_dimensions,
    assess_batch_dimensions,
    get_assessment_service,
)
from innovation_intelligence.analysis.dimensions.rag_engine import (
    RAGEngine,
    RAGEngineConfig,
    RAGContext,
    get_rag_engine,
)
from innovation_intelligence.llm.tools.trend_dimension_tool import (
    TrendDimensionType,
    TrendDimensionResult,
    TrendDimensionEvidence,
    AdoptionStage,
    ExpectationLevel,
    ProgressHorizon,
)
from innovation_intelligence.llm.tools.stake_dimension_tool import (
    StakeDimensionType,
    StakeAssessmentResult,
    StakeEvidence,
    CriticalityLevel,
    UrgencyLevel,
    ActionabilityLevel,
)

__all__ = [
    # Service
    "DimensionAssessmentService",
    "AssessmentConfig",
    "TrendDimensions",
    "StakeDimensions",
    "InsightDimensions",
    "assess_insight_dimensions",
    "assess_batch_dimensions",
    "get_assessment_service",
    # RAG Engine
    "RAGEngine",
    "RAGEngineConfig",
    "RAGContext",
    "get_rag_engine",
    # Trend Types
    "TrendDimensionType",
    "TrendDimensionResult",
    "TrendDimensionEvidence",
    "AdoptionStage",
    "ExpectationLevel",
    "ProgressHorizon",
    # Stake Types
    "StakeDimensionType",
    "StakeAssessmentResult",
    "StakeEvidence",
    "CriticalityLevel",
    "UrgencyLevel",
    "ActionabilityLevel",
]

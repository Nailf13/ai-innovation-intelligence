# src/innovation_intelligence/analysis/dimensions/__init__.py
"""
Dimension assessment module for insights.

This module provides RAG-based assessment of three dimensions:
- Adoption: Implementation stage (Nascent → Established)
- Expectation: Sentiment/outlook level (Low → High)
- Progress: Timeline horizon (Near-term → Long-term)

Usage:
    from innovation_intelligence.analysis.dimensions import (
        DimensionAssessmentService,
        assess_insight_dimensions,
        DimensionType,
    )

    # Assess a single insight
    result = assess_insight_dimensions(session, insight)

    # Or use the service for batch processing
    service = DimensionAssessmentService(session)
    results = service.assess_insights_batch(insights)
"""
from innovation_intelligence.analysis.dimensions.assessment_service import (
    DimensionAssessmentService,
    AssessmentConfig,
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
from innovation_intelligence.llm.tools.dimension_tool import (
    DimensionType,
    DimensionResult,
    DimensionEvidence,
    AdoptionStage,
    ExpectationLevel,
    ProgressHorizon,
)

__all__ = [
    # Service
    "DimensionAssessmentService",
    "AssessmentConfig",
    "InsightDimensions",
    "assess_insight_dimensions",
    "assess_batch_dimensions",
    "get_assessment_service",
    # RAG Engine
    "RAGEngine",
    "RAGEngineConfig",
    "RAGContext",
    "get_rag_engine",
    # Types
    "DimensionType",
    "DimensionResult",
    "DimensionEvidence",
    "AdoptionStage",
    "ExpectationLevel",
    "ProgressHorizon",
]

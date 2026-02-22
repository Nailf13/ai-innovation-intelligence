"""
Centralized configuration for the analysis pipeline.

Single source of truth for all tunable parameters used across:
- Insight extraction (dedup thresholds)
- Macro insight discovery (similarity, LLM naming)
- Strategic clustering (similarity thresholds)
- Dimension assessment (RAG retrieval params)
- Pipeline processing (workers, batching, error handling)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class SimilarityThresholds:
    """All similarity thresholds in one place."""

    # Macro insight discovery: minimum cosine similarity for clustering unit insights
    macro_discovery: float = 0.72

    # Strategic clustering: minimum similarity for assigning macro/unit insights to clusters
    strategic_clustering: float = 0.5

    # Deduplication: minimum similarity to consider two unit insights as duplicates
    dedup: float = 0.90

    # Minimum unit insights required to form a macro insight
    min_cluster_size: int = 2


@dataclass(frozen=True)
class RAGRetrievalConfig:
    """RAG retrieval parameters for dimension assessment."""

    # Number of chunks to retrieve per query
    top_k: int = 3

    # Minimum similarity for retrieved chunks
    min_similarity: float = 0.4

    # Maximum total context length in characters
    max_context_chars: int = 200_000

    # Whether to include podcast chunks
    include_podcasts: bool = True

    # Whether to include document chunks
    include_documents: bool = True

    # Whether to deduplicate overlapping content
    deduplicate: bool = True

    # Deduplication similarity threshold for RAG chunks
    chunk_dedup_threshold: float = 0.9


@dataclass(frozen=True)
class LLMNamingConfig:
    """LLM parameters for macro insight naming and description."""

    # Whether to use LLM for macro insight naming
    use_llm_naming: bool = True

    # Whether to generate descriptions for macro insights
    generate_descriptions: bool = False

    # Label generation parameters
    label_max_tokens: int = 120
    label_temperature: float = 0.2

    # Description generation parameters
    description_max_tokens: int = 200
    description_temperature: float = 0.3


@dataclass(frozen=True)
class ProcessingConfig:
    """Pipeline processing parameters."""

    batch_size: int = 10
    continue_on_error: bool = True
    max_workers: int = 3


@dataclass
class AnalysisConfig:
    """
    Single source of truth for all analysis pipeline parameters.

    Usage:
        from innovation_intelligence.analysis.analysis_config import get_analysis_config
        config = get_analysis_config()
        threshold = config.thresholds.macro_discovery
    """

    thresholds: SimilarityThresholds = field(default_factory=SimilarityThresholds)
    rag: RAGRetrievalConfig = field(default_factory=RAGRetrievalConfig)
    llm_naming: LLMNamingConfig = field(default_factory=LLMNamingConfig)
    processing: ProcessingConfig = field(default_factory=ProcessingConfig)

    # Stage toggles
    run_extraction: bool = True
    run_dimension_assessment: bool = True
    run_macro_discovery: bool = True
    run_strategic_clustering: bool = True

    # Extraction settings
    force_reextract: bool = False


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
_default_config: Optional[AnalysisConfig] = None


def get_analysis_config() -> AnalysisConfig:
    """Get the default analysis config singleton."""
    global _default_config
    if _default_config is None:
        _default_config = AnalysisConfig()
    return _default_config

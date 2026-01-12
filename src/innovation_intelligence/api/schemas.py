# src/innovation_intelligence/api/schemas.py
"""
Pydantic schemas for API request/response models.
"""
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------
class InsightType(str, Enum):
    TREND = "trend"
    HEALTH_STAKE = "health_stake"


class TrendDimensionType(str, Enum):
    """Dimension types for TRENDS."""
    ADOPTION = "adoption"
    EXPECTATION = "expectation"
    PROGRESS = "progress"


class StakeDimensionType(str, Enum):
    """Dimension types for HEALTH STAKES."""
    CRITICALITY = "criticality"
    URGENCY = "urgency"
    ACTIONABILITY = "actionability"


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


# ---------------------------------------------------------------------
# Podcast Schemas
# ---------------------------------------------------------------------
class PodcastSearchResult(BaseModel):
    feed_id: int
    title: str
    author: Optional[str] = None
    description: Optional[str] = None
    image_url: Optional[str] = None
    url: Optional[str] = None
    episode_count: Optional[int] = None


class PodcastEpisodeInfo(BaseModel):
    id: int
    title: str
    description: Optional[str] = None
    date_published: Optional[datetime] = None
    duration: Optional[int] = None
    audio_url: str
    image_url: Optional[str] = None


class PodcastSearchRequest(BaseModel):
    query: str = Field(..., min_length=2)


class PodcastSearchResponse(BaseModel):
    results: List[PodcastSearchResult]
    count: int


class EpisodeListRequest(BaseModel):
    feed_id: int
    limit: int = Field(default=10, ge=1, le=100)


class EpisodeListResponse(BaseModel):
    feed_id: int
    episodes: List[PodcastEpisodeInfo]
    count: int
    has_more: bool = False
    next_offset: Optional[int] = None  # Offset for next page


class EpisodeSelectRequest(BaseModel):
    feed_id: int
    episode_ids: List[int] = Field(..., min_length=1)
    podcast_name: str


class PodcastEpisodeDB(BaseModel):
    id: int
    podcast_name: str
    episode_title: str
    gcs_audio_uri: Optional[str] = None
    audio_url: Optional[str] = None
    gcs_transcript_uri: Optional[str] = None
    episode_date: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------
# Document Schemas (GCS-first mode)
# ---------------------------------------------------------------------
class DocumentUploadResponse(BaseModel):
    id: int
    title: str
    gcs_document_uri: str
    gcs_transcript_uri: Optional[str] = None
    source_type: Optional[str] = None
    created_at: datetime


class DocumentDB(BaseModel):
    id: int
    title: str
    source_type: Optional[str] = None
    gcs_document_uri: str
    gcs_transcript_uri: Optional[str] = None
    document_date: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True


class DocumentListResponse(BaseModel):
    documents: List[DocumentDB]
    count: int


# ---------------------------------------------------------------------
# Ingestion Schemas
# ---------------------------------------------------------------------
class TranscriptionRequest(BaseModel):
    podcast_episode_ids: List[int] = Field(default_factory=list)
    document_ids: List[int] = Field(default_factory=list)


class TranscriptionStatus(BaseModel):
    task_id: str
    status: TaskStatus
    progress: float = Field(ge=0, le=1)
    message: Optional[str] = None
    completed_items: int = 0
    total_items: int = 0
    errors: List[str] = Field(default_factory=list)


class IndexingRequest(BaseModel):
    include_podcasts: bool = True
    include_documents: bool = True


class IndexingResponse(BaseModel):
    podcast_chunks_created: int
    document_chunks_created: int
    errors: List[str] = Field(default_factory=list)


class IngestionRequest(BaseModel):
    """Request for vector indexing pipeline."""
    podcasts_only: bool = False
    documents_only: bool = False


class IngestionStatusResponse(BaseModel):
    """Response for ingestion task status."""
    task_id: str
    task_type: str
    status: TaskStatus
    entity_id: Optional[int] = None
    error: Optional[str] = None
    result: Optional[Dict[str, Any]] = None


class ProcessingStatusResponse(BaseModel):
    """Status response for unified processing tasks (download → transcribe → index)."""
    task_id: str
    task_type: str  # "podcast_processing" or "document_processing"
    status: TaskStatus
    entity_id: int
    current_stage: Optional[str] = None  # "download", "transcribe", "index", "completed"
    progress: float = Field(default=0.0, ge=0, le=1)
    error: Optional[str] = None
    result: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------
# Analysis Schemas
# ---------------------------------------------------------------------
class AnalysisRequest(BaseModel):
    run_extraction: bool = True
    run_dimension_assessment: bool = True
    run_macro_discovery: bool = True
    run_strategic_clustering: bool = True
    skip_extraction: bool = False
    clustering_only: bool = False
    force_reextract: bool = False
    macro_similarity_threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    cluster_similarity_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    use_llm_naming: bool = True
    generate_macro_descriptions: bool = False
    continue_on_error: bool = True


class StageResultResponse(BaseModel):
    stage_name: str
    success: bool
    items_processed: int
    items_created: int
    duration_seconds: float
    errors: List[str] = Field(default_factory=list)


class AnalysisResponse(BaseModel):
    success: bool
    duration_seconds: float
    stages: List[StageResultResponse]
    total_errors: int


class AnalysisStatusResponse(BaseModel):
    task_id: str
    status: TaskStatus
    current_stage: Optional[str] = None
    progress: float = Field(default=0.0, ge=0, le=1)
    error: Optional[str] = None
    result: Optional[Dict[str, Any]] = None


class PipelineStatsResponse(BaseModel):
    """Statistics from the analysis pipeline."""
    # Pipeline page: Items ready for analysis (NOT analyzed yet)
    podcast_episodes: int = 0  # Ready but not analyzed
    podcast_episodes_total: int = 0
    podcasts_analyzed: int = 0  # NEW: Count of analyzed podcasts

    documents: int = 0  # Ready but not analyzed
    documents_total: int = 0
    documents_analyzed: int = 0  # NEW: Count of analyzed documents

    # Insights page stats
    unit_insights: Dict[str, Any] = Field(default_factory=dict)
    macro_insights: Dict[str, Any] = Field(default_factory=dict)
    clusters: Dict[str, Any] = Field(default_factory=dict)
    dimensions_assessed: int = 0


# ---------------------------------------------------------------------
# Insight Schemas
# ---------------------------------------------------------------------
class EvidenceItem(BaseModel):
    """Evidence item with text and optional metadata."""
    text: str
    source_ref: Optional[str] = None
    similarity_score: Optional[float] = None
    # Podcast-specific metadata
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    # Document-specific metadata
    page: Optional[int] = None
    section: Optional[str] = None


class DimensionResponse(BaseModel):
    id: Optional[int] = None
    dimension_type: str
    value: str
    confidence: Optional[float] = None
    evidence: List[EvidenceItem] = Field(default_factory=list)


class UnitInsightResponse(BaseModel):
    id: int
    name: str
    description: str
    type: str
    source_type: str
    source_id: Optional[int] = None
    macro_insight_id: Optional[int] = None
    dimensions: List[DimensionResponse] = Field(default_factory=list)
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class MacroInsightResponse(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    cluster_id: Optional[int] = None
    unit_insight_count: int = 0
    unit_insights: Optional[List["UnitInsightResponse"]] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ClusterResponse(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    macro_insight_count: int = 0
    macro_insights: Optional[List["MacroInsightResponse"]] = None

    class Config:
        from_attributes = True


class InsightsListResponse(BaseModel):
    unit_insights: List[UnitInsightResponse]
    total: int
    page: int
    page_size: int


class MacroInsightsListResponse(BaseModel):
    macro_insights: List[MacroInsightResponse]
    total: int


class ClustersListResponse(BaseModel):
    clusters: List[ClusterResponse]
    total: int


class UnitInsightListResponse(BaseModel):
    """Response for listing unit insights."""
    insights: List[UnitInsightResponse]
    count: int


class MacroInsightListResponse(BaseModel):
    """Response for listing macro insights."""
    insights: List[MacroInsightResponse]
    count: int


class ClusterListResponse(BaseModel):
    """Response for listing clusters."""
    clusters: List[ClusterResponse]
    count: int


class SearchResultResponse(BaseModel):
    """Response for search results."""
    query: str
    results: List[Dict[str, Any]]
    count: int


# ---------------------------------------------------------------------
# Statistics Schemas
# ---------------------------------------------------------------------
class PipelineStatistics(BaseModel):
    sources: Dict[str, int]
    unit_insights: Dict[str, int]
    dimensions: Dict[str, Any]
    macro_insights: Dict[str, int]
    clusters: Dict[str, Any]


# ---------------------------------------------------------------------
# Search Schemas
# ---------------------------------------------------------------------
class SearchRequest(BaseModel):
    query: str = Field(..., min_length=2)
    top_k: int = Field(default=3, ge=1, le=100)
    source_type: Optional[str] = None
    include_podcasts: bool = True
    include_documents: bool = True


class SearchResultItem(BaseModel):
    score: float
    text: str
    source: str
    source_type: str
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SearchResponse(BaseModel):
    results: List[SearchResultItem]
    query: str
    count: int


# ---------------------------------------------------------------------
# Generic Schemas
# ---------------------------------------------------------------------
class ErrorResponse(BaseModel):
    error: str
    detail: Optional[str] = None


class SuccessResponse(BaseModel):
    success: bool = True
    message: str


class HealthResponse(BaseModel):
    status: str = "healthy"
    database: bool
    version: str = "0.1.0"

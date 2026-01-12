"""
Unit insight extraction from transcripts.

This module handles:
- Loading transcript text from GCS storage
- Extracting health insights via LLM (trends, health stakes)
- Persisting insights with embeddings to the database
- Deduplication: updating existing insights when similarity >= 0.90

GCS-first mode: All transcripts must be stored in GCS (gs://bucket/path/to/transcript.json)
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple, Union

import numpy as np
from sqlalchemy.orm import Session

from innovation_intelligence.analysis.insights.embedder import embed_text
from innovation_intelligence.config import settings
from innovation_intelligence.db.models import Document, PodcastEpisode, UnitInsight
from innovation_intelligence.llm.gemini_client import GeminiClient
from innovation_intelligence.llm.tools.insights_tool import extract_health_insights_from_text
from innovation_intelligence.logger import get_logger

log = get_logger(__name__)

# Type alias for transcript source (GCS URI only)
TranscriptSource = str  # GCS URI: gs://bucket/path/to/transcript.json

SourceEntity = Union[PodcastEpisode, Document]

# Similarity threshold for deduplication (update instead of insert)
DEDUP_SIMILARITY_THRESHOLD = 0.90


# Data Structures
@dataclass(frozen=True)
class Insight:
    """Extracted insight before persistence."""

    name: str
    description: str
    type: str  # "trend" | "health_stake"
    evidence: str
    source_filename: Optional[str] = None


@dataclass(frozen=True)
class ExtractionResult:
    """Result of insight extraction from a transcript."""

    filename: str
    insights: List[Insight]
    extraction_notes: str = ""


# Transcript Loading
def _extract_text_from_json_data(data: Any, source_name: str) -> str:
    """Extract text from various JSON structures."""
    if isinstance(data, str):
        return data

    if isinstance(data, list):
        texts: List[str] = []
        for item in data:
            if isinstance(item, str):
                texts.append(item)
            elif isinstance(item, dict):
                txt = item.get("text") or item.get("content") or item.get("transcript") or ""
                if txt:
                    texts.append(txt)
        return "\n".join(texts)

    if isinstance(data, dict):
        # Direct text fields
        for key in ("text", "transcript", "content"):
            if key in data:
                return str(data[key])

        # WhisperX format
        if "segments" in data and isinstance(data["segments"], list):
            return "\n".join(
                seg.get("text", "") for seg in data["segments"] if seg.get("text")
            )

        # Generic results array
        if "results" in data and isinstance(data["results"], list):
            return "\n".join(
                r.get("text") or r.get("transcript") or "" for r in data["results"]
            )

    log.warning("[EXTRACT] Unknown JSON structure in %s, converting to string", source_name)
    return str(data)


def _load_transcript_from_gcs(gcs_uri: str) -> str:
    """
    Load transcript text from GCS.

    Args:
        gcs_uri: GCS URI (gs://bucket/path/to/transcript.json)

    Returns:
        Transcript text extracted from the JSON

    Raises:
        ValueError: If GCS service is not available or transcript is empty
    """
    from innovation_intelligence.ingestion.gcs_service import GCSStorageService

    log.info("[EXTRACT] Loading transcript from GCS: %s", gcs_uri)

    gcs = GCSStorageService()
    data = gcs.get_transcript(gcs_uri)

    # Extract the transcript URI basename for logging
    source_name = gcs_uri.split("/")[-1] if "/" in gcs_uri else gcs_uri

    return _extract_text_from_json_data(data, source_name)


def load_transcript_text(gcs_uri: TranscriptSource) -> str:
    """
    Load transcript text from GCS URI.

    Args:
        gcs_uri: GCS URI (gs://bucket/path/to/transcript.json)

    Returns:
        Transcript text content

    Raises:
        ValueError: If GCS service is not available or transcript is empty
    """
    if not gcs_uri.startswith("gs://"):
        raise ValueError(f"Only GCS URIs are supported (gs://...), got: {gcs_uri}")

    return _load_transcript_from_gcs(gcs_uri)


# Client Factory
def _get_default_client() -> GeminiClient:
    """Create default GeminiClient from settings."""
    return GeminiClient(
        project=settings.gcp.project_id,
        location=settings.gcp.location,
        model_id=settings.gcp.gemini_model_id,
    )


# Insight Extraction (Pure - No DB)
def extract_insights_from_transcript(
    gcs_uri: TranscriptSource,
    *,
    filename: Optional[str] = None,
    client: Optional[GeminiClient] = None,
) -> ExtractionResult:
    """
    Extract health insights from a transcript using LLM.

    Args:
        gcs_uri: GCS URI (gs://bucket/path/to/transcript.json)
        filename: Optional identifier (defaults to GCS key basename)
        client: Optional GeminiClient (creates default if not provided)

    Returns:
        ExtractionResult with parsed insights

    Raises:
        ValueError: If GCS URI is invalid or transcript is empty
    """
    # Determine filename from GCS URI if not provided
    if filename is None:
        # Extract basename from GCS URI
        filename = gcs_uri.split("/")[-1].rsplit(".", 1)[0]

    text = load_transcript_text(gcs_uri)
    if not text.strip():
        raise ValueError(f"Empty transcript at {gcs_uri}")

    log.info("[EXTRACT] Processing transcript: %s (%d chars)", filename, len(text))

    # Use provided client or create default
    client = client or _get_default_client()

    tool_output = extract_health_insights_from_text(
        transcript_text=text,
        filename=filename,
        client=client,
    )

    insights: List[Insight] = []
    for item in tool_output.get("insights", []):
        name = item.get("name", "").strip()
        description = item.get("description", "").strip()
        insight_type = item.get("type", "").strip()

        if not name or not description or not insight_type:
            log.warning("[EXTRACT] Skipping incomplete insight: %s", item)
            continue

        insights.append(
            Insight(
                name=name,
                description=description,
                type=insight_type,
                evidence=item.get("evidence", "").strip(),
                source_filename=filename,
            )
        )

    log.info("[EXTRACT] Extracted %d insights from %s", len(insights), filename)

    return ExtractionResult(
        filename=filename,
        insights=insights,
        extraction_notes=tool_output.get("extraction_notes", "") or "",
    )


# Similarity Helpers
def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Compute cosine similarity between two vectors (assumes normalized)."""
    return float(np.dot(a, b))


def _find_most_similar_insight(
    session: Session,
    embedding: List[float],
    threshold: float = DEDUP_SIMILARITY_THRESHOLD,
) -> Tuple[Optional[UnitInsight], float]:
    """
    Find the most similar existing insight in the database.

    Args:
        session: Database session
        embedding: The embedding vector to compare against
        threshold: Minimum similarity threshold to consider a match

    Returns:
        Tuple of (matched UnitInsight or None, similarity score)
    """
    # Get all existing insights with embeddings
    existing_insights = session.query(UnitInsight).filter(
        UnitInsight.embedding.isnot(None)
    ).all()

    if not existing_insights:
        return None, 0.0

    # Normalize input embedding
    new_vec = np.array(embedding, dtype=np.float32)
    norm = np.linalg.norm(new_vec)
    if norm > 0:
        new_vec = new_vec / norm

    best_match: Optional[UnitInsight] = None
    best_similarity = 0.0

    for insight in existing_insights:
        if insight.embedding is None:
            continue

        existing_vec = np.array(insight.embedding, dtype=np.float32)
        norm = np.linalg.norm(existing_vec)
        if norm > 0:
            existing_vec = existing_vec / norm

        similarity = _cosine_similarity(new_vec, existing_vec)

        if similarity > best_similarity:
            best_similarity = similarity
            best_match = insight

    if best_similarity >= threshold:
        return best_match, best_similarity

    return None, best_similarity


def _merge_sources(
    existing_insight: UnitInsight,
    new_source: SourceEntity,
) -> None:
    """
    Merge the new source into the existing insight's sources.

    Updates the additional_source_ids JSONB field to include the new source.

    Args:
        existing_insight: The existing UnitInsight to update
        new_source: The new source (PodcastEpisode or Document) to add
    """
    # Initialize additional_source_ids if None
    if existing_insight.additional_source_ids is None:
        existing_insight.additional_source_ids = {
            "episode_ids": [],
            "document_ids": [],
        }

    # Ensure the dict has proper structure
    additional = existing_insight.additional_source_ids
    if "episode_ids" not in additional:
        additional["episode_ids"] = []
    if "document_ids" not in additional:
        additional["document_ids"] = []

    is_episode = isinstance(new_source, PodcastEpisode)

    if is_episode:
        # Check if this episode is already tracked (as primary or additional)
        if existing_insight.episode_id == new_source.id:
            return  # Already the primary source
        if new_source.id in additional["episode_ids"]:
            return  # Already in additional sources

        # Add to additional sources
        additional["episode_ids"].append(new_source.id)
    else:
        # Document source
        if existing_insight.document_id == new_source.id:
            return  # Already the primary source
        if new_source.id in additional["document_ids"]:
            return  # Already in additional sources

        additional["document_ids"].append(new_source.id)

    # Force SQLAlchemy to detect the change in JSONB
    existing_insight.additional_source_ids = dict(additional)


# Persistence
def _is_episode(source: SourceEntity) -> bool:
    """Check if source is a PodcastEpisode."""
    return isinstance(source, PodcastEpisode)


def _get_existing_insights_count(session: Session, source: SourceEntity) -> int:
    """Get count of existing UnitInsights for a source."""
    query = session.query(UnitInsight)
    if _is_episode(source):
        query = query.filter(UnitInsight.episode_id == source.id)
    else:
        query = query.filter(UnitInsight.document_id == source.id)
    return query.count()


def _create_unit_insights(
    session: Session,
    source: SourceEntity,
    extraction: ExtractionResult,
    *,
    force: bool = False,
    dedup_threshold: float = DEDUP_SIMILARITY_THRESHOLD,
) -> List[UnitInsight]:
    """
    Create or update UnitInsight rows with embeddings.

    Implements deduplication: if a newly extracted insight is very similar
    (similarity >= dedup_threshold, default 0.90) to an existing insight,
    the existing insight is UPDATED instead of creating a duplicate.
    Sources are merged to track all contributing sources.

    Args:
        session: Database session
        source: PodcastEpisode or Document
        extraction: Extraction result with insights
        force: If True, create even if insights already exist for this source
        dedup_threshold: Similarity threshold for deduplication (default 0.90)

    Returns:
        List of created or updated UnitInsight objects
    """
    existing_count = _get_existing_insights_count(session, source)
    if existing_count > 0 and not force:
        log.info("[EXTRACT] %d UnitInsights already exist for %s, skipping", existing_count, source)
        return []

    if existing_count > 0 and force:
        log.info("[EXTRACT] Force mode: will process insights alongside %d existing", existing_count)

    created: List[UnitInsight] = []
    updated: List[UnitInsight] = []

    for ins in extraction.insights:
        embedding = embed_text(f"{ins.name}. {ins.description}")

        # Check for similar existing insight
        similar_insight, similarity = _find_most_similar_insight(
            session, embedding, threshold=dedup_threshold
        )

        if similar_insight is not None:
            # UPDATE existing insight instead of creating duplicate
            log.info(
                "[EXTRACT] Found similar insight (%.3f similarity): '%s' ~ '%s' -> merging sources",
                similarity,
                ins.name,
                similar_insight.name,
            )

            # Merge the source into the existing insight
            _merge_sources(similar_insight, source)

            updated.append(similar_insight)
        else:
            # CREATE new insight
            unit_insight = UnitInsight(
                name=ins.name,
                description=ins.description,
                type=ins.type,
                embedding=embedding,
                episode_id=source.id if _is_episode(source) else None,
                document_id=source.id if not _is_episode(source) else None,
            )
            session.add(unit_insight)
            created.append(unit_insight)

    session.commit()

    # Refresh to get IDs for new insights
    for ui in created:
        session.refresh(ui)

    # Refresh updated insights to reflect changes
    for ui in updated:
        session.refresh(ui)

    log.info(
        "[EXTRACT] Processed insights for %s: %d created, %d updated (deduplicated)",
        source,
        len(created),
        len(updated),
    )

    # Return all processed insights (both new and updated)
    return created + updated


# Public API
def extract_and_persist_insights(
    *,
    gcs_uri: TranscriptSource,
    session: Session,
    source: SourceEntity,
    filename: Optional[str] = None,
    client: Optional[GeminiClient] = None,
    force: bool = False,
) -> tuple[ExtractionResult, List[UnitInsight]]:
    """
    End-to-end insight extraction and persistence.

    Steps:
    1. Extract insights from transcript via LLM (GCS only)
    2. Create UnitInsight rows with embeddings

    Args:
        gcs_uri: GCS URI (gs://bucket/path/to/transcript.json)
        session: Database session
        source: PodcastEpisode or Document entity
        filename: Optional identifier (defaults to GCS basename)
        client: Optional GeminiClient (creates default if not provided)
        force: If True, re-extract even if insights exist

    Returns:
        Tuple of (ExtractionResult, list of created UnitInsight objects)

    Raises:
        ValueError: If GCS URI is invalid or transcript is empty
    """
    extraction = extract_insights_from_transcript(
        gcs_uri=gcs_uri,
        filename=filename,
        client=client,
    )

    unit_insights = _create_unit_insights(
        session=session,
        source=source,
        extraction=extraction,
        force=force,
    )

    return extraction, unit_insights


# Backwards compatibility alias
extract_and_register_unit_insights = extract_and_persist_insights
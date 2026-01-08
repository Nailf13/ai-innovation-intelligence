"""
Unit insight extraction from transcripts.

This module handles:
- Loading transcript text from various JSON formats
- Extracting health insights via LLM (trends, health stakes)
- Persisting insights with embeddings to the database
- Assessing dimensions (adoption, expectation, progress) via RAG
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Union

from sqlalchemy.orm import Session

from innovation_intelligence.analysis.insights.embedder import embed_text
from innovation_intelligence.config import settings
from innovation_intelligence.db.models import Document, PodcastEpisode, UnitInsight
from innovation_intelligence.llm.tools.insights_tool import extract_health_insights_from_text
from innovation_intelligence.logger import get_logger

log = get_logger(__name__)

SourceEntity = Union[PodcastEpisode, Document]


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
def _extract_text_from_json_data(data, path: Path) -> str:
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

    log.warning("[EXTRACT] Unknown JSON structure in %s, converting to string", path)
    return str(data)


def _load_json_transcript(path: Path) -> str:
    """Load and parse a JSON transcript file."""
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return _extract_text_from_json_data(data, path)


def load_transcript_text(path: Path) -> str:
    """
    Load transcript text from a file.

    Supports:
    - Plain text files
    - JSON files with various structures (WhisperX, segments, results)

    Raises:
        FileNotFoundError: If file does not exist
        ValueError: If file is empty after loading
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Transcript file not found: {path}")

    if path.suffix.lower() == ".json":
        return _load_json_transcript(path)

    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        # Fallback: try parsing as JSON anyway
        return _load_json_transcript(path)


# Insight Extraction (Pure - No DB)
def extract_insights_from_transcript(
    transcript_path: Path,
    *,
    filename: Optional[str] = None,
) -> ExtractionResult:
    """
    Extract health insights from a transcript file using LLM.

    Args:
        transcript_path: Path to the transcript file
        filename: Optional identifier (defaults to file stem)

    Returns:
        ExtractionResult with parsed insights

    Raises:
        FileNotFoundError: If transcript file doesn't exist
        ValueError: If transcript is empty
    """
    transcript_path = Path(transcript_path)
    filename = filename or transcript_path.stem

    text = load_transcript_text(transcript_path)
    if not text.strip():
        raise ValueError(f"Empty transcript at {transcript_path}")

    log.info("[EXTRACT] Processing transcript: %s (%d chars)", filename, len(text))

    tool_output = extract_health_insights_from_text(
        transcript_text=text,
        filename=filename,
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
) -> List[UnitInsight]:
    """
    Create UnitInsight rows with embeddings.

    Args:
        session: Database session
        source: PodcastEpisode or Document
        extraction: Extraction result with insights
        force: If True, create even if insights already exist

    Returns:
        List of created UnitInsight objects
    """
    existing_count = _get_existing_insights_count(session, source)
    if existing_count > 0 and not force:
        log.info("[EXTRACT] %d UnitInsights already exist for %s, skipping", existing_count, source)
        return []

    if existing_count > 0 and force:
        log.info("[EXTRACT] Force mode: will add insights alongside %d existing", existing_count)

    created: List[UnitInsight] = []
    for ins in extraction.insights:
        embedding = embed_text(f"{ins.name}. {ins.description}")

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

    # Refresh to get IDs
    for ui in created:
        session.refresh(ui)

    log.info("[EXTRACT] Created %d UnitInsights for %s", len(created), source)
    return created


# Public API
def extract_and_persist_insights(
    *,
    transcript_path: Path,
    session: Session,
    source: SourceEntity,
    filename: Optional[str] = None,
    force: bool = False,
    assess_dimensions: bool = False,
) -> tuple[ExtractionResult, List[UnitInsight]]:
    """
    End-to-end insight extraction and persistence.

    Steps:
    1. Extract insights from transcript via LLM
    2. Create UnitInsight rows with embeddings
    3. (Optional) Assess dimensions via RAG

    Args:
        transcript_path: Path to transcript file
        session: Database session
        source: PodcastEpisode or Document entity
        filename: Optional identifier (defaults to file stem)
        force: If True, re-extract even if insights exist
        assess_dimensions: If True, assess dimensions for each insight

    Returns:
        Tuple of (ExtractionResult, list of created UnitInsight objects)

    Raises:
        FileNotFoundError: If transcript doesn't exist
        ValueError: If transcript is empty
    """
    extraction = extract_insights_from_transcript(
        transcript_path=transcript_path,
        filename=filename,
    )

    unit_insights = _create_unit_insights(
        session=session,
        source=source,
        extraction=extraction,
        force=force,
    )

    # Assess dimensions if requested and insights were created
    if assess_dimensions and unit_insights:
        _assess_insight_dimensions(session, unit_insights)

    return extraction, unit_insights


def _assess_insight_dimensions(
    session: Session,
    insights: List[UnitInsight],
) -> None:
    """
    Assess dimensions for a list of insights using RAG.

    Args:
        session: Database session
        insights: List of UnitInsight objects to assess
    """
    # Import here to avoid circular imports
    from innovation_intelligence.analysis.dimensions import (
        DimensionAssessmentService,
        AssessmentConfig,
    )

    log.info("[EXTRACT] Assessing dimensions for %d insights", len(insights))

    config = AssessmentConfig(
        persist_results=True,
        skip_existing=True,
    )

    service = DimensionAssessmentService(session, config)

    try:
        results = service.assess_insights_batch(insights)
        assessed_count = sum(1 for r in results if r.adoption or r.expectation or r.progress)
        log.info("[EXTRACT] Assessed dimensions for %d/%d insights", assessed_count, len(insights))
    except Exception as e:
        log.error("[EXTRACT] Failed to assess dimensions: %s", e)


def extract_and_persist_insights_with_dimensions(
    *,
    transcript_path: Path,
    session: Session,
    source: SourceEntity,
    filename: Optional[str] = None,
    force: bool = False,
) -> tuple[ExtractionResult, List[UnitInsight]]:
    """
    Extract insights and assess their dimensions in one call.

    This is a convenience wrapper that always enables dimension assessment.

    Args:
        transcript_path: Path to transcript file
        session: Database session
        source: PodcastEpisode or Document entity
        filename: Optional identifier (defaults to file stem)
        force: If True, re-extract even if insights exist

    Returns:
        Tuple of (ExtractionResult, list of created UnitInsight objects)
    """
    return extract_and_persist_insights(
        transcript_path=transcript_path,
        session=session,
        source=source,
        filename=filename,
        force=force,
        assess_dimensions=True,
    )


# Backwards compatibility alias
extract_and_register_unit_insights = extract_and_persist_insights

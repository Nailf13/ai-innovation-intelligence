from __future__ import annotations

from typing import Optional, Dict, Any, List, Union

from sqlalchemy.orm import Session

from innovation_intelligence.db.models import (
    HealthInsight,
    HealthInsightOccurrence,
    HealthInsightDimensions,
    PodcastEpisode,
    Document,
)


SourceType = Union[PodcastEpisode, Document]


class HealthInsightRepository:
    """Low-level DB operations for health insights & occurrences."""

    def __init__(self, session: Session) -> None:
        self.session = session

    # ------------------------------------------------------------------
    # Canonical insights
    # ------------------------------------------------------------------
    def get_by_name_type_period(
        self,
        name: str,  
        type_: str,
        period: Optional[str],
    ) -> Optional[HealthInsight]:
        q = (
            self.session.query(HealthInsight)
            .filter(HealthInsight.name == name)
            .filter(HealthInsight.type == type_)
        )
        if period is not None:
            q = q.filter(HealthInsight.period == period)
        return q.first()

    def create_insight(
        self,
        name: str,
        type_: str,
        description: str,
        period: Optional[str],
        first_episode: Optional[PodcastEpisode] = None,
    ) -> HealthInsight:
        insight = HealthInsight(
            name=name,
            type=type_,
            description=description,
            period=period,
            first_seen_episode_id=first_episode.id if first_episode else None,
            last_seen_episode_id=first_episode.id if first_episode else None,
        )
        self.session.add(insight)
        self.session.flush()  # assign id
        return insight

    def touch_last_seen(
        self,
        insight: HealthInsight,
        episode: Optional[PodcastEpisode],
    ) -> None:
        if episode is not None:
            insight.last_seen_episode_id = episode.id

    def list_all_insights(self) -> List[HealthInsight]:
        return self.session.query(HealthInsight).all()

    # ------------------------------------------------------------------
    # Occurrences
    # ------------------------------------------------------------------
    def create_occurrence_for_source(
        self,
        insight: HealthInsight,
        owner: SourceType,
        raw: Dict[str, Any],
        period: Optional[str],
        source_kind: str,
    ) -> HealthInsightOccurrence:
        """
        Create a HealthInsightOccurrence, binding either episode_id or document_id
        depending on source_kind ('episode' | 'document').
        """
        if source_kind not in {"episode", "document"}:
            raise ValueError(f"Invalid source_kind: {source_kind}")

        occ = HealthInsightOccurrence(
            insight_id=insight.id,
            episode_id=owner.id if source_kind == "episode" else None,
            document_id=owner.id if source_kind == "document" else None,
            raw_name=raw.get("name", ""),
            raw_description=raw.get("description", ""),
            raw_type=raw.get("type", ""),
            evidence=raw.get("evidence", ""),
            source_filename=raw.get("source_filename", ""),
            period=period,
        )
        self.session.add(occ)
        return occ

    def list_occurrences_for_insight(
        self,
        insight_id: int,
    ) -> List[HealthInsightOccurrence]:
        return (
            self.session.query(HealthInsightOccurrence)
            .filter(HealthInsightOccurrence.insight_id == insight_id)
            .order_by(HealthInsightOccurrence.created_at.asc())
            .all()
        )

    # ------------------------------------------------------------------
    # Dimensions
    # ------------------------------------------------------------------
    def get_dimensions(self, insight_id: int) -> Optional[HealthInsightDimensions]:
        return (
            self.session.query(HealthInsightDimensions)
            .filter(HealthInsightDimensions.insight_id == insight_id)
            .first()
        )

    def upsert_dimensions(
        self,
        insight_id: int,
        adoption_stage: Optional[str] = None,
        expectation_level: Optional[str] = None,
        progress_horizon: Optional[str] = None,
        evidence_json: Optional[str] = None,
    ) -> HealthInsightDimensions:
        dim = self.get_dimensions(insight_id)
        if dim is None:
            dim = HealthInsightDimensions(
                insight_id=insight_id,
                adoption_stage=adoption_stage,
                expectation_level=expectation_level,
                progress_horizon=progress_horizon,
                evidence_json=evidence_json,
            )
            self.session.add(dim)
        else:
            if adoption_stage is not None:
                dim.adoption_stage = adoption_stage
            if expectation_level is not None:
                dim.expectation_level = expectation_level
            if progress_horizon is not None:
                dim.progress_horizon = progress_horizon
            if evidence_json is not None:
                dim.evidence_json = evidence_json

        self.session.flush()
        return dim

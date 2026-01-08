# src/innovation_intelligence/api/routers/insight_views.py
"""Insights retrieval endpoints."""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload

from innovation_intelligence.api.deps import get_db
from innovation_intelligence.api.schemas import (
    ClusterResponse,
    ClusterListResponse,
    MacroInsightResponse,
    MacroInsightListResponse,
    UnitInsightResponse,
    UnitInsightListResponse,
    DimensionResponse,
    SearchRequest,
    SearchResultResponse,
)
from innovation_intelligence.db.models import (
    Cluster,
    MacroInsight,
    UnitInsight,
    InsightDimension,
    DimensionEvidence,
)
from innovation_intelligence.logger import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/insights", tags=["insights"])


def _get_source_info(ui: UnitInsight) -> tuple[str, Optional[int]]:
    """Get source type and ID from UnitInsight."""
    if ui.episode_id:
        return "podcast", ui.episode_id
    elif ui.document_id:
        return "document", ui.document_id
    return "unknown", None


@router.get("/unit", response_model=UnitInsightListResponse)
def list_unit_insights(
    skip: int = 0,
    limit: int = 50,
    source_type: Optional[str] = None,
    insight_type: Optional[str] = None,
    has_macro: Optional[bool] = None,
    db: Session = Depends(get_db),
):
    """List unit insights with optional filtering."""
    query = db.query(UnitInsight)
    if source_type:
        if source_type == "podcast":
            query = query.filter(UnitInsight.episode_id.isnot(None))
        elif source_type == "document":
            query = query.filter(UnitInsight.document_id.isnot(None))
    if insight_type:
        query = query.filter(UnitInsight.type == insight_type)
    if has_macro is not None:
        if has_macro:
            query = query.filter(UnitInsight.macro_insight_id.isnot(None))
        else:
            query = query.filter(UnitInsight.macro_insight_id.is_(None))
    total = query.count()
    insights = query.offset(skip).limit(limit).all()

    result_insights = []
    for ui in insights:
        src_type, src_id = _get_source_info(ui)
        result_insights.append(UnitInsightResponse(
            id=ui.id, name=ui.name, description=ui.description, type=ui.type,
            source_type=src_type, source_id=src_id,
            macro_insight_id=ui.macro_insight_id, created_at=None,
        ))
    return UnitInsightListResponse(insights=result_insights, count=total)


@router.get("/unit/{insight_id}", response_model=UnitInsightResponse)
def get_unit_insight(insight_id: int, db: Session = Depends(get_db)):
    """Get a specific unit insight with its dimensions."""
    insight = db.get(UnitInsight, insight_id)
    if not insight:
        raise HTTPException(status_code=404, detail="Unit insight not found")
    dimensions = db.query(InsightDimension).filter(InsightDimension.unit_insight_id == insight_id).all()
    dimension_responses = []
    for dim in dimensions:
        evidence = db.query(DimensionEvidence).filter(DimensionEvidence.dimension_id == dim.id).all()
        dimension_responses.append(DimensionResponse(
            id=dim.id, dimension_type=dim.dimension_type, value=dim.value,
            confidence=dim.confidence, evidence=[e.chunk_text for e in evidence],
        ))
    src_type, src_id = _get_source_info(insight)
    return UnitInsightResponse(
        id=insight.id, name=insight.name, description=insight.description, type=insight.type,
        source_type=src_type, source_id=src_id,
        macro_insight_id=insight.macro_insight_id, dimensions=dimension_responses, created_at=None,
    )


@router.get("/macro", response_model=MacroInsightListResponse)
def list_macro_insights(skip: int = 0, limit: int = 50, cluster_id: Optional[int] = None, has_cluster: Optional[bool] = None, db: Session = Depends(get_db)):
    """List macro insights with optional filtering."""
    query = db.query(MacroInsight)
    if cluster_id is not None:
        query = query.filter(MacroInsight.cluster_id == cluster_id)
    if has_cluster is not None:
        if has_cluster:
            query = query.filter(MacroInsight.cluster_id.isnot(None))
        else:
            query = query.filter(MacroInsight.cluster_id.is_(None))
    total = query.count()
    macros = query.offset(skip).limit(limit).all()
    return MacroInsightListResponse(
        insights=[MacroInsightResponse(id=m.id, name=m.name, description=m.description, cluster_id=m.cluster_id,
            unit_insight_count=len(m.unit_insights) if m.unit_insights else 0, created_at=m.created_at) for m in macros],
        count=total,
    )


@router.get("/macro/{macro_id}", response_model=MacroInsightResponse)
def get_macro_insight(macro_id: int, db: Session = Depends(get_db)):
    """Get a specific macro insight with its unit insights."""
    macro = db.query(MacroInsight).options(joinedload(MacroInsight.unit_insights)).filter(MacroInsight.id == macro_id).first()
    if not macro:
        raise HTTPException(status_code=404, detail="Macro insight not found")
    unit_insights = []
    for ui in macro.unit_insights:
        src_type, src_id = _get_source_info(ui)
        unit_insights.append(UnitInsightResponse(id=ui.id, name=ui.name, description=ui.description, type=ui.type,
            source_type=src_type, source_id=src_id,
            macro_insight_id=ui.macro_insight_id, created_at=None))
    return MacroInsightResponse(id=macro.id, name=macro.name, description=macro.description, cluster_id=macro.cluster_id,
        unit_insight_count=len(unit_insights), unit_insights=unit_insights, created_at=None)


@router.get("/clusters", response_model=ClusterListResponse)
def list_clusters(db: Session = Depends(get_db)):
    """List all strategic clusters."""
    clusters = db.query(Cluster).all()
    return ClusterListResponse(
        clusters=[ClusterResponse(id=c.id, name=c.name, description=c.description,
            macro_insight_count=len(c.macro_insights) if c.macro_insights else 0) for c in clusters],
        count=len(clusters),
    )


@router.get("/clusters/{cluster_id}", response_model=ClusterResponse)
def get_cluster(cluster_id: int, db: Session = Depends(get_db)):
    """Get a specific cluster with its macro insights."""
    cluster = db.query(Cluster).options(joinedload(Cluster.macro_insights)).filter(Cluster.id == cluster_id).first()
    if not cluster:
        raise HTTPException(status_code=404, detail="Cluster not found")
    macro_insights = [MacroInsightResponse(id=m.id, name=m.name, description=m.description, cluster_id=m.cluster_id,
        unit_insight_count=len(m.unit_insights) if m.unit_insights else 0, created_at=m.created_at) for m in cluster.macro_insights]
    return ClusterResponse(id=cluster.id, name=cluster.name, description=cluster.description,
        macro_insight_count=len(macro_insights), macro_insights=macro_insights)


@router.post("/search", response_model=SearchResultResponse)
def search_insights(request: SearchRequest, db: Session = Depends(get_db)):
    """Semantic search over indexed content using pgvector."""
    from innovation_intelligence.search.vector_search import semantic_search
    results = semantic_search(db, query=request.query, top_k=request.top_k, source_type=request.source_type)
    return SearchResultResponse(query=request.query, results=[{"chunk_id": r["chunk_id"], "source_type": r["source_type"],
        "text": r["text"], "similarity": r["similarity"], "metadata": r.get("metadata", {})} for r in results], count=len(results))


@router.get("/hierarchy")
def get_insight_hierarchy(db: Session = Depends(get_db)):
    """Get the full insight hierarchy: clusters -> macro insights -> unit insights."""
    clusters = db.query(Cluster).options(joinedload(Cluster.macro_insights).joinedload(MacroInsight.unit_insights)).all()
    unassigned_macros = db.query(MacroInsight).filter(MacroInsight.cluster_id.is_(None)).options(joinedload(MacroInsight.unit_insights)).all()
    orphan_units = db.query(UnitInsight).filter(UnitInsight.macro_insight_id.is_(None)).all()
    return {
        "clusters": [{"id": c.id, "name": c.name, "description": c.description, "macro_insights": [
            {"id": m.id, "name": m.name, "description": m.description, "unit_insights": [
                {"id": ui.id, "name": ui.name, "type": ui.type} for ui in m.unit_insights]} for m in c.macro_insights]} for c in clusters],
        "unassigned_macro_insights": [{"id": m.id, "name": m.name, "description": m.description, "unit_insights": [
            {"id": ui.id, "name": ui.name, "type": ui.type} for ui in m.unit_insights]} for m in unassigned_macros],
        "orphan_unit_insights": [{"id": ui.id, "name": ui.name, "type": ui.type} for ui in orphan_units],
    }

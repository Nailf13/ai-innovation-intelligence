# src/innovation_intelligence/api/routers/__init__.py
"""
API routers for Innovation Intelligence.
"""
from innovation_intelligence.api.routers.podcasts import router as podcasts_router
from innovation_intelligence.api.routers.documents import router as documents_router
from innovation_intelligence.api.routers.ingestion import router as ingestion_router
from innovation_intelligence.api.routers.analysis import router as analysis_router
from innovation_intelligence.api.routers.insight_views import router as insights_router
from innovation_intelligence.api.routers.media import router as media_router
from innovation_intelligence.api.routers.media_proxy import router as media_proxy_router

__all__ = [
    "podcasts_router",
    "documents_router",
    "ingestion_router",
    "analysis_router",
    "insights_router",
    "media_router",
    "media_proxy_router",
]

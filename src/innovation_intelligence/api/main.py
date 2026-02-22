# src/innovation_intelligence/api/main.py
"""FastAPI application entry point."""
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from innovation_intelligence.api.routers import (
    podcasts,
    documents,
    ingestion,
    analysis,
    media,
    media_proxy_router as media_proxy,
)
from innovation_intelligence.api.routers.insight_views import router as insights_router
from innovation_intelligence.analysis.insights.embedder import preload_model
from innovation_intelligence.logger import get_logger

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle for the API."""
    # Load embedding model in a thread so we don't block the event loop
    log.info("[STARTUP] Preloading embedding model...")
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, preload_model)
    log.info("[STARTUP] Embedding model ready")
    yield


app = FastAPI(
    title="Innovation Intelligence API",
    description="API for health trend analysis from podcasts and documents",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(podcasts.router)
app.include_router(documents.router)
app.include_router(ingestion.router)
app.include_router(analysis.router)
app.include_router(insights_router)
app.include_router(media.router)
app.include_router(media_proxy)


@app.get("/")
def root():
    """Root endpoint with API info."""
    return {"name": "Innovation Intelligence API", "version": "1.0.0", "docs": "/docs", "openapi": "/openapi.json"}


@app.get("/health")
def health_check():
    """Health check endpoint."""
    from sqlalchemy import text
    from innovation_intelligence.db.session import SessionLocal
    try:
        session = SessionLocal()
        session.execute(text("SELECT 1"))
        session.close()
        db_status = "healthy"
    except Exception as e:
        db_status = f"unhealthy: {e}"
    return {"status": "healthy", "database": db_status}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

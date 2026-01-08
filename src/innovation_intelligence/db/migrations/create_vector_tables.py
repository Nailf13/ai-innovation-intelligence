# src/innovation_intelligence/db/migrations/create_vector_tables.py
"""
Database migration script for pgvector tables.

Creates:
- pgvector extension
- podcast_chunk_vectors table
- document_chunk_vectors table
- HNSW indexes for similarity search
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from innovation_intelligence.db.session import engine, SessionLocal
from innovation_intelligence.db.base import Base
from innovation_intelligence.logger import get_logger

# Import models to register them with Base.metadata
from innovation_intelligence.db.models import PodcastChunkVector, DocumentChunkVector

log = get_logger(__name__)


def create_pgvector_extension() -> None:
    """
    Create the pgvector extension if it doesn't exist.
    """
    log.info("[MIGRATION] Creating pgvector extension...")

    with engine.connect() as conn:
        try:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            conn.commit()
            log.info("[MIGRATION] pgvector extension created/verified")
        except ProgrammingError as e:
            log.error(f"[MIGRATION] Failed to create pgvector extension: {e}")
            raise


def create_vector_tables() -> None:
    """
    Create the vector chunk tables.
    """
    log.info("[MIGRATION] Creating vector tables...")

    # Create tables defined in models
    Base.metadata.create_all(
        bind=engine,
        tables=[
            PodcastChunkVector.__table__,
            DocumentChunkVector.__table__,
        ],
    )

    log.info("[MIGRATION] Vector tables created")


def drop_vector_tables() -> None:
    """
    Drop the vector chunk tables (for reset/testing).
    """
    log.warning("[MIGRATION] Dropping vector tables...")

    Base.metadata.drop_all(
        bind=engine,
        tables=[
            PodcastChunkVector.__table__,
            DocumentChunkVector.__table__,
        ],
    )

    log.info("[MIGRATION] Vector tables dropped")


def verify_tables() -> None:
    """
    Verify that vector tables exist and have correct structure.
    """
    log.info("[MIGRATION] Verifying vector tables...")

    with engine.connect() as conn:
        # Check tables exist
        result = conn.execute(text("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
            AND table_name IN ('podcast_chunk_vectors', 'document_chunk_vectors')
        """))
        tables = [row[0] for row in result]

        if 'podcast_chunk_vectors' in tables:
            log.info("[MIGRATION] ✓ podcast_chunk_vectors exists")
        else:
            log.warning("[MIGRATION] ✗ podcast_chunk_vectors missing")

        if 'document_chunk_vectors' in tables:
            log.info("[MIGRATION] ✓ document_chunk_vectors exists")
        else:
            log.warning("[MIGRATION] ✗ document_chunk_vectors missing")

        # Check pgvector extension
        result = conn.execute(text("""
            SELECT extname FROM pg_extension WHERE extname = 'vector'
        """))
        extensions = [row[0] for row in result]

        if 'vector' in extensions:
            log.info("[MIGRATION] ✓ pgvector extension installed")
        else:
            log.warning("[MIGRATION] ✗ pgvector extension missing")


def run_migration() -> None:
    """
    Run the full migration: extension + tables.
    """
    log.info("[MIGRATION] Starting vector tables migration...")

    create_pgvector_extension()
    create_vector_tables()
    verify_tables()

    log.info("[MIGRATION] Migration complete")


def reset_migration() -> None:
    """
    Drop and recreate vector tables (for development/testing).
    """
    log.warning("[MIGRATION] Resetting vector tables...")

    drop_vector_tables()
    create_pgvector_extension()
    create_vector_tables()
    verify_tables()

    log.info("[MIGRATION] Reset complete")


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        command = sys.argv[1]
        if command == "reset":
            reset_migration()
        elif command == "verify":
            verify_tables()
        elif command == "drop":
            drop_vector_tables()
        else:
            print(f"Unknown command: {command}")
            print("Usage: python create_vector_tables.py [reset|verify|drop]")
            sys.exit(1)
    else:
        run_migration()

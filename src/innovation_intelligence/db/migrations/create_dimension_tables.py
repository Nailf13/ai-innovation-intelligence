# src/innovation_intelligence/db/migrations/create_dimension_tables.py
"""
Database migration script for dimension assessment tables.

Creates:
- insight_dimensions table (dimension assessments for unit insights)
- dimension_evidence table (RAG evidence supporting assessments)
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from innovation_intelligence.db.session import engine
from innovation_intelligence.db.base import Base
from innovation_intelligence.logger import get_logger

# Import models to register them with Base.metadata
from innovation_intelligence.db.models import InsightDimension, DimensionEvidence

log = get_logger(__name__)


def create_dimension_tables() -> None:
    """
    Create the dimension assessment tables.
    """
    log.info("[MIGRATION] Creating dimension tables...")

    # Create tables defined in models
    Base.metadata.create_all(
        bind=engine,
        tables=[
            InsightDimension.__table__,
            DimensionEvidence.__table__,
        ],
    )

    log.info("[MIGRATION] Dimension tables created")


def drop_dimension_tables() -> None:
    """
    Drop the dimension tables (for reset/testing).
    """
    log.warning("[MIGRATION] Dropping dimension tables...")

    # Drop in reverse order due to FK constraints
    Base.metadata.drop_all(
        bind=engine,
        tables=[
            DimensionEvidence.__table__,
            InsightDimension.__table__,
        ],
    )

    log.info("[MIGRATION] Dimension tables dropped")


def verify_tables() -> None:
    """
    Verify that dimension tables exist and have correct structure.
    """
    log.info("[MIGRATION] Verifying dimension tables...")

    with engine.connect() as conn:
        # Check tables exist
        result = conn.execute(text("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
            AND table_name IN ('insight_dimensions', 'dimension_evidence')
        """))
        tables = [row[0] for row in result]

        if 'insight_dimensions' in tables:
            log.info("[MIGRATION] insight_dimensions exists")
        else:
            log.warning("[MIGRATION] insight_dimensions missing")

        if 'dimension_evidence' in tables:
            log.info("[MIGRATION] dimension_evidence exists")
        else:
            log.warning("[MIGRATION] dimension_evidence missing")

        # Check columns in insight_dimensions
        if 'insight_dimensions' in tables:
            result = conn.execute(text("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                AND table_name = 'insight_dimensions'
            """))
            columns = [row[0] for row in result]
            log.info(f"[MIGRATION] insight_dimensions columns: {columns}")


def run_migration() -> None:
    """
    Run the full migration for dimension tables.
    """
    log.info("[MIGRATION] Starting dimension tables migration...")

    create_dimension_tables()
    verify_tables()

    log.info("[MIGRATION] Migration complete")


def reset_migration() -> None:
    """
    Drop and recreate dimension tables (for development/testing).
    """
    log.warning("[MIGRATION] Resetting dimension tables...")

    drop_dimension_tables()
    create_dimension_tables()
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
            drop_dimension_tables()
        else:
            print(f"Unknown command: {command}")
            print("Usage: python create_dimension_tables.py [reset|verify|drop]")
            sys.exit(1)
    else:
        run_migration()

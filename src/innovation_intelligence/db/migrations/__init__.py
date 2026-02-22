# src/innovation_intelligence/db/migrations/__init__.py
"""
Database migrations package.
"""
from innovation_intelligence.db.migrations.create_vector_tables import (
    run_migration as run_vector_migration,
    reset_migration as reset_vector_migration,
    verify_tables as verify_vector_tables,
)
from innovation_intelligence.db.migrations.add_audio_metadata import (
    run_migration as run_audio_metadata_migration,
    verify_columns as verify_audio_metadata_columns,
)


def run_migration():
    """Run all migrations."""
    run_vector_migration()
    run_audio_metadata_migration()


def reset_migration():
    """Reset vector tables."""
    reset_vector_migration()


def verify_tables():
    """Verify all tables and columns."""
    verify_vector_tables()
    verify_audio_metadata_columns()


__all__ = [
    "run_migration",
    "reset_migration",
    "verify_tables",
    "run_vector_migration",
    "run_audio_metadata_migration",
]

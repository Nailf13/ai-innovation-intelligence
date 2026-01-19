# src/innovation_intelligence/db/migrations/__init__.py
"""
Database migrations package.
"""
from innovation_intelligence.db.migrations.create_vector_tables import (
    run_migration as run_vector_migration,
    reset_migration as reset_vector_migration,
    verify_tables as verify_vector_tables,
)
from innovation_intelligence.db.migrations.add_gcs_columns import (
    run_migration as run_gcs_migration,
    verify_migration as verify_gcs_migration,
)
from innovation_intelligence.db.migrations.add_unit_insight_cluster import (
    run_migration as run_unit_insight_cluster_migration,
    verify_migration as verify_unit_insight_cluster_migration,
)


def run_migration():
    """Run all migrations."""
    run_vector_migration()
    run_gcs_migration()
    run_unit_insight_cluster_migration()


def reset_migration():
    """Reset vector tables."""
    reset_vector_migration()


def verify_tables():
    """Verify all tables and columns."""
    verify_vector_tables()
    verify_gcs_migration()
    verify_unit_insight_cluster_migration()


__all__ = [
    "run_migration",
    "reset_migration",
    "verify_tables",
    "run_vector_migration",
    "run_gcs_migration",
    "verify_gcs_migration",
    "run_unit_insight_cluster_migration",
    "verify_unit_insight_cluster_migration",
]

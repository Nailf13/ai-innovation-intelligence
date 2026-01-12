#!/usr/bin/env python3
# src/innovation_intelligence/cli/init_db.py
"""
CLI entrypoint for database initialization.

Creates all tables and enables required PostgreSQL extensions (pgvector).

Usage:
    init-db run                 # Initialize database
    init-db run --drop-existing # Drop all tables first (dangerous!)
    init-db status              # Check database status
"""
import argparse
import sys

from innovation_intelligence.config import settings
from innovation_intelligence.logger import get_logger

log = get_logger(__name__)


def cmd_run(args):
    """Initialize the database (create tables and extensions)."""
    from innovation_intelligence.db.session import engine, init_db
    from sqlalchemy import text

    print("=" * 80)
    print("DATABASE INITIALIZATION")
    print("=" * 80)
    print(f"Database URL: {settings.db.url.split('@')[-1]}")  # Hide credentials
    print("=" * 80)
    print()

    try:
        # Test connection
        print("Testing database connection...")
        with engine.connect() as conn:
            result = conn.execute(text("SELECT version()"))
            version = result.scalar()
            print(f"✓ Connected to PostgreSQL")
            print(f"  Version: {version.split(',')[0]}")
            print()

        # Drop existing tables if requested
        if args.drop_existing:
            print("WARNING: Dropping all existing tables...")
            if not args.yes:
                response = input("Are you sure? This will DELETE ALL DATA! [y/N]: ")
                if response.lower() != 'y':
                    print("Aborted.")
                    sys.exit(0)

            from innovation_intelligence.db.base import Base
            from innovation_intelligence.db import models  # noqa: F401

            print("Dropping tables...")
            Base.metadata.drop_all(bind=engine)
            print("✓ Tables dropped")
            print()

        # Enable pgvector extension
        print("Enabling pgvector extension...")
        with engine.connect() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            conn.commit()
            print("✓ pgvector extension enabled")
            print()

        # Create tables
        print("Creating database tables...")
        init_db()
        print("✓ All tables created successfully")
        print()

        # Verify tables
        print("Verifying tables...")
        with engine.connect() as conn:
            result = conn.execute(text(
                """
                SELECT tablename
                FROM pg_tables
                WHERE schemaname = 'public'
                ORDER BY tablename
                """
            ))
            tables = [row[0] for row in result]

            if tables:
                print(f"✓ Found {len(tables)} tables:")
                for table in tables:
                    print(f"  - {table}")
            else:
                print("⚠ No tables found")
            print()

        print("=" * 80)
        print("DATABASE INITIALIZATION COMPLETE")
        print("=" * 80)

    except Exception as e:
        print()
        print("=" * 80)
        print("ERROR: Database initialization failed")
        print("=" * 80)
        print(f"Error: {e}")
        print()
        print("Troubleshooting:")
        print("1. Ensure PostgreSQL is running (docker compose up -d)")
        print("2. Check DATABASE_URL in .env file")
        print("3. Verify database credentials")
        sys.exit(1)


def cmd_migrate(args):
    """Run database migrations."""
    from innovation_intelligence.db.migrations.drop_local_paths import run_migration, check_gcs_uris_exist

    print()
    print("=" * 80)
    print("DATABASE MIGRATION")
    print("=" * 80)
    print("Migration: Drop local path columns (GCS-first storage)")
    print("=" * 80)
    print()

    if args.check_only:
        # Check if migration is safe to run
        all_ok, message = check_gcs_uris_exist()
        if all_ok:
            print(f"✓ Migration is safe to run: {message}")
            print()
            print("To apply migration, run:")
            print("  init-db migrate")
        else:
            print(f"✗ Migration is NOT safe to run:")
            print(f"  {message}")
            print()
            print("To force migration anyway (NOT RECOMMENDED), run:")
            print("  init-db migrate --force")
            sys.exit(1)
        return

    # Run migration
    success = run_migration(force=args.force)
    if success:
        print()
        print("=" * 80)
        print("MIGRATION COMPLETE")
        print("=" * 80)
    else:
        sys.exit(1)


def cmd_status(args):
    """Check database status."""
    from innovation_intelligence.db.session import engine
    from sqlalchemy import text

    print()
    print("=" * 80)
    print("DATABASE STATUS")
    print("=" * 80)
    print(f"Database URL: {settings.db.url.split('@')[-1]}")  # Hide credentials
    print("=" * 80)
    print()

    try:
        # Test connection
        print("Connection status:")
        with engine.connect() as conn:
            # Get PostgreSQL version
            result = conn.execute(text("SELECT version()"))
            version = result.scalar()
            print(f"✓ Connected to PostgreSQL")
            print(f"  Version: {version.split(',')[0]}")
            print()

            # Check pgvector extension
            print("Extensions:")
            result = conn.execute(text(
                """
                SELECT extname, extversion
                FROM pg_extension
                WHERE extname = 'vector'
                """
            ))
            vector_ext = result.fetchone()
            if vector_ext:
                print(f"✓ pgvector extension: v{vector_ext[1]}")
            else:
                print("✗ pgvector extension: NOT INSTALLED")
            print()

            # List tables
            print("Tables:")
            result = conn.execute(text(
                """
                SELECT tablename
                FROM pg_tables
                WHERE schemaname = 'public'
                ORDER BY tablename
                """
            ))
            tables = [row[0] for row in result]

            if tables:
                print(f"✓ Found {len(tables)} tables:")
                for table in tables:
                    # Get row count
                    count_result = conn.execute(text(f"SELECT COUNT(*) FROM {table}"))
                    count = count_result.scalar()
                    print(f"  - {table}: {count:,} rows")
            else:
                print("⚠ No tables found (database not initialized)")
            print()

            # Check indexes
            print("Vector indexes:")
            result = conn.execute(text(
                """
                SELECT
                    schemaname,
                    tablename,
                    indexname
                FROM pg_indexes
                WHERE indexname LIKE '%embedding%hnsw%'
                ORDER BY tablename, indexname
                """
            ))
            indexes = result.fetchall()
            if indexes:
                print(f"✓ Found {len(indexes)} vector indexes:")
                for schema, table, index in indexes:
                    print(f"  - {table}.{index}")
            else:
                print("⚠ No vector indexes found")
            print()

        print("=" * 80)
        print("DATABASE STATUS: OK")
        print("=" * 80)

    except Exception as e:
        print()
        print("=" * 80)
        print("ERROR: Cannot connect to database")
        print("=" * 80)
        print(f"Error: {e}")
        print()
        print("Troubleshooting:")
        print("1. Ensure PostgreSQL is running (docker compose up -d)")
        print("2. Check DATABASE_URL in .env file")
        print("3. Verify database credentials")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Database initialization CLI for Innovation Intelligence",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Initialize database
  init-db run

  # Drop and recreate all tables (dangerous!)
  init-db run --drop-existing --yes

  # Check database status
  init-db status

  # Run database migration (GCS-first)
  init-db migrate

  # Check if migration is safe to run
  init-db migrate --check-only
        """,
    )
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # Run command
    run_parser = subparsers.add_parser(
        "run",
        help="Initialize database (create tables and extensions)",
    )
    run_parser.add_argument(
        "--drop-existing",
        action="store_true",
        help="Drop all existing tables first (DANGEROUS: deletes all data)",
    )
    run_parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip confirmation prompt when dropping tables",
    )
    run_parser.set_defaults(func=cmd_run)

    # Status command
    status_parser = subparsers.add_parser(
        "status",
        help="Check database status",
    )
    status_parser.set_defaults(func=cmd_status)

    # Migrate command
    migrate_parser = subparsers.add_parser(
        "migrate",
        help="Run database migrations (GCS-first storage)",
    )
    migrate_parser.add_argument(
        "--check-only",
        action="store_true",
        help="Only check if migration is safe to run, don't apply changes",
    )
    migrate_parser.add_argument(
        "--force",
        action="store_true",
        help="Force migration even if GCS URIs are missing (NOT RECOMMENDED)",
    )
    migrate_parser.set_defaults(func=cmd_migrate)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()

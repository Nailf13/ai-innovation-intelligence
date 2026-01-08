#!/usr/bin/env python3
# src/innovation_intelligence/cli/index_vectors.py
"""
CLI entrypoint for vector indexing.

Usage:
    python -m innovation_intelligence.cli.index_vectors --help
    python -m innovation_intelligence.cli.index_vectors run
    python -m innovation_intelligence.cli.index_vectors run --podcasts-only
    python -m innovation_intelligence.cli.index_vectors run --documents-only
    python -m innovation_intelligence.cli.index_vectors search "your query here"
    python -m innovation_intelligence.cli.index_vectors stats
"""
import argparse
import json
import sys
from pathlib import Path

from innovation_intelligence.config import settings


def cmd_run(args):
    """Run vector indexing."""
    from innovation_intelligence.ingestion.vector_indexing.pipeline import (
        index_podcasts,
        index_documents,
        run_full_ingestion,
    )

    podcasts_dir = Path(args.podcasts_dir) if args.podcasts_dir else None
    documents_dir = Path(args.documents_dir) if args.documents_dir else None

    if args.podcasts_only:
        print("Indexing podcasts only...")
        stats = index_podcasts(podcasts_dir, not args.no_speaker_id)
    elif args.documents_only:
        print("Indexing documents only...")
        stats = index_documents(documents_dir)
    else:
        print("Running full ingestion...")
        stats = run_full_ingestion(
            podcasts_dir=podcasts_dir,
            documents_dir=documents_dir,
            run_speaker_identification=not args.no_speaker_id,
            run_migration_first=not args.skip_migration,
        )

    print("\n" + "="*60)
    print(str(stats))
    print("="*60)

    if stats.errors:
        print("\nErrors encountered:")
        for error in stats.errors:
            print(f"  - {error}")
        sys.exit(1)


def cmd_search(args):
    """Search the vector index."""
    from innovation_intelligence.search.vector_search import search_chunks

    print(f"Searching for: {args.query}")
    print("-" * 60)

    results = search_chunks(
        query=args.query,
        top_k=args.top_k,
        include_podcasts=not args.documents_only,
        include_documents=not args.podcasts_only,
    )

    if not results:
        print("No results found.")
        return

    for i, r in enumerate(results, 1):
        print(f"\n[{i}] Score: {r.score:.4f}")
        print(f"    Source: {r.source}")

        # Format metadata
        meta_parts = []
        if "speaker" in r.metadata and r.metadata["speaker"]:
            meta_parts.append(f"Speaker: {r.metadata['speaker']}")
        if "start" in r.metadata and r.metadata["start"]:
            meta_parts.append(f"Time: {r.metadata['start']:.1f}s - {r.metadata.get('end', 0):.1f}s")
        if "page" in r.metadata and r.metadata["page"]:
            meta_parts.append(f"Page: {r.metadata['page']}")
        if "section" in r.metadata and r.metadata["section"]:
            meta_parts.append(f"Section: {r.metadata['section']}")

        if meta_parts:
            print(f"    {' | '.join(meta_parts)}")

        # Text preview
        text_preview = r.text[:200].replace("\n", " ")
        print(f"    Text: {text_preview}...")


def cmd_stats(args):
    """Show index statistics."""
    from innovation_intelligence.search.vector_search import VectorSearchService

    with VectorSearchService() as service:
        stats = service.get_stats()

    print("\nVector Index Statistics")
    print("=" * 40)
    print(f"Podcast chunks:  {stats['podcast_chunks']:,}")
    print(f"Document chunks: {stats['document_chunks']:,}")
    print(f"Total chunks:    {stats['podcast_chunks'] + stats['document_chunks']:,}")


def cmd_migrate(args):
    """Run database migration."""
    from innovation_intelligence.db.migrations.create_vector_tables import (
        run_migration,
        reset_migration,
        verify_tables,
    )

    if args.reset:
        print("Resetting vector tables (drop + create)...")
        reset_migration()
    elif args.verify:
        print("Verifying vector tables...")
        verify_tables()
    else:
        print("Running migration...")
        run_migration()

    print("Done.")


def main():
    parser = argparse.ArgumentParser(
        description="Vector indexing CLI for Innovation Intelligence",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # Run command
    run_parser = subparsers.add_parser("run", help="Run vector indexing")
    run_parser.add_argument(
        "--podcasts-only",
        action="store_true",
        help="Index only podcasts",
    )
    run_parser.add_argument(
        "--documents-only",
        action="store_true",
        help="Index only documents",
    )
    run_parser.add_argument(
        "--no-speaker-id",
        action="store_true",
        help="Skip speaker identification",
    )
    run_parser.add_argument(
        "--skip-migration",
        action="store_true",
        help="Skip database migration",
    )
    run_parser.add_argument(
        "--podcasts-dir",
        type=str,
        help="Custom podcasts directory",
    )
    run_parser.add_argument(
        "--documents-dir",
        type=str,
        help="Custom documents directory",
    )
    run_parser.set_defaults(func=cmd_run)

    # Search command
    search_parser = subparsers.add_parser("search", help="Search the vector index")
    search_parser.add_argument("query", type=str, help="Search query")
    search_parser.add_argument(
        "--top-k",
        type=int,
        default=5,
        help="Number of results (default: 5)",
    )
    search_parser.add_argument(
        "--podcasts-only",
        action="store_true",
        help="Search only podcasts",
    )
    search_parser.add_argument(
        "--documents-only",
        action="store_true",
        help="Search only documents",
    )
    search_parser.set_defaults(func=cmd_search)

    # Stats command
    stats_parser = subparsers.add_parser("stats", help="Show index statistics")
    stats_parser.set_defaults(func=cmd_stats)

    # Migrate command
    migrate_parser = subparsers.add_parser("migrate", help="Database migration")
    migrate_parser.add_argument(
        "--reset",
        action="store_true",
        help="Drop and recreate tables",
    )
    migrate_parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify tables exist",
    )
    migrate_parser.set_defaults(func=cmd_migrate)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()

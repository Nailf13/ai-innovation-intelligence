#!/usr/bin/env python3
# src/innovation_intelligence/cli/assess_dimensions.py
"""
CLI entrypoint for dimension assessment.

Usage:
    python -m innovation_intelligence.cli.assess_dimensions --help
    python -m innovation_intelligence.cli.assess_dimensions run              # Assess all insights without dimensions
    python -m innovation_intelligence.cli.assess_dimensions run --insight-id 123  # Assess specific insight
    python -m innovation_intelligence.cli.assess_dimensions run --force      # Re-assess all insights
    python -m innovation_intelligence.cli.assess_dimensions stats            # Show assessment stats
    python -m innovation_intelligence.cli.assess_dimensions migrate          # Run dimension table migration
"""
import argparse
import json
import sys
from typing import Optional

from innovation_intelligence.config import settings
from innovation_intelligence.logger import get_logger

log = get_logger(__name__)


def cmd_run(args):
    """Run dimension assessment."""
    from innovation_intelligence.analysis.dimensions import (
        DimensionAssessmentService,
        AssessmentConfig,
        RAGEngineConfig,
    )
    from innovation_intelligence.db.models import UnitInsight
    from innovation_intelligence.db.session import SessionLocal

    session = SessionLocal()

    try:
        # Configure assessment
        rag_config = RAGEngineConfig(
            top_k=args.top_k,
            min_similarity=args.min_similarity,
            include_podcasts=not args.documents_only,
            include_documents=not args.podcasts_only,
        )

        config = AssessmentConfig(
            assess_adoption=not args.skip_adoption,
            assess_expectation=not args.skip_expectation,
            assess_progress=not args.skip_progress,
            rag_config=rag_config,
            persist_results=not args.dry_run,
            skip_existing=not args.force,
            period=args.period,
        )

        service = DimensionAssessmentService(session, config)

        if args.insight_id:
            # Assess specific insight
            insight = session.query(UnitInsight).get(args.insight_id)
            if not insight:
                print(f"Insight with ID {args.insight_id} not found.")
                sys.exit(1)

            print(f"Assessing dimensions for: {insight.name[:60]}...")
            result = service.assess_insight(insight)
            _print_result(result)

        else:
            # Assess all new insights
            print("Finding insights needing dimension assessment...")
            results = service.assess_new_insights(source_filter=args.source)

            print(f"\nCompleted assessment for {len(results)} insights")

            if args.verbose:
                for result in results:
                    _print_result(result)
                    print("-" * 60)

            # Summary
            total_dims = sum(
                (1 if r.adoption else 0) + (1 if r.expectation else 0) + (1 if r.progress else 0)
                for r in results
            )
            print(f"\nTotal dimensions assessed: {total_dims}")

    finally:
        session.close()


def _print_result(result):
    """Print a single assessment result."""
    print(f"\n  {result.insight_name[:60]}")

    if result.adoption:
        print(f"    Adoption:    {result.adoption.value}")
        if result.adoption.confidence:
            print(f"                 (confidence: {result.adoption.confidence:.2f})")

    if result.expectation:
        print(f"    Expectation: {result.expectation.value}")
        if result.expectation.confidence:
            print(f"                 (confidence: {result.expectation.confidence:.2f})")

    if result.progress:
        print(f"    Progress:    {result.progress.value}")
        if result.progress.confidence:
            print(f"                 (confidence: {result.progress.confidence:.2f})")


def cmd_stats(args):
    """Show dimension assessment statistics."""
    from innovation_intelligence.db.models import UnitInsight, InsightDimension
    from innovation_intelligence.db.session import SessionLocal
    from sqlalchemy import func

    session = SessionLocal()

    try:
        # Total insights
        total_insights = session.query(UnitInsight).count()

        # Insights with dimensions
        insights_with_dims = (
            session.query(InsightDimension.unit_insight_id)
            .distinct()
            .count()
        )

        # Dimensions by type
        dim_counts = (
            session.query(
                InsightDimension.dimension_type,
                func.count(InsightDimension.id)
            )
            .group_by(InsightDimension.dimension_type)
            .all()
        )

        # RAG stats
        from innovation_intelligence.analysis.dimensions import get_rag_engine
        rag_engine = get_rag_engine(session)
        rag_stats = rag_engine.get_stats()

        print("\nDimension Assessment Statistics")
        print("=" * 50)
        print(f"Total insights:          {total_insights:,}")
        print(f"Insights with dimensions: {insights_with_dims:,}")
        print(f"Pending assessment:      {total_insights - insights_with_dims:,}")

        print("\nDimensions by type:")
        for dim_type, count in dim_counts:
            print(f"  {dim_type:12s}: {count:,}")

        print("\nVector Index (for RAG):")
        print(f"  Podcast chunks:  {rag_stats['podcast_chunks']:,}")
        print(f"  Document chunks: {rag_stats['document_chunks']:,}")

    finally:
        session.close()


def cmd_export(args):
    """Export dimensions to JSON."""
    from innovation_intelligence.db.models import UnitInsight, InsightDimension, DimensionEvidence
    from innovation_intelligence.db.session import SessionLocal

    session = SessionLocal()

    try:
        # Query insights with dimensions
        query = session.query(UnitInsight).join(InsightDimension)

        if args.insight_id:
            query = query.filter(UnitInsight.id == args.insight_id)

        insights = query.distinct().all()

        output = {
            "period": args.period or settings.aws.period_name,
            "total_insights": len(insights),
            "insights": [],
        }

        for insight in insights:
            insight_data = {
                "id": insight.id,
                "name": insight.name,
                "description": insight.description,
                "type": insight.type,
                "dimensions": {},
            }

            for dim in insight.dimensions:
                evidence_list = [
                    {
                        "text": ev.chunk_text[:500],
                        "source": ev.source_ref,
                        "similarity": ev.similarity_score,
                    }
                    for ev in dim.evidence_chunks
                ]

                if dim.dimension_type == "adoption":
                    insight_data["dimensions"]["adoption"] = {
                        "stage": dim.value,
                        "confidence": dim.confidence,
                        "evidence": evidence_list,
                    }
                elif dim.dimension_type == "expectation":
                    insight_data["dimensions"]["expectation"] = {
                        "level": dim.value,
                        "confidence": dim.confidence,
                        "evidence": evidence_list,
                    }
                elif dim.dimension_type == "progress":
                    insight_data["dimensions"]["progress"] = {
                        "horizon": dim.value,
                        "confidence": dim.confidence,
                        "evidence": evidence_list,
                    }

            output["insights"].append(insight_data)

        # Output
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                json.dump(output, f, ensure_ascii=False, indent=2)
            print(f"Exported {len(insights)} insights to {args.output}")
        else:
            print(json.dumps(output, ensure_ascii=False, indent=2))

    finally:
        session.close()


def cmd_migrate(args):
    """Run database migration for dimension tables."""
    from innovation_intelligence.db.migrations.create_dimension_tables import (
        run_migration,
        reset_migration,
        verify_tables,
    )

    if args.reset:
        print("Resetting dimension tables (drop + create)...")
        reset_migration()
    elif args.verify:
        print("Verifying dimension tables...")
        verify_tables()
    else:
        print("Running dimension tables migration...")
        run_migration()

    print("Done.")


def main():
    parser = argparse.ArgumentParser(
        description="Dimension assessment CLI for Innovation Intelligence",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Assess all new insights
    assess-dimensions run

    # Assess specific insight
    assess-dimensions run --insight-id 123

    # Re-assess all insights (force)
    assess-dimensions run --force

    # Skip specific dimensions
    assess-dimensions run --skip-progress

    # Dry run (don't save)
    assess-dimensions run --dry-run

    # Export to JSON
    assess-dimensions export -o dimensions.json
        """,
    )
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # Run command
    run_parser = subparsers.add_parser("run", help="Run dimension assessment")
    run_parser.add_argument(
        "--insight-id",
        type=int,
        help="Assess specific insight by ID",
    )
    run_parser.add_argument(
        "--source",
        type=str,
        help="Filter by source identifier",
    )
    run_parser.add_argument(
        "--force",
        action="store_true",
        help="Re-assess even if dimensions exist",
    )
    run_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Don't persist results to database",
    )
    run_parser.add_argument(
        "--skip-adoption",
        action="store_true",
        help="Skip adoption dimension",
    )
    run_parser.add_argument(
        "--skip-expectation",
        action="store_true",
        help="Skip expectation dimension",
    )
    run_parser.add_argument(
        "--skip-progress",
        action="store_true",
        help="Skip progress dimension",
    )
    run_parser.add_argument(
        "--top-k",
        type=int,
        default=10,
        help="Number of chunks to retrieve (default: 10)",
    )
    run_parser.add_argument(
        "--min-similarity",
        type=float,
        default=0.3,
        help="Minimum similarity threshold (default: 0.3)",
    )
    run_parser.add_argument(
        "--podcasts-only",
        action="store_true",
        help="Only use podcast chunks for RAG",
    )
    run_parser.add_argument(
        "--documents-only",
        action="store_true",
        help="Only use document chunks for RAG",
    )
    run_parser.add_argument(
        "--period",
        type=str,
        default=None,
        help="Analysis period label (e.g., '2025')",
    )
    run_parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Print detailed results",
    )
    run_parser.set_defaults(func=cmd_run)

    # Stats command
    stats_parser = subparsers.add_parser("stats", help="Show assessment statistics")
    stats_parser.set_defaults(func=cmd_stats)

    # Export command
    export_parser = subparsers.add_parser("export", help="Export dimensions to JSON")
    export_parser.add_argument(
        "--output", "-o",
        type=str,
        help="Output file path",
    )
    export_parser.add_argument(
        "--insight-id",
        type=int,
        help="Export specific insight",
    )
    export_parser.add_argument(
        "--period",
        type=str,
        default=None,
        help="Period label for export",
    )
    export_parser.set_defaults(func=cmd_export)

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

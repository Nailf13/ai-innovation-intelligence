#!/usr/bin/env python3
# src/innovation_intelligence/cli/analyze.py
"""
CLI entrypoint for the full analysis pipeline.

Usage:
    python -m innovation_intelligence.cli.analyze run               # Full pipeline
    python -m innovation_intelligence.cli.analyze run --skip-extraction  # From existing insights
    python -m innovation_intelligence.cli.analyze run --clustering-only  # Only clustering stages
    python -m innovation_intelligence.cli.analyze stats             # Show statistics
"""
import argparse
import json
import sys
from pathlib import Path

from innovation_intelligence.config import settings
from innovation_intelligence.logger import get_logger

log = get_logger(__name__)


def cmd_run(args):
    """Run the analysis pipeline."""
    from innovation_intelligence.analysis.pipelines.batch_analysis import (
        run_full_analysis,
        run_analysis_from_insights,
        run_clustering_only,
        AnalysisPipelineConfig,
    )
    from innovation_intelligence.db.session import SessionLocal

    session = SessionLocal()

    try:
        # Build config
        config = AnalysisPipelineConfig(
            run_extraction=not args.skip_extraction and not args.clustering_only,
            run_dimension_assessment=not args.skip_dimensions and not args.clustering_only,
            run_macro_discovery=not args.skip_macro,
            run_strategic_clustering=not args.skip_clustering,
            force_reextract=args.force,
            macro_similarity_threshold=args.macro_threshold,
            cluster_similarity_threshold=args.cluster_threshold,
            use_llm_naming=not args.no_llm_naming,
            generate_macro_descriptions=args.generate_descriptions,
            continue_on_error=not args.fail_fast,
        )

        # Determine transcripts directory
        transcripts_dir = None
        if args.transcripts_dir:
            transcripts_dir = Path(args.transcripts_dir)

        print("=" * 60)
        print("INNOVATION INTELLIGENCE - ANALYSIS PIPELINE")
        print("=" * 60)

        # Show what will run
        stages = []
        if config.run_extraction:
            stages.append("1. Insight Extraction")
        if config.run_dimension_assessment:
            stages.append("2. Dimension Assessment")
        if config.run_macro_discovery:
            stages.append("3. Macro Insight Discovery")
        if config.run_strategic_clustering:
            stages.append("4. Strategic Clustering")

        print(f"Stages to run: {', '.join(stages) or 'None'}")
        print("-" * 60)

        if args.clustering_only:
            print("Running clustering stages only...")
            result = run_clustering_only(
                session,
                macro_similarity_threshold=args.macro_threshold,
                cluster_similarity_threshold=args.cluster_threshold,
            )
        elif args.skip_extraction:
            print("Running from existing insights...")
            result = run_analysis_from_insights(session, config=config)
        else:
            print("Running full pipeline...")
            result = run_full_analysis(
                session,
                transcripts_dir=transcripts_dir,
                config=config,
            )

        # Print summary
        print("\n" + result.summary())

        # Exit with error if failed
        if not result.success:
            sys.exit(1)

    finally:
        session.close()


def cmd_stats(args):
    """Show pipeline statistics."""
    from innovation_intelligence.analysis.pipelines.batch_analysis import (
        get_pipeline_statistics,
    )
    from innovation_intelligence.db.session import SessionLocal

    session = SessionLocal()

    try:
        stats = get_pipeline_statistics(session)

        print("\nPIPELINE STATISTICS")
        print("=" * 50)

        # Sources
        print("\nSources:")
        print(f"  Podcast Episodes: {stats['sources']['podcast_episodes']:,}")
        print(f"  Documents:        {stats['sources']['documents']:,}")

        # Unit Insights
        print("\nUnit Insights:")
        print(f"  Total:            {stats['unit_insights']['total']:,}")
        print(f"  With Macro:       {stats['unit_insights']['with_macro']:,}")
        print(f"  Without Macro:    {stats['unit_insights']['without_macro']:,}")

        # Dimensions
        print("\nDimensions:")
        print(f"  Total:            {stats['dimensions']['total']:,}")
        if stats['dimensions']['by_type']:
            for dim_type, count in stats['dimensions']['by_type'].items():
                print(f"    {dim_type:15s}: {count:,}")

        # Macro Insights
        print("\nMacro Insights:")
        print(f"  Total:            {stats['macro_insights']['total']:,}")
        print(f"  With Cluster:     {stats['macro_insights']['with_cluster']:,}")
        print(f"  Without Cluster:  {stats['macro_insights']['without_cluster']:,}")

        # Strategic Clusters
        print("\nStrategic Clusters:")
        print(f"  Total:            {stats['clusters']['total']:,}")
        if stats['clusters']['by_name']:
            print("  Distribution:")
            for name, count in sorted(
                stats['clusters']['by_name'].items(),
                key=lambda x: x[1],
                reverse=True
            ):
                print(f"    {name[:35]:35s}: {count:,}")

        # Export to JSON if requested
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                json.dump(stats, f, indent=2, default=str)
            print(f"\nExported to {args.output}")

    finally:
        session.close()


def cmd_reset(args):
    """Reset pipeline entities (dangerous!)."""
    from innovation_intelligence.db.session import SessionLocal
    from innovation_intelligence.db.models import (
        UnitInsight,
        MacroInsight,
        Cluster,
        InsightDimension,
        DimensionEvidence,
    )

    if not args.confirm:
        print("This will DELETE all analysis results!")
        print("Use --confirm to proceed.")
        sys.exit(1)

    session = SessionLocal()

    try:
        # Delete in order to respect foreign keys
        what_to_reset = []

        if args.all or args.dimensions:
            what_to_reset.extend(["dimensions", "evidence"])
        if args.all or args.clustering:
            what_to_reset.extend(["clusters", "macro_insights"])
        if args.all or args.insights:
            what_to_reset.append("unit_insights")

        counts = {}

        if "evidence" in what_to_reset:
            counts["evidence"] = session.query(DimensionEvidence).delete()
        if "dimensions" in what_to_reset:
            counts["dimensions"] = session.query(InsightDimension).delete()
        if "macro_insights" in what_to_reset:
            # Clear cluster assignments first
            session.query(MacroInsight).update({MacroInsight.cluster_id: None})
            counts["macro_insights"] = session.query(MacroInsight).delete()
        if "clusters" in what_to_reset:
            counts["clusters"] = session.query(Cluster).delete()
        if "unit_insights" in what_to_reset:
            # Clear macro assignments first
            session.query(UnitInsight).update({UnitInsight.macro_insight_id: None})
            counts["unit_insights"] = session.query(UnitInsight).delete()

        session.commit()

        print("Reset complete:")
        for entity, count in counts.items():
            print(f"  Deleted {count:,} {entity}")

    finally:
        session.close()


def main():
    parser = argparse.ArgumentParser(
        description="Analysis pipeline CLI for Innovation Intelligence",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Run full pipeline
    analyze run

    # Run from existing insights (skip extraction)
    analyze run --skip-extraction

    # Only run clustering stages
    analyze run --clustering-only

    # Adjust thresholds
    analyze run --macro-threshold 0.70 --cluster-threshold 0.72

    # Show statistics
    analyze stats

    # Export stats to JSON
    analyze stats -o stats.json
        """,
    )
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # Run command
    run_parser = subparsers.add_parser("run", help="Run analysis pipeline")
    run_parser.add_argument(
        "--transcripts-dir",
        type=str,
        help="Directory containing transcript files",
    )
    run_parser.add_argument(
        "--skip-extraction",
        action="store_true",
        help="Skip insight extraction (use existing insights)",
    )
    run_parser.add_argument(
        "--skip-dimensions",
        action="store_true",
        help="Skip dimension assessment",
    )
    run_parser.add_argument(
        "--skip-macro",
        action="store_true",
        help="Skip macro insight discovery",
    )
    run_parser.add_argument(
        "--skip-clustering",
        action="store_true",
        help="Skip strategic clustering",
    )
    run_parser.add_argument(
        "--clustering-only",
        action="store_true",
        help="Only run macro discovery and strategic clustering",
    )
    run_parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-extraction even if insights exist",
    )
    run_parser.add_argument(
        "--macro-threshold",
        type=float,
        default=0.72,
        help="Similarity threshold for macro insight clustering (default: 0.72)",
    )
    run_parser.add_argument(
        "--cluster-threshold",
        type=float,
        default=0.75,
        help="Similarity threshold for strategic clustering (default: 0.75)",
    )
    run_parser.add_argument(
        "--no-llm-naming",
        action="store_true",
        help="Don't use LLM to generate macro insight names",
    )
    run_parser.add_argument(
        "--generate-descriptions",
        action="store_true",
        help="Generate descriptions for macro insights via LLM",
    )
    run_parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop on first error",
    )
    run_parser.set_defaults(func=cmd_run)

    # Stats command
    stats_parser = subparsers.add_parser("stats", help="Show pipeline statistics")
    stats_parser.add_argument(
        "--output", "-o",
        type=str,
        help="Export stats to JSON file",
    )
    stats_parser.set_defaults(func=cmd_stats)

    # Reset command
    reset_parser = subparsers.add_parser("reset", help="Reset pipeline entities")
    reset_parser.add_argument(
        "--confirm",
        action="store_true",
        help="Confirm deletion",
    )
    reset_parser.add_argument(
        "--all",
        action="store_true",
        help="Reset everything",
    )
    reset_parser.add_argument(
        "--dimensions",
        action="store_true",
        help="Reset dimensions only",
    )
    reset_parser.add_argument(
        "--clustering",
        action="store_true",
        help="Reset clustering only (macro insights + clusters)",
    )
    reset_parser.add_argument(
        "--insights",
        action="store_true",
        help="Reset unit insights only",
    )
    reset_parser.set_defaults(func=cmd_reset)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()

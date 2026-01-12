#!/usr/bin/env python3
# src/innovation_intelligence/cli/ingest_podcasts.py
"""
CLI entrypoint for podcast ingestion.

Full pipeline:
1. Search for podcasts
2. Download audio to GCS
3. Transcribe with Google Speech API (Chirp)
4. Persist transcript in GCS bucket
5. Persist metadata in PostgreSQL
6. Index vectors in pgvector

Usage:
    ingest-podcasts run "Huberman Lab" --max-episodes 5
    ingest-podcasts run "Huberman Lab" --max-episodes 3 --language fr-FR
    ingest-podcasts run "Huberman Lab" --skip-indexing
    ingest-podcasts run "Huberman Lab" --keywords "health,nutrition"
    ingest-podcasts list "Huberman Lab" --max-results 10
"""
import argparse
import sys
from typing import List, Optional

from innovation_intelligence.config import settings
from innovation_intelligence.logger import get_logger

log = get_logger(__name__)


def cmd_run(args):
    """Run podcast ingestion pipeline."""
    from innovation_intelligence.ingestion.podcasts.pipeline import (
        PodcastIngestionPipeline,
    )

    # Parse keywords
    keywords: Optional[List[str]] = None
    if args.keywords:
        keywords = [k.strip() for k in args.keywords.split(",")]

    # Parse language codes
    language_codes: Optional[List[str]] = None
    if args.language:
        language_codes = [args.language]

    print("=" * 80)
    print(f"PODCAST INGESTION PIPELINE")
    print("=" * 80)
    print(f"Podcast: {args.podcast_name}")
    print(f"Max episodes: {args.max_episodes}")
    print(f"Language: {language_codes[0] if language_codes else 'en-US'}")
    print(f"Keywords: {keywords or 'default health keywords'}")
    print(f"Skip indexing: {args.skip_indexing}")
    print(f"Trim audio: {args.trim_audio} seconds")
    print("=" * 80)
    print()

    # Progress callback for user feedback
    def progress_callback(stage, status, message, progress_pct):
        print(f"[{stage.value.upper()}] {status.value}: {message} ({progress_pct:.0f}%)")

    # Run ingestion
    with PodcastIngestionPipeline(
        language_codes=language_codes,
        progress_callback=progress_callback,
    ) as pipeline:
        result = pipeline.ingest_from_podcast(
            podcast_name=args.podcast_name,
            max_episodes=args.max_episodes,
            relevance_keywords=keywords,
            min_relevance_score=args.min_relevance_score,
            skip_existing=not args.force_reprocess,
            skip_indexing=args.skip_indexing,
            trim_audio_seconds=args.trim_audio,
        )

    # Print summary
    print()
    print("=" * 80)
    print("INGESTION RESULTS")
    print("=" * 80)
    print(str(result))
    print("=" * 80)

    # Print detailed results
    if result.results:
        print()
        print("EPISODE DETAILS:")
        print("-" * 80)
        for i, episode_result in enumerate(result.results, 1):
            print(f"\n[{i}] {episode_result.episode_info.title[:60]}")
            print(f"    Status: {episode_result.status.value}")
            print(f"    DB ID: {episode_result.db_episode_id}")

            if episode_result.gcs_audio_uri:
                print(f"    Audio: {episode_result.gcs_audio_uri}")
            if episode_result.gcs_transcript_uri:
                print(f"    Transcript: {episode_result.gcs_transcript_uri}")
            if episode_result.chunks_indexed:
                print(f"    Chunks indexed: {episode_result.chunks_indexed}")

            if episode_result.error_message:
                print(f"    Error: {episode_result.error_message}")
                print(f"    Error stage: {episode_result.error_stage.value if episode_result.error_stage else 'unknown'}")

            # Duration
            if episode_result.completed_at and episode_result.started_at:
                duration = (episode_result.completed_at - episode_result.started_at).total_seconds()
                print(f"    Duration: {duration:.1f}s")

    # Exit with error if any failed
    if result.failed > 0:
        print()
        print(f"WARNING: {result.failed} episode(s) failed")
        sys.exit(1)


def cmd_list(args):
    """List episodes from a podcast without ingesting."""
    from innovation_intelligence.ingestion.podcasts.services import PodcastSearchService

    print(f"Searching for podcast: {args.podcast_name}")
    print("-" * 80)

    search_service = PodcastSearchService()

    # Search for podcast
    podcast_info, episodes = search_service.discover_relevant_episodes(
        podcast_name=args.podcast_name,
        limit=args.max_results,
        keywords=None,  # No filtering when listing
    )

    if not episodes:
        print("No episodes found.")
        return

    print(f"\nPodcast: {podcast_info.title}")
    print(f"Feed ID: {podcast_info.feed_id}")
    print(f"Episodes found: {len(episodes)}")
    print()

    # Print episodes
    for i, episode in enumerate(episodes, 1):
        print(f"[{i}] {episode.title}")
        print(f"    Published: {episode.published_at.strftime('%Y-%m-%d %H:%M')}")
        print(f"    Duration: {episode.duration}s")
        print(f"    Audio URL: {episode.audio_url[:80]}...")
        if episode.description:
            desc = episode.description[:100].replace("\n", " ")
            print(f"    Description: {desc}...")
        print()


def cmd_stats(args):
    """Show ingestion statistics."""
    from innovation_intelligence.db.session import SessionLocal
    from innovation_intelligence.db.repositories.episode_repository import EpisodeRepository
    from innovation_intelligence.db.repositories.vector_repository import VectorRepository

    session = SessionLocal()

    try:
        episode_repo = EpisodeRepository(session)
        vector_repo = VectorRepository(session)

        # Get counts
        total_episodes = episode_repo.count()
        episodes_with_transcripts = episode_repo.count_with_transcripts()
        total_podcast_chunks = vector_repo.count_podcast_chunks()

        print()
        print("=" * 80)
        print("PODCAST INGESTION STATISTICS")
        print("=" * 80)
        print(f"Total episodes: {total_episodes}")
        print(f"Episodes with transcripts: {episodes_with_transcripts}")
        print(f"Podcast chunks in vector DB: {total_podcast_chunks:,}")
        print("=" * 80)

    finally:
        session.close()


def main():
    parser = argparse.ArgumentParser(
        description="Podcast ingestion CLI for Innovation Intelligence",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Ingest 5 episodes from Huberman Lab
  ingest-podcasts run "Huberman Lab" --max-episodes 5

  # Ingest French podcast
  ingest-podcasts run "French Podcast" --language fr-FR --max-episodes 3

  # Filter by keywords
  ingest-podcasts run "The Peter Attia Drive" --keywords "longevity,health"

  # List episodes without ingesting
  ingest-podcasts list "Huberman Lab" --max-results 10

  # View statistics
  ingest-podcasts stats
        """,
    )
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # Run command
    run_parser = subparsers.add_parser(
        "run",
        help="Run podcast ingestion pipeline",
    )
    run_parser.add_argument(
        "podcast_name",
        type=str,
        help="Name of the podcast to search for",
    )
    run_parser.add_argument(
        "--max-episodes",
        type=int,
        default=5,
        help="Maximum number of episodes to ingest (default: 5)",
    )
    run_parser.add_argument(
        "--language",
        type=str,
        default="en-US",
        help="Language code for transcription (default: en-US). Examples: en-US, fr-FR, es-ES",
    )
    run_parser.add_argument(
        "--keywords",
        type=str,
        help="Comma-separated relevance keywords for filtering episodes",
    )
    run_parser.add_argument(
        "--min-relevance-score",
        type=float,
        default=0.0,
        help="Minimum relevance score (0.0-1.0) (default: 0.0)",
    )
    run_parser.add_argument(
        "--skip-indexing",
        action="store_true",
        help="Skip vector indexing stage",
    )
    run_parser.add_argument(
        "--force-reprocess",
        action="store_true",
        help="Force reprocessing of existing episodes",
    )
    run_parser.add_argument(
        "--trim-audio",
        type=int,
        default=0,
        help="Trim N seconds from audio start (default: 0)",
    )
    run_parser.set_defaults(func=cmd_run)

    # List command
    list_parser = subparsers.add_parser(
        "list",
        help="List episodes from a podcast without ingesting",
    )
    list_parser.add_argument(
        "podcast_name",
        type=str,
        help="Name of the podcast to search for",
    )
    list_parser.add_argument(
        "--max-results",
        type=int,
        default=10,
        help="Maximum number of episodes to list (default: 10)",
    )
    list_parser.set_defaults(func=cmd_list)

    # Stats command
    stats_parser = subparsers.add_parser(
        "stats",
        help="Show ingestion statistics",
    )
    stats_parser.set_defaults(func=cmd_stats)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()

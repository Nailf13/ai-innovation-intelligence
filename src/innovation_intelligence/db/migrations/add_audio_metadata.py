# src/innovation_intelligence/db/migrations/add_audio_metadata.py
"""
Database migration: add audio metadata columns to podcast_episodes.

Adds:
- audio_duration_seconds (FLOAT, nullable) — duration in seconds
- audio_file_size (BIGINT, nullable) — file size in bytes

These columns enable instant audio seeking by letting the frontend
calculate byte offsets without downloading the file first.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from innovation_intelligence.db.session import engine
from innovation_intelligence.logger import get_logger

log = get_logger(__name__)


def run_migration() -> None:
    """Add audio_duration_seconds and audio_file_size columns."""
    log.info("[MIGRATION] Adding audio metadata columns to podcast_episodes...")

    with engine.begin() as conn:
        # Add audio_duration_seconds if not exists
        try:
            conn.execute(text("""
                ALTER TABLE podcast_episodes
                ADD COLUMN IF NOT EXISTS audio_duration_seconds FLOAT
            """))
            log.info("[MIGRATION] Added audio_duration_seconds column")
        except ProgrammingError:
            log.info("[MIGRATION] audio_duration_seconds column already exists")

        # Add audio_file_size if not exists
        try:
            conn.execute(text("""
                ALTER TABLE podcast_episodes
                ADD COLUMN IF NOT EXISTS audio_file_size BIGINT
            """))
            log.info("[MIGRATION] Added audio_file_size column")
        except ProgrammingError:
            log.info("[MIGRATION] audio_file_size column already exists")

    log.info("[MIGRATION] Audio metadata migration complete")


def verify_columns() -> None:
    """Verify that the new columns exist."""
    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = 'public'
            AND table_name = 'podcast_episodes'
            AND column_name IN ('audio_duration_seconds', 'audio_file_size')
        """))
        columns = {row[0]: row[1] for row in result}

        for col in ('audio_duration_seconds', 'audio_file_size'):
            if col in columns:
                log.info(f"[MIGRATION] {col} exists ({columns[col]})")
            else:
                log.warning(f"[MIGRATION] {col} is MISSING")


def backfill_from_gcs() -> None:
    """
    Backfill audio_file_size for existing episodes from GCS blob metadata.

    This reads the blob size from GCS without downloading the file.
    Duration cannot be determined from GCS metadata alone (requires ffprobe).
    """
    from innovation_intelligence.db.session import SessionLocal
    from innovation_intelligence.db.models import PodcastEpisode
    from innovation_intelligence.ingestion.gcs_service import GCSStorageService

    log.info("[MIGRATION] Backfilling audio_file_size from GCS...")

    gcs = GCSStorageService()
    db = SessionLocal()

    try:
        episodes = db.query(PodcastEpisode).filter(
            PodcastEpisode.audio_file_size.is_(None),
            PodcastEpisode.gcs_audio_uri.isnot(None),
        ).all()

        updated = 0
        for ep in episodes:
            try:
                bucket_name, key = gcs._parse_gcs_uri(ep.gcs_audio_uri)
                blob = gcs.client.bucket(bucket_name).blob(key)
                blob.reload()

                if blob.size:
                    ep.audio_file_size = blob.size
                    updated += 1
                    log.debug(f"[MIGRATION] Episode {ep.id}: {blob.size} bytes")
            except Exception as e:
                log.warning(f"[MIGRATION] Failed to get size for episode {ep.id}: {e}")

        db.commit()
        log.info(f"[MIGRATION] Backfilled {updated}/{len(episodes)} episodes")

    finally:
        db.close()


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        command = sys.argv[1]
        if command == "verify":
            verify_columns()
        elif command == "backfill":
            run_migration()
            backfill_from_gcs()
        else:
            print(f"Unknown command: {command}")
            print("Usage: python add_audio_metadata.py [verify|backfill]")
            sys.exit(1)
    else:
        run_migration()
        verify_columns()

"""
End-to-end vector indexing pipeline.

Orchestrates:
1. Speaker identification for podcasts
2. Transcript transformation
3. Chunking (podcasts and documents)
4. Embedding
5. Storage in pgvector

Supports both:
- Batch processing (folder discovery)
- Single document upload (API-ready)
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO, Dict, List, Optional, Union

from innovation_intelligence.config import settings
from innovation_intelligence.logger import get_logger

# Speaker identification
from innovation_intelligence.llm.tools.speaker_identification_tool import (
    identify_speakers_from_file,
)

# Transcript transformation
from innovation_intelligence.ingestion.podcasts.transcript_transformer import (
    transform_transcript,
    TransformedTranscript,
)

# Chunking
from innovation_intelligence.chunking.podcast_chunker import (
    PodcastChunker,
    PodcastChunk,
)
from innovation_intelligence.chunking.document_chunker import (
    DocumentChunker,
    DocumentChunk,
)

# Embedding
from innovation_intelligence.analysis.insights.embedder import embed_texts_batch

# Storage
from innovation_intelligence.db.session import SessionLocal
from innovation_intelligence.db.repositories.vector_repository import VectorRepository
from innovation_intelligence.db.migrations.create_vector_tables import run_migration

# Document service
from innovation_intelligence.ingestion.documents.document_service import (
    DocumentService,
    DocumentUploadResult,
)

log = get_logger(__name__)


@dataclass
class IndexingStats:
    """Statistics from an indexing run."""
    podcasts_processed: int = 0
    podcast_chunks_created: int = 0
    podcast_chunks_stored: int = 0
    documents_processed: int = 0
    document_chunks_created: int = 0
    document_chunks_stored: int = 0
    errors: List[str] = field(default_factory=list)

    def __str__(self) -> str:
        return (
            f"Indexing Stats:\n"
            f"  Podcasts: {self.podcasts_processed} processed, "
            f"{self.podcast_chunks_created} chunks created, "
            f"{self.podcast_chunks_stored} stored\n"
            f"  Documents: {self.documents_processed} processed, "
            f"{self.document_chunks_created} chunks created, "
            f"{self.document_chunks_stored} stored\n"
            f"  Errors: {len(self.errors)}"
        )


class VectorIndexingPipeline:
    """
    Pipeline for indexing content into pgvector.

    Handles the full workflow:
    1. Load source files
    2. Transform/chunk content
    3. Generate embeddings
    4. Store in database
    """

    def __init__(
        self,
        run_speaker_identification: bool = True,
        batch_size: int = 50,
        session=None,
    ):
        self.run_speaker_identification = run_speaker_identification
        self.batch_size = batch_size

        # Session management
        self._session = session
        self._owns_session = session is None

        # Chunkers
        self.podcast_chunker = PodcastChunker()
        self.document_chunker = DocumentChunker()

        # Stats
        self.stats = IndexingStats()

    @property
    def session(self):
        if self._session is None:
            self._session = SessionLocal()
        return self._session

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._owns_session and self._session is not None:
            self._session.close()

    # -----------------------------------------------------------------
    # Podcast indexing
    # -----------------------------------------------------------------
    def index_podcast_episode(
        self,
        transcript_path: Path,
        episode_title: Optional[str] = None,
        episode_date: Optional[str] = None,
        speaker_map: Optional[Dict[str, str]] = None,
    ) -> int:
        """
        Index a single podcast episode.

        Args:
            transcript_path: Path to transcript JSON
            episode_title: Episode title (uses filename if not provided)
            episode_date: Episode publication date
            speaker_map: Pre-computed speaker mapping (skips identification)

        Returns:
            Number of chunks indexed
        """
        transcript_path = Path(transcript_path)
        source = transcript_path.stem
        episode_title = episode_title or source

        log.info(f"[INDEX] Processing podcast: {episode_title}")

        try:
            # Step 1: Transform transcript (with speaker identification)
            transformed = transform_transcript(
                transcript_path,
                episode_title,
                speaker_map=speaker_map,
                run_identification=self.run_speaker_identification and speaker_map is None,
            )

            if not transformed.segments:
                log.warning(f"[INDEX] No segments in {source}")
                return 0

            # Step 2: Chunk the transcript
            chunks = self.podcast_chunker.chunk(
                segments=transformed.segments,
                source=source,
                episode_date=episode_date,
                metadata={
                    "episode_title": episode_title,
                    "speaker_map": transformed.speaker_map,
                },
            )

            if not chunks:
                log.warning(f"[INDEX] No chunks created for {source}")
                return 0

            self.stats.podcast_chunks_created += len(chunks)

            # Step 3: Embed chunks
            log.info(f"[INDEX] Embedding {len(chunks)} podcast chunks")
            texts_to_embed = [chunk.full_text for chunk in chunks]
            embeddings = embed_texts_batch(texts_to_embed)

            # Step 4: Store in database
            repo = VectorRepository(self.session)

            # Delete existing chunks for this source (re-indexing)
            repo.delete_podcast_chunks_by_source(source)

            stored = repo.store_podcast_chunks_batch(chunks, embeddings)
            self.stats.podcast_chunks_stored += stored
            self.stats.podcasts_processed += 1

            log.info(f"[INDEX] Indexed {stored} chunks for {source}")
            return stored

        except Exception as e:
            error_msg = f"Failed to index {source}: {e}"
            log.error(f"[INDEX] {error_msg}")
            self.stats.errors.append(error_msg)
            return 0

    def index_podcast_directory(
        self,
        transcripts_dir: Path,
        pattern: str = "*.json",
    ) -> int:
        """
        Index all podcasts in a directory.

        Args:
            transcripts_dir: Directory containing transcript JSONs
            pattern: Glob pattern for files

        Returns:
            Total chunks indexed
        """
        transcripts_dir = Path(transcripts_dir)
        transcript_files = sorted(transcripts_dir.glob(pattern))

        log.info(f"[INDEX] Found {len(transcript_files)} podcast transcripts in {transcripts_dir}")

        total_chunks = 0
        for i, file_path in enumerate(transcript_files, 1):
            log.info(f"[INDEX] Processing podcast {i}/{len(transcript_files)}: {file_path.name}")
            chunks = self.index_podcast_episode(file_path)
            total_chunks += chunks

        return total_chunks

    # -----------------------------------------------------------------
    # Document indexing
    # -----------------------------------------------------------------
    def index_document(
        self,
        document_path: Path,
        source: Optional[str] = None,
        document_date: Optional[str] = None,
    ) -> int:
        """
        Index a single document.

        Args:
            document_path: Path to document (txt file)
            source: Document identifier
            document_date: Document date

        Returns:
            Number of chunks indexed
        """
        document_path = Path(document_path)
        source = source or document_path.stem

        log.info(f"[INDEX] Processing document: {source}")

        try:
            # Step 1: Read document
            with open(document_path, "r", encoding="utf-8") as f:
                text = f.read()

            if not text.strip():
                log.warning(f"[INDEX] Empty document: {source}")
                return 0

            # Step 2: Chunk the document
            chunks = self.document_chunker.chunk(
                text=text,
                source=source,
                document_date=document_date,
            )

            if not chunks:
                log.warning(f"[INDEX] No chunks created for {source}")
                return 0

            self.stats.document_chunks_created += len(chunks)

            # Step 3: Embed chunks
            log.info(f"[INDEX] Embedding {len(chunks)} document chunks")
            texts_to_embed = [chunk.full_text for chunk in chunks]
            embeddings = embed_texts_batch(texts_to_embed)

            # Step 4: Store in database
            repo = VectorRepository(self.session)

            # Delete existing chunks for this source
            repo.delete_document_chunks_by_source(source)

            stored = repo.store_document_chunks_batch(chunks, embeddings)
            self.stats.document_chunks_stored += stored
            self.stats.documents_processed += 1

            log.info(f"[INDEX] Indexed {stored} chunks for {source}")
            return stored

        except Exception as e:
            error_msg = f"Failed to index {source}: {e}"
            log.error(f"[INDEX] {error_msg}")
            self.stats.errors.append(error_msg)
            return 0

    def index_document_directory(
        self,
        documents_dir: Path,
        pattern: str = "*.txt",
    ) -> int:
        """
        Index all documents in a directory.

        Args:
            documents_dir: Directory containing document files
            pattern: Glob pattern for files

        Returns:
            Total chunks indexed
        """
        documents_dir = Path(documents_dir)
        document_files = sorted(documents_dir.glob(pattern))

        log.info(f"[INDEX] Found {len(document_files)} documents in {documents_dir}")

        total_chunks = 0
        for i, file_path in enumerate(document_files, 1):
            log.info(f"[INDEX] Processing document {i}/{len(document_files)}: {file_path.name}")
            chunks = self.index_document(file_path)
            total_chunks += chunks

        return total_chunks

    # -----------------------------------------------------------------
    # Single document upload + indexing (API-ready)
    # -----------------------------------------------------------------
    def ingest_uploaded_document(
        self,
        *,
        file: Optional[BinaryIO] = None,
        file_path: Optional[Union[Path, str]] = None,
        title: str,
        document_date: datetime,
    ) -> DocumentIngestionResult:
        """
        Upload and index a single document (API-ready method).

        This is the primary method for API integration. It handles:
        1. File upload/storage
        2. Text extraction
        3. Chunking
        4. Embedding
        5. Vector storage

        Args:
            file: File-like object (e.g., from FastAPI UploadFile.file)
            file_path: Path to existing file on disk
            title: Document title (required)
            document_date: Document date (required for proper indexing)

        Returns:
            DocumentIngestionResult with full status

        Example (FastAPI):
            ```python
            @app.post("/documents/upload")
            async def upload_document(
                file: UploadFile,
                title: str,
                document_date: datetime,
            ):
                with VectorIndexingPipeline() as pipeline:
                    result = pipeline.ingest_uploaded_document(
                        file=file.file,
                        title=title,
                        document_date=document_date,
                    )
                return result.to_dict()
            ```
        """
        log.info(f"[INGEST] Starting document ingestion: {title}")

        # Step 1: Upload document
        doc_service = DocumentService(session=self.session)
        upload_result = doc_service.upload_document(
            file=file,
            file_path=file_path,
            title=title,
            document_date=document_date,
        )

        if not upload_result.success:
            return DocumentIngestionResult(
                success=False,
                document_id=upload_result.document_id,
                title=title,
                error=upload_result.error,
            )

        # Step 2: Index the extracted text
        if not upload_result.transcript_path:
            return DocumentIngestionResult(
                success=False,
                document_id=upload_result.document_id,
                title=title,
                error="No transcript path - text extraction may have failed",
            )

        transcript_path = Path(upload_result.transcript_path)
        if not transcript_path.exists():
            return DocumentIngestionResult(
                success=False,
                document_id=upload_result.document_id,
                title=title,
                error=f"Transcript file not found: {transcript_path}",
            )

        # Index using the standard method
        chunks_indexed = self.index_document(
            document_path=transcript_path,
            source=transcript_path.stem,
            document_date=document_date.isoformat() if document_date else None,
        )

        log.info(f"[INGEST] Completed: {title} ({chunks_indexed} chunks)")

        return DocumentIngestionResult(
            success=True,
            document_id=upload_result.document_id,
            title=title,
            file_path=upload_result.file_path,
            transcript_path=upload_result.transcript_path,
            chunks_indexed=chunks_indexed,
        )


@dataclass
class DocumentIngestionResult:
    """Result of a document ingestion (upload + indexing)."""
    success: bool
    document_id: Optional[int] = None
    title: str = ""
    file_path: Optional[str] = None
    transcript_path: Optional[str] = None
    chunks_indexed: int = 0
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "document_id": self.document_id,
            "title": self.title,
            "file_path": self.file_path,
            "transcript_path": self.transcript_path,
            "chunks_indexed": self.chunks_indexed,
            "error": self.error,
        }


# -----------------------------------------------------------------
# Convenience functions
# -----------------------------------------------------------------
def ingest_document(
    *,
    file: Optional[BinaryIO] = None,
    file_path: Optional[Union[Path, str]] = None,
    title: str,
    document_date: datetime,
) -> DocumentIngestionResult:
    """
    Upload and index a single document (convenience function).

    This is the recommended entry point for single document ingestion.
    For batch processing, use index_documents() instead.

    Args:
        file: File-like object (for API uploads)
        file_path: Path to file on disk (for CLI/batch)
        title: Document title
        document_date: Document publication/creation date

    Returns:
        DocumentIngestionResult with success status and details

    Example:
        ```python
        from innovation_intelligence.ingestion.vector_indexing.pipeline import ingest_document
        from datetime import datetime

        result = ingest_document(
            file_path="/path/to/report.pdf",
            title="Q4 2024 Health Trends",
            document_date=datetime(2024, 12, 15),
        )
        if result.success:
            print(f"Indexed {result.chunks_indexed} chunks")
        ```
    """
    with VectorIndexingPipeline() as pipeline:
        return pipeline.ingest_uploaded_document(
            file=file,
            file_path=file_path,
            title=title,
            document_date=document_date,
        )


def index_podcasts(
    transcripts_dir: Optional[Path] = None,
    run_speaker_identification: bool = True,
) -> IndexingStats:
    """
    Index all podcasts from the default or specified directory.

    Args:
        transcripts_dir: Directory with transcript JSONs (defaults to config)
        run_speaker_identification: Whether to identify speakers

    Returns:
        IndexingStats with results
    """
    transcripts_dir = transcripts_dir or settings.paths.transcripts_dir

    with VectorIndexingPipeline(run_speaker_identification=run_speaker_identification) as pipeline:
        pipeline.index_podcast_directory(transcripts_dir)
        return pipeline.stats


def index_documents(
    documents_dir: Optional[Path] = None,
) -> IndexingStats:
    """
    Index all documents from the default or specified directory.

    Args:
        documents_dir: Directory with document files

    Returns:
        IndexingStats with results
    """
    # Default to documents/transcripts (extracted text)
    documents_dir = documents_dir or (settings.paths.data_root / "documents" / "transcripts")

    with VectorIndexingPipeline() as pipeline:
        pipeline.index_document_directory(documents_dir)
        return pipeline.stats


def run_full_ingestion(
    podcasts_dir: Optional[Path] = None,
    documents_dir: Optional[Path] = None,
    run_speaker_identification: bool = True,
    run_migration_first: bool = True,
) -> IndexingStats:
    """
    Run full ingestion: podcasts + documents.

    Args:
        podcasts_dir: Directory with podcast transcripts
        documents_dir: Directory with document files
        run_speaker_identification: Whether to identify speakers
        run_migration_first: Whether to run DB migration first

    Returns:
        Combined IndexingStats
    """
    podcasts_dir = podcasts_dir or settings.paths.transcripts_dir
    documents_dir = documents_dir or (settings.paths.data_root / "documents" / "transcripts")

    log.info("="*60)
    log.info("[INGESTION] Starting full vector ingestion")
    log.info("="*60)

    # Run migration if requested
    if run_migration_first:
        log.info("[INGESTION] Running database migration...")
        run_migration()

    with VectorIndexingPipeline(run_speaker_identification=run_speaker_identification) as pipeline:
        # Index podcasts
        log.info("[INGESTION] Indexing podcasts...")
        pipeline.index_podcast_directory(podcasts_dir)

        # Index documents
        log.info("[INGESTION] Indexing documents...")
        pipeline.index_document_directory(documents_dir)

        log.info("="*60)
        log.info("[INGESTION] Ingestion complete")
        log.info(str(pipeline.stats))
        log.info("="*60)

        return pipeline.stats


# -----------------------------------------------------------------
# CLI
# -----------------------------------------------------------------
if __name__ == "__main__":
    import sys

    # Parse arguments
    args = sys.argv[1:]

    if "--help" in args or "-h" in args:
        print("""
Vector Indexing Pipeline

Usage:
    python pipeline.py                    # Run full ingestion
    python pipeline.py --podcasts-only    # Index only podcasts
    python pipeline.py --documents-only   # Index only documents
    python pipeline.py --no-speaker-id    # Skip speaker identification
    python pipeline.py --skip-migration   # Skip DB migration

Options:
    --podcasts-dir <path>   Custom podcasts directory
    --documents-dir <path>  Custom documents directory
""")
        sys.exit(0)

    # Parse options
    run_speaker_id = "--no-speaker-id" not in args
    run_migration_flag = "--skip-migration" not in args
    podcasts_only = "--podcasts-only" in args
    documents_only = "--documents-only" in args

    # Custom directories
    podcasts_dir = None
    documents_dir = None

    for i, arg in enumerate(args):
        if arg == "--podcasts-dir" and i + 1 < len(args):
            podcasts_dir = Path(args[i + 1])
        elif arg == "--documents-dir" and i + 1 < len(args):
            documents_dir = Path(args[i + 1])

    # Run appropriate indexing
    if podcasts_only:
        stats = index_podcasts(podcasts_dir, run_speaker_id)
    elif documents_only:
        stats = index_documents(documents_dir)
    else:
        stats = run_full_ingestion(
            podcasts_dir=podcasts_dir,
            documents_dir=documents_dir,
            run_speaker_identification=run_speaker_id,
            run_migration_first=run_migration_flag,
        )

    print("\n" + str(stats))

    if stats.errors:
        print("\nErrors:")
        for error in stats.errors:
            print(f"  - {error}")

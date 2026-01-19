"""
End-to-end vector indexing pipeline (GCS-first mode).

Orchestrates:
1. Transcript loading from GCS
2. Chunking (podcasts and documents)
3. Embedding
4. Storage in pgvector

All content is loaded from GCS URIs stored in the database.
No local file discovery - all sources must be ingested first.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO, Dict, List, Optional, Union

from innovation_intelligence.config import settings
from innovation_intelligence.logger import get_logger

# Transcript transformation
from innovation_intelligence.ingestion.podcasts.transcript_transformer import (
    transform_transcript,
    TransformedTranscript,
)

# Chunking
from innovation_intelligence.ingestion.podcasts.podcast_chunker import (
    PodcastChunker,
    PodcastChunk,
)
from innovation_intelligence.ingestion.documents.document_chunker import (
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
        batch_size: int = 50,
        session=None,
    ):
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
    def index_podcast_episode_from_gcs(
        self,
        gcs_transcript_uri: str,
        podcast_name: str,
        episode_title: str,
        episode_date: Optional[str] = None,
        raise_on_error: bool = False,
    ) -> int:
        """
        Index a single podcast episode from GCS (GCS-first mode).

        Args:
            gcs_transcript_uri: GCS URI to transcript JSON
            podcast_name: Podcast name
            episode_title: Episode title
            episode_date: Episode publication date (optional)
            raise_on_error: If True, raises exceptions instead of catching them

        Returns:
            Number of chunks indexed

        Raises:
            Exception: If raise_on_error=True and indexing fails
        """
        from innovation_intelligence.ingestion.gcs_service import GCSStorageService
        from innovation_intelligence.ingestion.podcasts.podcast_chunker import chunk_from_gcs

        source = f"{podcast_name} - {episode_title}"
        log.info(f"[INDEX] Processing podcast from GCS: {source}")

        try:
            # Step 1: Load and chunk transcript from GCS
            chunks = chunk_from_gcs(
                gcs_transcript_uri=gcs_transcript_uri,
                source=source,
                episode_date=episode_date,
            )

            if not chunks:
                log.warning(f"[INDEX] No chunks created for {source}")
                return 0

            self.stats.podcast_chunks_created += len(chunks)

            # Step 2: Embed chunks
            log.info(f"[INDEX] Embedding {len(chunks)} podcast chunks")
            texts_to_embed = [chunk.full_text for chunk in chunks]
            embeddings = embed_texts_batch(texts_to_embed)

            # Step 3: Store in database
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

            # Re-raise if requested (for pipeline failure detection)
            if raise_on_error:
                raise

            return 0

    def index_all_podcasts_from_db(self) -> int:
        """
        Index all podcast episodes from database (GCS-first mode).

        Queries all episodes with GCS transcript URIs and indexes them.

        Returns:
            Total chunks indexed
        """
        from innovation_intelligence.db.models import PodcastEpisode

        episodes = self.session.query(PodcastEpisode).filter(
            PodcastEpisode.gcs_transcript_uri.isnot(None)
        ).all()

        log.info(f"[INDEX] Found {len(episodes)} podcast episodes with GCS transcripts in database")

        total_chunks = 0
        for i, episode in enumerate(episodes, 1):
            log.info(f"[INDEX] Processing podcast {i}/{len(episodes)}: {episode.podcast_name} - {episode.episode_title}")
            chunks = self.index_podcast_episode_from_gcs(
                gcs_transcript_uri=episode.gcs_transcript_uri,
                podcast_name=episode.podcast_name,
                episode_title=episode.episode_title,
                episode_date=episode.episode_date.isoformat() if episode.episode_date else None,
            )
            total_chunks += chunks

        return total_chunks

    # -----------------------------------------------------------------
    # Document indexing
    # -----------------------------------------------------------------
    def index_document_from_gcs(
        self,
        gcs_transcript_uri: str,
        source: str,
        document_date: Optional[str] = None,
    ) -> int:
        """
        Index a single document from GCS (GCS-first mode).

        Args:
            gcs_transcript_uri: GCS URI to transcript text file
            source: Document title
            document_date: Document date (optional)

        Returns:
            Number of chunks indexed
        """
        from innovation_intelligence.ingestion.gcs_service import GCSStorageService

        log.info(f"[INDEX] Processing document from GCS: {source}")

        try:
            # Step 1: Load transcript from GCS
            gcs_service = GCSStorageService()
            data = gcs_service.read_json(gcs_transcript_uri)

            # Extract text and segments from the transcript data
            text = data.get("text", "")
            segments = data.get("segments", [])

            if not text.strip():
                log.warning(f"[INDEX] Empty document: {source}")
                return 0

            # Step 2: Chunk the document (use segments if available for accurate page numbers)
            if segments:
                log.debug(f"[INDEX] Using segments for accurate page tracking ({len(segments)} pages)")
                chunks = self.document_chunker.chunk_from_segments(
                    segments=segments,
                    source=source,
                    document_date=document_date,
                )
            else:
                log.debug(f"[INDEX] Using legacy text-based chunking (no page info)")
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

    def index_all_documents_from_db(self) -> int:
        """
        Index all documents from database (GCS-first mode).

        Queries all documents with GCS transcript URIs and indexes them.

        Returns:
            Total chunks indexed
        """
        from innovation_intelligence.db.models import Document

        documents = self.session.query(Document).filter(
            Document.gcs_transcript_uri.isnot(None)
        ).all()

        log.info(f"[INDEX] Found {len(documents)} documents with GCS transcripts in database")

        total_chunks = 0
        for i, doc in enumerate(documents, 1):
            log.info(f"[INDEX] Processing document {i}/{len(documents)}: {doc.title}")
            chunks = self.index_document_from_gcs(
                gcs_transcript_uri=doc.gcs_transcript_uri,
                source=doc.title,
                document_date=doc.document_date.isoformat() if doc.document_date else None,
            )
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

        # Step 2: Index the extracted text from GCS
        if not upload_result.gcs_transcript_uri:
            return DocumentIngestionResult(
                success=False,
                document_id=upload_result.document_id,
                title=title,
                error="No GCS transcript URI - text extraction may have failed",
            )

        # Download transcript from GCS to temp file for indexing
        from innovation_intelligence.ingestion.gcs_service import GCSStorageService
        import tempfile

        gcs_service = GCSStorageService()
        tmp_transcript = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8')
        tmp_transcript_path = Path(tmp_transcript.name)

        try:
            # Read transcript from GCS
            transcript_data = gcs_service.read_text(upload_result.gcs_transcript_uri)
            tmp_transcript.write(transcript_data)
            tmp_transcript.close()

            # Index using the standard method
            chunks_indexed = self.index_document(
                document_path=tmp_transcript_path,
                source=title,
                document_date=document_date.isoformat() if document_date else None,
            )

            log.info(f"[INGEST] Completed: {title} ({chunks_indexed} chunks)")

            return DocumentIngestionResult(
                success=True,
                document_id=upload_result.document_id,
                title=title,
                gcs_uri=upload_result.gcs_uri,
                gcs_transcript_uri=upload_result.gcs_transcript_uri,
                chunks_indexed=chunks_indexed,
            )
        finally:
            # Clean up temp file
            if tmp_transcript_path.exists():
                tmp_transcript_path.unlink()


@dataclass
class DocumentIngestionResult:
    """Result of a document ingestion (upload + indexing) - GCS-first mode."""
    success: bool
    document_id: Optional[int] = None
    title: str = ""
    gcs_uri: Optional[str] = None
    gcs_transcript_uri: Optional[str] = None
    chunks_indexed: int = 0
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "document_id": self.document_id,
            "title": self.title,
            "gcs_uri": self.gcs_uri,
            "gcs_transcript_uri": self.gcs_transcript_uri,
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


def index_podcasts() -> IndexingStats:
    """
    Index all podcasts from database (GCS-first mode).

    Returns:
        IndexingStats with results
    """
    with VectorIndexingPipeline() as pipeline:
        pipeline.index_all_podcasts_from_db()
        return pipeline.stats


def index_documents() -> IndexingStats:
    """
    Index all documents from database (GCS-first mode).

    Returns:
        IndexingStats with results
    """
    with VectorIndexingPipeline() as pipeline:
        pipeline.index_all_documents_from_db()
        return pipeline.stats


def run_full_ingestion(
    run_migration_first: bool = True,
) -> IndexingStats:
    """
    Run full ingestion: podcasts + documents from database (GCS-first mode).

    Args:
        run_migration_first: Whether to run DB migration first

    Returns:
        Combined IndexingStats
    """
    log.info("="*60)
    log.info("[INGESTION] Starting full vector ingestion (GCS-first mode)")
    log.info("="*60)

    # Run migration if requested
    if run_migration_first:
        log.info("[INGESTION] Running database migration...")
        run_migration()

    with VectorIndexingPipeline() as pipeline:
        # Index podcasts from database
        log.info("[INGESTION] Indexing podcasts from database...")
        pipeline.index_all_podcasts_from_db()

        # Index documents from database
        log.info("[INGESTION] Indexing documents from database...")
        pipeline.index_all_documents_from_db()

        log.info("="*60)
        log.info("[INGESTION] Ingestion complete")
        log.info(str(pipeline.stats))
        log.info("="*60)

        return pipeline.stats


def run_indexing_pipeline(
    session,
    podcasts_only: bool = False,
    documents_only: bool = False,
) -> dict:
    """
    Run vector indexing pipeline from database (GCS-first mode, for API use).

    Args:
        session: Database session
        podcasts_only: Index only podcasts
        documents_only: Index only documents

    Returns:
        Dict with podcast_chunks and document_chunks counts
    """
    pipeline = VectorIndexingPipeline(session=session)

    if not documents_only:
        pipeline.index_all_podcasts_from_db()

    if not podcasts_only:
        pipeline.index_all_documents_from_db()

    return {
        "podcast_chunks": pipeline.stats.podcast_chunks_stored,
        "document_chunks": pipeline.stats.document_chunks_stored,
    }


# -----------------------------------------------------------------
# CLI
# -----------------------------------------------------------------
if __name__ == "__main__":
    import sys

    # Parse arguments
    args = sys.argv[1:]

    if "--help" in args or "-h" in args:
        print("""
Vector Indexing Pipeline (GCS-first mode)

Usage:
    python pipeline.py                    # Run full ingestion from database
    python pipeline.py --podcasts-only    # Index only podcasts from database
    python pipeline.py --documents-only   # Index only documents from database
    python pipeline.py --skip-migration   # Skip DB migration

All content is loaded from GCS URIs stored in the database.
""")
        sys.exit(0)

    # Parse options
    run_migration_flag = "--skip-migration" not in args
    podcasts_only = "--podcasts-only" in args
    documents_only = "--documents-only" in args

    # Run appropriate indexing
    if podcasts_only:
        stats = index_podcasts()
    elif documents_only:
        stats = index_documents()
    else:
        stats = run_full_ingestion(
            run_migration_first=run_migration_flag,
        )

    print("\n" + str(stats))

    if stats.errors:
        print("\nErrors:")
        for error in stats.errors:
            print(f"  - {error}")

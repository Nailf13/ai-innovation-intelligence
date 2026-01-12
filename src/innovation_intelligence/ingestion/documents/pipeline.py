from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from innovation_intelligence.config import settings
from innovation_intelligence.logger import get_logger
from innovation_intelligence.db.session import init_db, SessionLocal

from innovation_intelligence.ingestion.documents.discovery import list_pdf_files
from innovation_intelligence.ingestion.documents.text_extraction import pdf_to_text_file
from innovation_intelligence.db.repositories import DocumentRepository

log = get_logger(__name__)


_gcs_service = None


def _get_gcs_service():
    """Get or create GCS service (lazy initialization)."""
    global _gcs_service
    if _gcs_service is None:
        from innovation_intelligence.ingestion.gcs_service import GCSStorageService
        _gcs_service = GCSStorageService()
    return _gcs_service


def get_documents_dirs() -> tuple[Path, Path]:
    """
    Compute input and transcript directories for documents based on config.

    - input_dir:       <DATA_ROOT>/documents/raw
    - transcripts_dir: <DATA_ROOT>/documents/transcripts
    """
    data_root = settings.paths.data_root
    input_dir = (data_root / "documents" / "raw").resolve()
    transcripts_dir = (data_root / "documents" / "transcripts").resolve()
    input_dir.mkdir(parents=True, exist_ok=True)
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    return input_dir, transcripts_dir


def _generate_document_id(title: str) -> str:
    """Generate a unique document ID from title."""
    import datetime
    clean_title = "".join(c if c.isalnum() or c in " -_" else "_" for c in title)
    clean_title = clean_title.strip().replace(" ", "_")[:50]
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{clean_title}_{timestamp}"


def ingest_documents(
    root: Path | None = None,
    upload_to_gcs: bool = True,
    store_transcript_in_gcs: bool = True,
) -> Dict[str, Any]:
    """
    End-to-end ingestion pipeline for documents (GCS-first mode, PDF only):

      1) Scan documents/raw (or a custom root) for PDFs
      2) For each PDF:
         - upload document to GCS (required)
         - extract text via PyMuPDF to temporary file
         - store transcript in GCS
         - create Document record with GCS URIs only
         - skip if GCS transcript URI already exists

    Args:
        root: Optional custom input directory (defaults to documents/raw)
        upload_to_gcs: Whether to upload documents to GCS (default: True, required for new docs)
        store_transcript_in_gcs: Whether to store transcripts in GCS (default: True)

    Returns:
        Dictionary with ingestion results and statistics (GCS URIs only)
    """
    init_db()

    results = {
        "total": 0,
        "processed": 0,
        "skipped": 0,
        "failed": 0,
        "documents": [],
    }

    if root is None:
        input_dir, transcripts_dir = get_documents_dirs()
    else:
        input_dir = Path(root).resolve()
        data_root = settings.paths.data_root
        transcripts_dir = (data_root / "documents" / "transcripts").resolve()
        transcripts_dir.mkdir(parents=True, exist_ok=True)

    log.info(f"[DOC] Input dir: {input_dir}")
    log.info(f"[DOC] Transcript dir: {transcripts_dir}")

    pdf_files = list_pdf_files(input_dir)
    if not pdf_files:
        log.warning(f"[DOC] No PDFs found in {input_dir}")
        return results

    results["total"] = len(pdf_files)
    log.info(f"[DOC] Found {len(pdf_files)} PDF(s) to inspect.")

    session: Session = SessionLocal()
    repo = DocumentRepository(session)

    # Get GCS service if uploading is enabled
    gcs_service = _get_gcs_service() if (upload_to_gcs or store_transcript_in_gcs) else None

    try:
        # Get existing documents indexed by GCS URI
        existing_index = repo.index_by_gcs_uri()
        # For local paths, we'll need to build a separate index
        existing_by_local_path = {}

        for pdf_path in pdf_files:
            pdf_path = pdf_path.resolve()
            key = str(pdf_path)

            log.info("-" * 80)
            log.info(f"[DOC] Processing: {pdf_path}")

            doc_result = {
                "file_path": str(pdf_path),
                "title": pdf_path.stem.replace("_", " "),
                "status": "pending",
                "gcs_uri": None,
                "gcs_transcript_uri": None,
                "error": None,
            }

            try:
                # Check if document exists by looking for its GCS URI
                # We'll generate the document_id upfront to check
                title = pdf_path.stem.replace("_", " ")
                document_id = _generate_document_id(title)

                doc = existing_by_local_path.get(key)

                # If we already have a transcript (GCS-first mode), skip
                if doc and doc.gcs_transcript_uri:
                    log.info(
                        f"[DOC] Already ingested (id={doc.id}), gcs_transcript_uri={doc.gcs_transcript_uri} -> skipping."
                    )
                    doc_result["status"] = "skipped"
                    doc_result["document_id"] = doc.id
                    results["skipped"] += 1
                    results["documents"].append(doc_result)
                    continue

                # Where to store extracted text
                out_name = pdf_path.stem + ".txt"
                out_path = transcripts_dir / out_name

                # Extract text and save
                log.info(f"[DOC] Extracting text to: {out_path}")
                pdf_to_text_file(pdf_path, out_path)

                title = pdf_path.stem.replace("_", " ")
                document_id = _generate_document_id(title)

                # Upload document to GCS
                gcs_uri = None
                if upload_to_gcs and gcs_service:
                    try:
                        gcs_uri = gcs_service.upload_document(pdf_path, document_id)
                        log.info(f"[GCS] Uploaded document: {gcs_uri}")
                        doc_result["gcs_uri"] = gcs_uri
                    except Exception as e:
                        log.warning(f"[GCS] Document upload failed: {e}")

                # Store transcript in GCS
                gcs_transcript_uri = None
                if store_transcript_in_gcs and gcs_service:
                    try:
                        # Read the transcript text
                        text = out_path.read_text(encoding="utf-8")

                        # Create transcript data structure
                        transcript_data = {
                            "text": text,
                            "segments": [{"text": text, "start": 0, "end": 0}],
                            "_metadata": {
                                "document_id": document_id,
                                "source_file": pdf_path.name,
                                "extraction_method": "pdf_to_text",
                            },
                        }

                        gcs_transcript_uri = gcs_service.store_transcript(
                            data=transcript_data,
                            content_id=document_id,
                            content_type="document",
                        )
                        log.info(f"[GCS] Stored transcript: {gcs_transcript_uri}")
                        doc_result["gcs_transcript_uri"] = gcs_transcript_uri
                    except Exception as e:
                        log.warning(f"[GCS] Transcript storage failed: {e}")

                if doc is None:
                    # New document (GCS-first mode - requires GCS URI)
                    if not gcs_uri:
                        log.error(f"[DB] Cannot create document without GCS URI (upload_to_gcs must be True)")
                        doc_result["status"] = "failed"
                        doc_result["error"] = "Missing GCS URI"
                        results["failed"] += 1
                        results["documents"].append(doc_result)
                        continue

                    doc = repo.create(
                        title=title,
                        gcs_document_uri=gcs_uri,
                        source_type="pdf",
                        gcs_transcript_uri=gcs_transcript_uri,
                    )
                    existing_index[key] = doc
                    log.info(f"[DB] Created Document id={doc.id}")
                else:
                    # Existing document, update GCS URIs if available
                    if gcs_uri or gcs_transcript_uri:
                        repo.update_gcs_uris(
                            doc,
                            gcs_document_uri=gcs_uri,
                            gcs_transcript_uri=gcs_transcript_uri,
                        )
                    log.info(f"[DB] Updated Document id={doc.id} with GCS URIs")

                doc_result["status"] = "processed"
                doc_result["document_id"] = doc.id
                results["processed"] += 1

            except Exception as e:
                log.error(f"[DOC] Failed to process {pdf_path}: {e}")
                doc_result["status"] = "failed"
                doc_result["error"] = str(e)
                results["failed"] += 1

            results["documents"].append(doc_result)

    finally:
        session.close()

    log.info(f"Document ingestion completed: {results['processed']} processed, "
             f"{results['skipped']} skipped, {results['failed']} failed")

    return results


def ingest_documents_from_gcs(
    gcs_uris: List[str],
    extract_text: bool = True,
    store_transcript_in_gcs: bool = True,
) -> Dict[str, Any]:
    """
    Ingest documents from GCS URIs (GCS-first mode).

    Downloads documents from GCS to temporary files, extracts text,
    stores transcripts in GCS, and creates database records with GCS URIs only.

    Args:
        gcs_uris: List of GCS URIs to ingest
        extract_text: Whether to extract text from PDFs
        store_transcript_in_gcs: Whether to store transcripts back to GCS

    Returns:
        Dictionary with ingestion results (GCS URIs only)
    """
    init_db()

    results = {
        "total": len(gcs_uris),
        "processed": 0,
        "failed": 0,
        "documents": [],
    }

    if not gcs_uris:
        log.warning("[DOC] No GCS URIs provided")
        return results

    _, transcripts_dir = get_documents_dirs()
    gcs_service = _get_gcs_service()

    session: Session = SessionLocal()
    repo = DocumentRepository(session)

    try:
        for gcs_uri in gcs_uris:
            log.info("-" * 80)
            log.info(f"[DOC] Processing from GCS: {gcs_uri}")

            doc_result = {
                "gcs_uri": gcs_uri,
                "status": "pending",
                "error": None,
            }

            try:
                # Download from GCS to temp location
                from tempfile import mkdtemp
                temp_dir = Path(mkdtemp())
                local_path = gcs_service.download_document(gcs_uri, temp_dir)

                title = local_path.stem.replace("_", " ")
                doc_result["title"] = title

                # Check if already exists by GCS URI
                existing = repo.get_by_gcs_uri(gcs_uri)
                if existing:
                    log.info(f"[DOC] Already exists: {existing.id}")
                    doc_result["status"] = "skipped"
                    doc_result["document_id"] = existing.id
                    results["documents"].append(doc_result)
                    continue

                # Extract text if enabled (using temp file)
                gcs_transcript_uri = None

                if extract_text and local_path.suffix.lower() == ".pdf":
                    # Extract text to temporary location
                    import tempfile
                    tmp_transcript = tempfile.NamedTemporaryFile(
                        mode="w", suffix=".txt", delete=False, encoding="utf-8"
                    )
                    tmp_transcript_path = Path(tmp_transcript.name)
                    tmp_transcript.close()

                    try:
                        log.info(f"[DOC] Extracting text to temp file")
                        pdf_to_text_file(local_path, tmp_transcript_path)

                        # Store transcript in GCS
                        if store_transcript_in_gcs:
                            try:
                                document_id = _generate_document_id(title)
                                text = tmp_transcript_path.read_text(encoding="utf-8")

                                transcript_data = {
                                    "text": text,
                                    "segments": [{"text": text, "start": 0, "end": 0}],
                                    "_metadata": {
                                        "document_id": document_id,
                                        "source_gcs_uri": gcs_uri,
                                        "extraction_method": "pdf_to_text",
                                    },
                                }

                                gcs_transcript_uri = gcs_service.store_transcript(
                                    data=transcript_data,
                                    content_id=document_id,
                                    content_type="document",
                                )
                                log.info(f"[GCS] Stored transcript: {gcs_transcript_uri}")
                                doc_result["gcs_transcript_uri"] = gcs_transcript_uri
                            except Exception as e:
                                log.warning(f"[GCS] Transcript storage failed: {e}")
                    finally:
                        # Clean up temporary transcript file
                        if tmp_transcript_path.exists():
                            tmp_transcript_path.unlink()

                # Create database record with GCS URIs only
                doc = repo.create(
                    title=title,
                    gcs_document_uri=gcs_uri,
                    source_type="pdf",
                    gcs_transcript_uri=gcs_transcript_uri,
                )
                log.info(f"[DB] Created Document id={doc.id}")

                doc_result["status"] = "processed"
                doc_result["document_id"] = doc.id
                results["processed"] += 1

            except Exception as e:
                log.error(f"[DOC] Failed to process {gcs_uri}: {e}")
                doc_result["status"] = "failed"
                doc_result["error"] = str(e)
                results["failed"] += 1

            results["documents"].append(doc_result)

    finally:
        session.close()

    log.info(f"GCS ingestion completed: {results['processed']} processed, "
             f"{results['failed']} failed")

    return results


if __name__ == "__main__":
    ingest_documents()
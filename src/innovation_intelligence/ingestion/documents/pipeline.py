from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from innovation_intelligence.config import settings
from innovation_intelligence.logger import get_logger
from innovation_intelligence.db.session import init_db, SessionLocal

from innovation_intelligence.ingestion.documents.discovery import list_pdf_files
from innovation_intelligence.ingestion.documents.text_extraction import pdf_to_text_file
from innovation_intelligence.db.repositories import DocumentRepository

log = get_logger(__name__)


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


def ingest_documents(root: Path | None = None) -> None:
    """
    End-to-end ingestion pipeline for documents (PDF only for now):

      1) Scan documents/raw (or a custom root) for PDFs
      2) For each PDF:
         - if not in DB → create Document row
         - extract text via PyMuPDF
         - save .txt in documents/transcripts
         - update Document.transcript_path
    """
    init_db()

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
        return

    log.info(f"[DOC] Found {len(pdf_files)} PDF(s) to inspect.")

    session: Session = SessionLocal()
    repo = DocumentRepository(session)

    try:
        existing_index = repo.index_by_path()

        for pdf_path in pdf_files:
            pdf_path = pdf_path.resolve()
            key = str(pdf_path)

            log.info("-" * 80)
            log.info(f"[DOC] Processing: {pdf_path}")

            doc = existing_index.get(key)

            # If we already have a transcript, skip
            if doc and doc.transcript_path:
                log.info(
                    f"[DOC] Already ingested (id={doc.id}), transcript_path={doc.transcript_path} → skipping."
                )
                continue

            # Where to store extracted text
            out_name = pdf_path.stem + ".txt"
            out_path = transcripts_dir / out_name

            # Extract text and save
            log.info(f"[DOC] Extracting text to: {out_path}")
            pdf_to_text_file(pdf_path, out_path)

            title = pdf_path.stem.replace("_", " ")

            if doc is None:
                # New document
                doc = repo.create(
                    title=title,
                    file_path=pdf_path,
                    source_type="pdf",
                    transcript_path=out_path,
                )
                existing_index[key] = doc
                log.info(f"[DB] Created Document id={doc.id}")
            else:
                # Existing document, just update transcript
                repo.update_transcript_path(doc, out_path)
                log.info(f"[DB] Updated Document id={doc.id} with transcript_path")

    finally:
        session.close()

    log.info("🎉 Document ingestion pipeline completed successfully.")


if __name__ == "__main__":
    ingest_documents()
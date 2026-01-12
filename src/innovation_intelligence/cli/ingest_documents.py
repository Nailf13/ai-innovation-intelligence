#!/usr/bin/env python3
# src/innovation_intelligence/cli/ingest_documents.py
"""
CLI entrypoint for document ingestion.

Full pipeline:
1. Upload document file (PDF, TXT, etc.)
2. Upload to GCS
3. Extract text from document
4. Persist transcript in GCS bucket
5. Persist metadata in PostgreSQL
6. Index vectors in pgvector

Usage:
    ingest-documents upload /path/to/report.pdf --title "Q4 2024 Health Trends"
    ingest-documents upload /path/to/report.pdf --title "Report" --date 2024-12-15
    ingest-documents upload /path/to/report.pdf --title "Report" --source-type whitepaper
    ingest-documents batch /path/to/documents/folder
    ingest-documents list
    ingest-documents stats
"""
import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from innovation_intelligence.config import settings
from innovation_intelligence.logger import get_logger

log = get_logger(__name__)


def cmd_upload(args):
    """Upload a single document."""
    from innovation_intelligence.ingestion.indexing.pipeline import (
        VectorIndexingPipeline,
    )

    # Validate file path
    file_path = Path(args.file_path)
    if not file_path.exists():
        print(f"Error: File not found: {file_path}")
        sys.exit(1)

    # Parse document date
    document_date: Optional[datetime] = None
    if args.date:
        try:
            document_date = datetime.fromisoformat(args.date)
        except ValueError:
            print(f"Error: Invalid date format: {args.date}")
            print("Use ISO format: YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS")
            sys.exit(1)
    else:
        # Default to current date
        document_date = datetime.now()

    print("=" * 80)
    print("DOCUMENT INGESTION PIPELINE")
    print("=" * 80)
    print(f"File: {file_path.name}")
    print(f"Title: {args.title}")
    print(f"Date: {document_date.strftime('%Y-%m-%d')}")
    print(f"Source type: {args.source_type or 'auto-detect'}")
    print(f"File format: {args.file_format or 'auto-detect'}")
    print(f"Skip indexing: {args.skip_indexing}")
    print(f"Upload to GCS: {not args.no_gcs}")
    print("=" * 80)
    print()

    # Run ingestion
    with VectorIndexingPipeline() as pipeline:
        # Configure GCS settings
        pipeline.document_service = None  # Will be recreated with proper settings
        from innovation_intelligence.ingestion.documents.document_service import DocumentService

        doc_service = DocumentService(
            session=pipeline.session,
            upload_to_gcs=not args.no_gcs,
            store_transcript_in_gcs=not args.no_gcs,
        )

        print("[UPLOAD] Uploading document...")
        upload_result = doc_service.upload_document(
            file_path=file_path,
            title=args.title,
            document_date=document_date,
            source_type=args.source_type,
            file_format=args.file_format,
            extract_text=True,
        )

        if not upload_result.success:
            print(f"Error: {upload_result.error}")
            sys.exit(1)

        print(f"[UPLOAD] Success! Document ID: {upload_result.document_id}")
        if upload_result.warnings:
            for warning in upload_result.warnings:
                print(f"[UPLOAD] Warning: {warning}")

        # Print GCS URIs if available
        if upload_result.gcs_uri:
            print(f"[GCS] Document URI: {upload_result.gcs_uri}")
        if upload_result.gcs_transcript_uri:
            print(f"[GCS] Transcript URI: {upload_result.gcs_transcript_uri}")

        # Index vectors if not skipped
        chunks_indexed = 0
        if not args.skip_indexing:
            if upload_result.gcs_transcript_uri:
                print()
                print("[INDEX] Indexing vectors from GCS...")
                print(f"[INDEX] Reading from GCS: {upload_result.gcs_transcript_uri}")

                # Index the document from GCS
                from innovation_intelligence.ingestion.indexing.pipeline import VectorIndexingPipeline
                from innovation_intelligence.db.session import SessionLocal

                session = SessionLocal()
                try:
                    with VectorIndexingPipeline(session=session) as pipeline:
                        chunks_indexed = pipeline.index_document_from_gcs(
                            gcs_transcript_uri=upload_result.gcs_transcript_uri,
                            source=upload_result.title,
                            document_date=args.date.isoformat() if args.date else None,
                        )
                    print(f"[INDEX] Successfully indexed {chunks_indexed} chunks")
                except Exception as e:
                    print(f"[INDEX] Error during indexing: {e}")
                    print(f"[INDEX] You can manually index later with: index-vectors run --documents-only")
                finally:
                    session.close()
            else:
                print()
                print("[INDEX] Warning: No transcript available, skipping indexing")

    # Print summary
    print()
    print("=" * 80)
    print("INGESTION COMPLETE")
    print("=" * 80)
    print(f"Document ID: {upload_result.document_id}")
    print(f"Title: {upload_result.title}")
    print(f"GCS URI: {upload_result.gcs_uri}")
    print(f"Transcript URI: {upload_result.gcs_transcript_uri or 'N/A'}")
    print(f"Chunks indexed: {chunks_indexed}")
    print("=" * 80)


def cmd_batch(args):
    """Batch process documents from a folder."""
    from innovation_intelligence.ingestion.documents.pipeline import ingest_documents

    # Validate directory
    directory = Path(args.directory)
    if not directory.exists():
        print(f"Error: Directory not found: {directory}")
        sys.exit(1)

    if not directory.is_dir():
        print(f"Error: Not a directory: {directory}")
        sys.exit(1)

    print("=" * 80)
    print("BATCH DOCUMENT INGESTION")
    print("=" * 80)
    print(f"Directory: {directory}")
    print(f"Upload to GCS: {not args.no_gcs}")
    print("=" * 80)
    print()

    # Run batch ingestion
    results = ingest_documents(
        root=directory,
        upload_to_gcs=not args.no_gcs,
        store_transcript_in_gcs=not args.no_gcs,
    )

    # Print summary
    print()
    print("=" * 80)
    print("BATCH INGESTION COMPLETE")
    print("=" * 80)
    print(f"Total files: {results['total']}")
    print(f"Processed: {results['processed']}")
    print(f"Skipped: {results['skipped']}")
    print(f"Failed: {results['failed']}")
    print("=" * 80)

    # Print details
    if results["documents"]:
        print()
        print("DOCUMENT DETAILS:")
        print("-" * 80)
        for i, doc in enumerate(results["documents"], 1):
            status_symbol = "✓" if doc["status"] == "processed" else ("⊘" if doc["status"] == "skipped" else "✗")
            print(f"\n[{i}] {status_symbol} {doc['title']}")
            print(f"    Status: {doc['status']}")
            if doc.get("document_id"):
                print(f"    DB ID: {doc['document_id']}")
            if doc.get("gcs_uri"):
                print(f"    GCS: {doc['gcs_uri']}")
            if doc.get("error"):
                print(f"    Error: {doc['error']}")

    # Index vectors if requested
    if not args.skip_indexing:
        print()
        print("=" * 80)
        print("INDEXING VECTORS")
        print("=" * 80)
        from innovation_intelligence.ingestion.indexing.pipeline import index_documents

        stats = index_documents()
        print()
        print(str(stats))

    # Exit with error if any failed
    if results["failed"] > 0:
        sys.exit(1)


def cmd_list(args):
    """List documents in the database."""
    from innovation_intelligence.db.session import SessionLocal
    from innovation_intelligence.db.repositories.document_repository import DocumentRepository

    session = SessionLocal()

    try:
        repo = DocumentRepository(session)
        documents = repo.list_all(
            source_type=args.source_type,
            limit=args.limit,
        )

        if not documents:
            print("No documents found.")
            return

        print()
        print("=" * 80)
        print(f"DOCUMENTS ({len(documents)})")
        print("=" * 80)

        for i, doc in enumerate(documents, 1):
            print(f"\n[{i}] {doc.title}")
            print(f"    ID: {doc.id}")
            print(f"    Source type: {doc.source_type}")
            if doc.document_date:
                print(f"    Date: {doc.document_date.strftime('%Y-%m-%d')}")
            if doc.gcs_document_uri:
                print(f"    GCS URI: {doc.gcs_document_uri}")
            has_transcript = "Yes" if doc.gcs_transcript_uri else "No"
            print(f"    Transcript: {has_transcript}")
            print(f"    Created: {doc.created_at.strftime('%Y-%m-%d %H:%M')}")

    finally:
        session.close()


def cmd_stats(args):
    """Show document ingestion statistics."""
    from innovation_intelligence.db.session import SessionLocal
    from innovation_intelligence.db.repositories.document_repository import DocumentRepository
    from innovation_intelligence.db.repositories.vector_repository import VectorRepository

    session = SessionLocal()

    try:
        doc_repo = DocumentRepository(session)
        vector_repo = VectorRepository(session)

        # Get counts
        total_documents = doc_repo.count()
        documents_with_transcripts = doc_repo.count_with_transcripts()
        total_document_chunks = vector_repo.count_document_chunks()

        # Get documents by source type
        documents_by_type = doc_repo.count_by_source_type()

        print()
        print("=" * 80)
        print("DOCUMENT INGESTION STATISTICS")
        print("=" * 80)
        print(f"Total documents: {total_documents}")
        print(f"Documents with transcripts: {documents_with_transcripts}")
        print(f"Document chunks in vector DB: {total_document_chunks:,}")
        print()
        print("Documents by source type:")
        for source_type, count in documents_by_type.items():
            print(f"  - {source_type}: {count}")
        print("=" * 80)

    finally:
        session.close()


def cmd_delete(args):
    """Delete a document."""
    from innovation_intelligence.ingestion.documents.document_service import DocumentService

    print(f"Deleting document ID: {args.document_id}")
    print(f"  Delete files: {args.delete_files}")
    print(f"  Delete from GCS: {args.delete_gcs}")

    if not args.force:
        response = input("\nAre you sure? (yes/no): ")
        if response.lower() not in ["yes", "y"]:
            print("Cancelled.")
            return

    with DocumentService() as service:
        success = service.delete_document(
            document_id=args.document_id,
            delete_files=args.delete_files,
            delete_from_gcs=args.delete_gcs,
        )

        if success:
            print("Document deleted successfully.")
        else:
            print("Error: Document not found.")
            sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Document ingestion CLI for Innovation Intelligence",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Upload a single document
  ingest-documents upload /path/to/report.pdf --title "Q4 2024 Health Trends"

  # Upload with metadata
  ingest-documents upload report.pdf --title "Report" --date 2024-12-15 --source-type whitepaper

  # Batch process documents from a folder
  ingest-documents batch /path/to/documents/

  # List all documents
  ingest-documents list

  # View statistics
  ingest-documents stats

  # Delete a document
  ingest-documents delete 123 --delete-files --delete-gcs
        """,
    )
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # Upload command
    upload_parser = subparsers.add_parser(
        "upload",
        help="Upload a single document",
    )
    upload_parser.add_argument(
        "file_path",
        type=str,
        help="Path to the document file",
    )
    upload_parser.add_argument(
        "--title",
        type=str,
        required=True,
        help="Document title",
    )
    upload_parser.add_argument(
        "--date",
        type=str,
        help="Document date (ISO format: YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS). Defaults to current date.",
    )
    upload_parser.add_argument(
        "--source-type",
        type=str,
        help="Source type (e.g., report, whitepaper, article). Auto-detected if not provided.",
    )
    upload_parser.add_argument(
        "--file-format",
        type=str,
        help="File format (e.g., pdf, txt, docx). Auto-detected if not provided.",
    )
    upload_parser.add_argument(
        "--skip-indexing",
        action="store_true",
        help="Skip vector indexing stage",
    )
    upload_parser.add_argument(
        "--no-gcs",
        action="store_true",
        help="Skip GCS upload (local storage only)",
    )
    upload_parser.set_defaults(func=cmd_upload)

    # Batch command
    batch_parser = subparsers.add_parser(
        "batch",
        help="Batch process documents from a folder",
    )
    batch_parser.add_argument(
        "directory",
        type=str,
        help="Path to directory containing documents",
    )
    batch_parser.add_argument(
        "--skip-indexing",
        action="store_true",
        help="Skip vector indexing stage",
    )
    batch_parser.add_argument(
        "--no-gcs",
        action="store_true",
        help="Skip GCS upload (local storage only)",
    )
    batch_parser.set_defaults(func=cmd_batch)

    # List command
    list_parser = subparsers.add_parser(
        "list",
        help="List documents in the database",
    )
    list_parser.add_argument(
        "--source-type",
        type=str,
        help="Filter by source type",
    )
    list_parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum number of documents to show (default: 50)",
    )
    list_parser.set_defaults(func=cmd_list)

    # Stats command
    stats_parser = subparsers.add_parser(
        "stats",
        help="Show document ingestion statistics",
    )
    stats_parser.set_defaults(func=cmd_stats)

    # Delete command
    delete_parser = subparsers.add_parser(
        "delete",
        help="Delete a document",
    )
    delete_parser.add_argument(
        "document_id",
        type=int,
        help="Document ID to delete",
    )
    delete_parser.add_argument(
        "--delete-files",
        action="store_true",
        help="Delete associated files from disk",
    )
    delete_parser.add_argument(
        "--delete-gcs",
        action="store_true",
        help="Delete files from GCS",
    )
    delete_parser.add_argument(
        "--force",
        action="store_true",
        help="Skip confirmation prompt",
    )
    delete_parser.set_defaults(func=cmd_delete)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()

import { useState, useEffect } from 'react';
import { ChevronLeft } from 'lucide-react';

// ============================================
// PdfModal Component
// ============================================

interface PdfModalProps {
  open: boolean;
  page?: number;
  path?: string;
  onClose: () => void;
}

export function PdfModal({ open, page = 1, path, onClose }: PdfModalProps) {
  const [currentPage, setCurrentPage] = useState(page);

  useEffect(() => {
    setCurrentPage(page);
  }, [page]);

  if (!open) return null;

  const pdfPath = path || '../../data/documents/10_KeyTrends_2025_NNB.pdf';
  const pdfUrl = `${pdfPath}#page=${currentPage}`;

  return (
    <div
      className="modal-overlay modal-open"
      onClick={onClose}
      aria-hidden={!open}
    >
      <div
        className="modal-container pdf-modal-container"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="PDF Document"
      >
        <div className="modal-header">
          <div className="modal-title">{pdfPath.split('/').pop()}</div>
          <div className="pdf-page-info">Page {currentPage}</div>
          <button className="modal-close-btn" onClick={onClose} aria-label="Close PDF">
            ×
          </button>
        </div>
        <div className="pdf-viewer">
          <iframe src={pdfUrl} key={currentPage} />
        </div>
        <div className="pdf-nav">
          <button
            className="pdf-nav-btn"
            onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
            disabled={currentPage <= 1}
          >
            <ChevronLeft className="w-4 h-4 inline mr-1" />
            Previous
          </button>
          <button
            className="pdf-nav-btn"
            onClick={() => setCurrentPage((p) => p + 1)}
          >
            Next
            <ChevronLeft className="w-4 h-4 inline ml-1 rotate-180" />
          </button>
        </div>
      </div>
    </div>
  );
}

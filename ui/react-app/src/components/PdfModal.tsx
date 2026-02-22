import { X, FileText } from 'lucide-react';

interface PdfModalProps {
  open: boolean;
  pdfUrl: string | null;
  initialPage?: number;
  title?: string;
  cacheKey?: string;
  onClose: () => void;
}

/**
 * PDF viewer using browser's native PDF viewer via iframe.
 * - Chrome uses PDFium (C++ native, fast)
 * - Firefox uses built-in PDF.js
 * - Handles range requests natively for efficient loading
 * - Built-in zoom, search, navigation, print
 * - #page=N fragment navigates to initial page
 */
export function PdfModal({ open, pdfUrl, initialPage = 1, title, onClose }: PdfModalProps) {
  if (!open || !pdfUrl) return null;

  // Build the iframe URL with page fragment
  const iframeSrc = initialPage > 1 ? `${pdfUrl}#page=${initialPage}` : pdfUrl;

  return (
    <div className="modal-overlay modal-open" onClick={onClose}>
      <div
        className="modal-container pdf-modal-container"
        onClick={(e) => e.stopPropagation()}
        style={{
          width: '90vw',
          height: '90vh',
          maxWidth: '1400px',
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        {/* Header */}
        <div className="modal-header">
          <div className="flex items-center gap-2">
            <FileText className="w-5 h-5 text-teal-500" />
            <div className="modal-title">{title || 'PDF Document'}</div>
          </div>
          <button onClick={onClose} className="modal-close-btn" aria-label="Close PDF">
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Native PDF viewer via iframe */}
        <iframe
          src={iframeSrc}
          title={title || 'PDF Document'}
          style={{
            flex: 1,
            width: '100%',
            border: 'none',
            background: '#525252',
          }}
        />
      </div>
    </div>
  );
}

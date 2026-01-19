import { X, FileText, ChevronLeft, ChevronRight, ZoomIn, ZoomOut } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import * as pdfjsLib from 'pdfjs-dist';

// Configure PDF.js worker
pdfjsLib.GlobalWorkerOptions.workerSrc = '/pdf.worker.min.mjs';

interface PdfModalProps {
  open: boolean;
  pdfUrl: string | null;
  initialPage?: number;
  title?: string;
  onClose: () => void;
}

// Cache PDF documents globally to avoid reloading
const pdfCache = new Map<string, pdfjsLib.PDFDocumentProxy>();

/**
 * Ultra-fast PDF viewer using PDF.js directly
 * - Loads document once, keeps it in memory
 * - Instant page switching (< 50ms)
 * - Only renders current page
 */
export function PdfModal({ open, pdfUrl, initialPage = 1, title, onClose }: PdfModalProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [pdfDoc, setPdfDoc] = useState<pdfjsLib.PDFDocumentProxy | null>(null);
  const [currentPage, setCurrentPage] = useState(initialPage);
  const [numPages, setNumPages] = useState(0);
  const [scale, setScale] = useState(1.5);
  const [isLoading, setIsLoading] = useState(false);
  const loadingTaskRef = useRef<pdfjsLib.PDFDocumentLoadingTask | null>(null);

  // Load PDF document and handle page changes
  useEffect(() => {
    if (!open || !pdfUrl) return;

    // Check if document is already loaded
    if (pdfDoc && pdfCache.get(pdfUrl) === pdfDoc) {
      // Same document, just update page
      console.log('Same document, jumping to page:', initialPage);
      setCurrentPage(initialPage);
      return;
    }

    // Check cache first
    const cached = pdfCache.get(pdfUrl);
    if (cached) {
      console.log('Using cached PDF:', pdfUrl);
      setPdfDoc(cached);
      setNumPages(cached.numPages);
      setCurrentPage(initialPage);
      setIsLoading(false);
      return;
    }

    // Load new document
    console.log('Loading PDF document:', pdfUrl);
    setIsLoading(true);

    const loadingTask = pdfjsLib.getDocument({
      url: pdfUrl,
      disableAutoFetch: true, // Don't prefetch all pages
      disableStream: false,
    });

    loadingTaskRef.current = loadingTask;

    loadingTask.promise
      .then((pdf) => {
        console.log('PDF loaded:', pdf.numPages, 'pages');
        // Cache the document
        pdfCache.set(pdfUrl, pdf);
        setPdfDoc(pdf);
        setNumPages(pdf.numPages);
        setCurrentPage(initialPage);
        setIsLoading(false);
      })
      .catch((error) => {
        if (error.message !== 'Worker was destroyed') {
          console.error('Error loading PDF:', error);
        }
        setIsLoading(false);
      });

    // Don't destroy the loading task on unmount - let it complete
  }, [open, pdfUrl, initialPage, pdfDoc]);

  // Render current page
  useEffect(() => {
    if (!pdfDoc || !canvasRef.current || currentPage < 1 || currentPage > numPages) return;

    console.log('Rendering page:', currentPage);

    const renderPage = async () => {
      const page = await pdfDoc.getPage(currentPage);
      const canvas = canvasRef.current!;
      const context = canvas.getContext('2d')!;

      const viewport = page.getViewport({ scale });

      canvas.height = viewport.height;
      canvas.width = viewport.width;

      const renderContext = {
        canvasContext: context,
        viewport: viewport,
        canvas: canvas,
      };

      await page.render(renderContext).promise;
      console.log('Page rendered:', currentPage);
    };

    renderPage();
  }, [pdfDoc, currentPage, scale, numPages]);

  // Page navigation
  const goToPage = (page: number) => {
    if (page >= 1 && page <= numPages) {
      setCurrentPage(page);
    }
  };

  const nextPage = () => goToPage(currentPage + 1);
  const prevPage = () => goToPage(currentPage - 1);
  const zoomIn = () => setScale((s) => Math.min(3, s + 0.25));
  const zoomOut = () => setScale((s) => Math.max(0.5, s - 0.25));

  if (!open || !pdfUrl) return null;

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
          <div className="flex items-center gap-4">
            {numPages > 0 && (
              <span className="text-sm" style={{ color: '#94a3b8' }}>
                Page {currentPage} of {numPages}
              </span>
            )}
            <button onClick={onClose} className="modal-close-btn" aria-label="Close PDF">
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>

        {/* PDF Canvas */}
        <div
          style={{
            flex: 1,
            overflow: 'auto',
            background: '#525252',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            position: 'relative',
          }}
        >
          {isLoading ? (
            <div className="flex flex-col items-center gap-3">
              <div className="w-10 h-10 border-4 border-teal-500 border-t-transparent rounded-full animate-spin" />
              <span className="text-sm text-white">Loading PDF...</span>
            </div>
          ) : (
            <canvas
              ref={canvasRef}
              style={{
                maxWidth: '100%',
                maxHeight: '100%',
                boxShadow: '0 2px 8px rgba(0,0,0,0.3)',
              }}
            />
          )}
        </div>

        {/* Controls */}
        <div
          className="pdf-nav"
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            padding: '1rem',
            background: '#1e293b',
          }}
        >
          {/* Page Navigation */}
          <div className="flex items-center gap-2">
            <button
              onClick={prevPage}
              disabled={currentPage <= 1}
              className="pdf-nav-btn"
              style={{ opacity: currentPage <= 1 ? 0.5 : 1 }}
            >
              <ChevronLeft className="w-4 h-4" />
            </button>
            <input
              type="number"
              min={1}
              max={numPages}
              value={currentPage}
              onChange={(e) => goToPage(parseInt(e.target.value) || 1)}
              className="w-16 px-2 py-1 text-sm text-center rounded"
              style={{
                background: 'rgba(255,255,255,0.1)',
                color: '#e2e8f0',
                border: 'none',
              }}
            />
            <span className="text-sm text-gray-400">of {numPages}</span>
            <button
              onClick={nextPage}
              disabled={currentPage >= numPages}
              className="pdf-nav-btn"
              style={{ opacity: currentPage >= numPages ? 0.5 : 1 }}
            >
              <ChevronRight className="w-4 h-4" />
            </button>
          </div>

          {/* Zoom Controls */}
          <div className="flex items-center gap-2">
            <span className="text-sm text-gray-400">Zoom:</span>
            <button onClick={zoomOut} disabled={scale <= 0.5} className="pdf-nav-btn">
              <ZoomOut className="w-4 h-4" />
            </button>
            <span className="text-sm text-white min-w-[60px] text-center">
              {Math.round(scale * 100)}%
            </span>
            <button onClick={zoomIn} disabled={scale >= 3} className="pdf-nav-btn">
              <ZoomIn className="w-4 h-4" />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

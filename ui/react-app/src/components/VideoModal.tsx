// ============================================
// Helper Functions
// ============================================

function getYouTubeID(url: string): string | null {
  const regExp = /^.*(?:youtu\.be\/|v\/|u\/\w\/|embed\/|watch\?v=|[?&]v=)([^#&?]{11}).*/;
  const match = url.match(regExp);
  return match ? match[1] : null;
}

function isYouTubeURL(url: string | undefined): boolean {
  return /(?:youtube\.com|youtu\.be)/i.test(url || '');
}

// ============================================
// VideoModal Component
// ============================================

interface VideoModalProps {
  open: boolean;
  url?: string;
  start?: number;
  end?: number;
  title?: string;
  subtitle?: string;
  onClose: () => void;
}

export function VideoModal({ open, url, start = 0, end, title, subtitle, onClose }: VideoModalProps) {
  if (!open) return null;

  const videoId = url ? getYouTubeID(url) : null;
  const isValid = videoId && isYouTubeURL(url);

  const params = new URLSearchParams({
    autoplay: '1',
    enablejsapi: '1',
    start: String(Math.max(0, Math.floor(start))),
  });
  if (end !== undefined) {
    params.set('end', String(Math.max(0, Math.ceil(end))));
  }

  const embedUrl = isValid ? `https://www.youtube.com/embed/${videoId}?${params.toString()}` : '';

  return (
    <div
      className="modal-overlay modal-open"
      onClick={onClose}
      aria-hidden={!open}
    >
      <div
        className="modal-container video-modal-container"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="Video Evidence"
      >
        <div className="modal-header">
          <div className="modal-title">{title || 'Video Evidence'}</div>
          <button className="modal-close-btn" onClick={onClose} aria-label="Close video">
            ×
          </button>
        </div>
        <div className="video-content">
          {isValid ? (
            <iframe
              src={embedUrl}
              allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
              allowFullScreen
            />
          ) : (
            <div className="flex items-center justify-center h-full text-red-400 p-10">
              Invalid or missing YouTube URL.
            </div>
          )}
        </div>
        {subtitle && (
          <div className="video-info">{subtitle}</div>
        )}
      </div>
    </div>
  );
}

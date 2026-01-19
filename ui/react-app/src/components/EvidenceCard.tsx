import { useState } from 'react';
import { Play, FileText, Mic2, Clock } from 'lucide-react';
import { EvidenceItem } from '../types';
import { usePodcastEpisode, useDocument } from '../hooks/useMediaAccess';
import { useToast } from './common/Toast';
import { mediaApi } from '../api/media';

interface EvidenceCardProps {
  evidence: EvidenceItem;
  sourceType: 'podcast' | 'document';
  sourceId: number | null;
  onPlayAudio?: (url: string, startTime: number, title: string) => void;
  onOpenPdf?: (url: string, page: number, title: string) => void;
}

export function EvidenceCard({
  evidence,
  sourceType,
  sourceId,
  onPlayAudio,
  onOpenPdf,
}: EvidenceCardProps) {
  const [isLoading, setIsLoading] = useState(false);
  const { showToast } = useToast();

  // Use evidence's source_id if available, otherwise fall back to prop sourceId
  const actualSourceType = evidence.source_type || sourceType;
  const actualSourceId = evidence.source_id ?? sourceId;

  // Fetch source details
  const { data: podcast } = usePodcastEpisode(actualSourceType === 'podcast' ? actualSourceId : null);
  const { data: document } = useDocument(actualSourceType === 'document' ? actualSourceId : null);

  // Parse source name from source_ref
  const sourceName = parseSourceName(evidence.source_ref);
  const truncatedSource = truncateText(sourceName || 'Unknown Source', 30);

  // Format timestamp
  const formatTime = (seconds: number): string => {
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}:${secs.toString().padStart(2, '0')}`;
  };

  // Handle audio playback using proxy URL
  const handlePlayAudio = () => {
    if (!actualSourceId || evidence.start_time === undefined) return;

    try {
      setIsLoading(true);
      const proxyUrl = mediaApi.getProxyPodcastUrl(actualSourceId);
      const title = podcast
        ? `${podcast.podcast_name} - ${podcast.episode_title}`
        : 'Podcast Episode';

      onPlayAudio?.(proxyUrl, evidence.start_time, title);
      showToast('Audio ready to play', 'success');
    } catch (error) {
      console.error('Failed to play audio:', error);
      showToast('Failed to load audio', 'error');
    } finally {
      setIsLoading(false);
    }
  };

  // Handle PDF viewing using proxy URL
  const handleOpenPdf = () => {
    if (!actualSourceId || evidence.page === undefined) return;

    try {
      setIsLoading(true);
      const proxyUrl = mediaApi.getProxyDocumentUrl(actualSourceId);
      const title = document?.title || 'Document';

      onOpenPdf?.(proxyUrl, evidence.page, title);
      // Don't show loading toast - PDF modal will handle its own loading state
    } catch (error) {
      console.error('Failed to open PDF:', error);
      showToast('Failed to load document', 'error');
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="evidence-card">
      {/* Quote */}
      <p className="evidence-quote">"{truncateToWords(evidence.text, 50)}"</p>

      {/* Metadata */}
      <div className="evidence-metadata">
        <div className="evidence-source-info">
          {/* Source Type Icon */}
          {actualSourceType === 'podcast' ? (
            <Mic2 className="w-4 h-4 text-purple-500" />
          ) : (
            <FileText className="w-4 h-4 text-blue-500" />
          )}
          <span className="text-xs font-medium">
            {actualSourceType === 'podcast' ? 'Podcast' : 'Document'}
          </span>
          <span className="text-gray-300">•</span>
          <span className="text-xs text-gray-600 truncate" title={sourceName}>
            {truncatedSource}
          </span>
        </div>

        {/* Action + Details */}
        <div className="flex items-center gap-3">
          {/* Podcast: Timestamp + Play Button */}
          {actualSourceType === 'podcast' && evidence.start_time !== undefined && (
            <>
              <span className="evidence-timestamp">
                <Clock className="w-3 h-3 inline mr-1" />
                {formatTime(evidence.start_time)}
                {evidence.end_time && ` - ${formatTime(evidence.end_time)}`}
              </span>
              <button
                onClick={handlePlayAudio}
                disabled={isLoading || !actualSourceId}
                className={`
                  p-1.5 rounded-full transition-colors
                  ${isLoading || !actualSourceId ? 'opacity-50 cursor-not-allowed' : 'hover:bg-purple-100 text-purple-600'}
                `}
                title="Play audio at timestamp"
              >
                <Play className="w-4 h-4" />
              </button>
            </>
          )}

          {/* Document: Page + Click to Open */}
          {actualSourceType === 'document' && evidence.page !== undefined && (
            <button
              onClick={handleOpenPdf}
              disabled={isLoading || !actualSourceId}
              className={`
                evidence-page-link
                ${isLoading || !actualSourceId ? 'opacity-50 cursor-not-allowed' : ''}
              `}
              title="Open PDF at page"
            >
              <FileText className="w-3 h-3 inline mr-1" />
              Page {evidence.page}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

// Helper functions
function parseSourceName(sourceRef: string | undefined): string | undefined {
  if (!sourceRef) return undefined;
  const parts = sourceRef.split(' | ');
  return parts[0]?.trim();
}

function truncateText(text: string, maxLength: number): string {
  if (text.length <= maxLength) return text;
  return text.substring(0, maxLength - 1) + '…';
}

function truncateToWords(text: string, wordLimit: number): string {
  const words = text.trim().split(/\s+/);
  if (words.length <= wordLimit) return text;
  return words.slice(0, wordLimit).join(' ') + '…';
}

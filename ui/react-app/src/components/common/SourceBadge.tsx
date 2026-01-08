import { Mic, FileText } from 'lucide-react';
import clsx from 'clsx';

interface SourceBadgeProps {
  sourceType: 'podcast' | 'document' | string;
  size?: 'sm' | 'md';
}

export function SourceBadge({ sourceType, size = 'md' }: SourceBadgeProps) {
  const isPodcast = sourceType === 'podcast';

  return (
    <span
      className={clsx(
        'badge inline-flex items-center gap-1',
        isPodcast ? 'badge-podcast' : 'badge-document',
        size === 'sm' && 'text-xs px-2 py-0.5'
      )}
    >
      {isPodcast ? (
        <Mic className={clsx(size === 'sm' ? 'w-3 h-3' : 'w-4 h-4')} />
      ) : (
        <FileText className={clsx(size === 'sm' ? 'w-3 h-3' : 'w-4 h-4')} />
      )}
      {isPodcast ? 'Podcast' : 'Document'}
    </span>
  );
}

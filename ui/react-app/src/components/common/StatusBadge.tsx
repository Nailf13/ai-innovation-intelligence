import clsx from 'clsx';
import { TaskStatus } from '../../types';
import { Loader2, CheckCircle, XCircle, Clock } from 'lucide-react';

interface StatusBadgeProps {
  status: TaskStatus;
  size?: 'sm' | 'md';
}

const statusConfig: Record<TaskStatus, { label: string; className: string; icon: typeof Clock }> = {
  pending: {
    label: 'Pending',
    className: 'badge-pending',
    icon: Clock,
  },
  running: {
    label: 'Running',
    className: 'badge-running',
    icon: Loader2,
  },
  completed: {
    label: 'Completed',
    className: 'badge-completed',
    icon: CheckCircle,
  },
  failed: {
    label: 'Failed',
    className: 'badge-failed',
    icon: XCircle,
  },
};

export function StatusBadge({ status, size = 'md' }: StatusBadgeProps) {
  const config = statusConfig[status];
  const Icon = config.icon;

  return (
    <span
      className={clsx(
        'badge',
        config.className,
        size === 'sm' && 'text-xs px-2 py-0.5'
      )}
    >
      <Icon
        className={clsx(
          'mr-1',
          size === 'sm' ? 'w-3 h-3' : 'w-4 h-4',
          status === 'running' && 'animate-spin'
        )}
      />
      {config.label}
    </span>
  );
}

import clsx from 'clsx';

type AdoptionLevel =
  | 'Nascent experimentation'
  | 'Early adoption'
  | 'Crossing the chasm'
  | 'Established practice'
  | string;

interface AdoptionBadgeProps {
  level: AdoptionLevel;
  size?: 'sm' | 'md' | 'lg';
}

const adoptionConfig: Record<string, { label: string; color: string; bgColor: string }> = {
  'Nascent experimentation': {
    label: 'Emerging',
    color: 'text-yellow-700',
    bgColor: 'bg-yellow-100',
  },
  'Early adoption': {
    label: 'Early Adoption',
    color: 'text-orange-700',
    bgColor: 'bg-orange-100',
  },
  'Crossing the chasm': {
    label: 'Growing',
    color: 'text-blue-700',
    bgColor: 'bg-blue-100',
  },
  'Established practice': {
    label: 'Mainstream',
    color: 'text-green-700',
    bgColor: 'bg-green-100',
  },
};

export function AdoptionBadge({ level, size = 'md' }: AdoptionBadgeProps) {
  const config = adoptionConfig[level] || {
    label: level,
    color: 'text-gray-700',
    bgColor: 'bg-gray-100',
  };

  return (
    <span
      className={clsx(
        'inline-flex items-center rounded-full font-medium',
        config.color,
        config.bgColor,
        size === 'sm' && 'px-2 py-0.5 text-xs',
        size === 'md' && 'px-2.5 py-1 text-xs',
        size === 'lg' && 'px-3 py-1 text-sm'
      )}
    >
      <span
        className={clsx(
          'w-2 h-2 rounded-full mr-1.5',
          level === 'Nascent experimentation' && 'bg-yellow-500',
          level === 'Early adoption' && 'bg-orange-500',
          level === 'Crossing the chasm' && 'bg-blue-500',
          level === 'Established practice' && 'bg-green-500',
          !adoptionConfig[level] && 'bg-gray-500'
        )}
      />
      {config.label}
    </span>
  );
}

// Legend component for showing all adoption levels
export function AdoptionLegend() {
  const levels: AdoptionLevel[] = [
    'Nascent experimentation',
    'Early adoption',
    'Crossing the chasm',
    'Established practice',
  ];

  return (
    <div className="flex flex-wrap gap-2">
      {levels.map((level) => (
        <AdoptionBadge key={level} level={level} size="sm" />
      ))}
    </div>
  );
}

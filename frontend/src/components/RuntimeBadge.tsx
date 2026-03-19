interface RuntimeBadgeProps {
  runtime: string;
  size?: 'sm' | 'md';
}

const RUNTIME_CONFIG: Record<string, { label: string; color: string; icon: string }> = {
  claude_code: {
    label: 'Claude Code',
    color: '#f5a623',
    icon: '\u{1f9e0}',
  },
  openclaw: {
    label: 'OpenClaw',
    color: '#22c55e',
    icon: '\u{1f980}',
  },
  bash: {
    label: 'Bash',
    color: '#22d3ee',
    icon: '\u{1f4bb}',
  },
  http: {
    label: 'HTTP',
    color: '#a78bfa',
    icon: '\u{1f310}',
  },
};

export default function RuntimeBadge({ runtime, size = 'sm' }: RuntimeBadgeProps) {
  const config = RUNTIME_CONFIG[runtime] || {
    label: runtime,
    color: '#8b90a5',
    icon: '\u{2699}\u{fe0f}',
  };

  const fontSize = size === 'sm' ? '0.625rem' : '0.75rem';
  const padding = size === 'sm' ? '2px 6px' : '3px 8px';

  return (
    <span
      className="inline-flex items-center gap-1 rounded-full font-medium font-mono"
      style={{
        fontSize,
        padding,
        background: `${config.color}15`,
        color: config.color,
        border: `1px solid ${config.color}30`,
      }}
    >
      <span>{config.icon}</span>
      {config.label}
    </span>
  );
}

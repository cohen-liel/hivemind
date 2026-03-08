// Shared agent constants — single source of truth
export const AGENT_ICONS: Record<string, string> = {
  orchestrator: '\u{1F3AF}',
  developer: '\u{1F4BB}',
  reviewer: '\u{1F50D}',
  tester: '\u{1F9EA}',
  devops: '\u{2699}\uFE0F',
};

export const AGENT_LABELS: Record<string, string> = {
  developer: 'Developer',
  reviewer: 'Reviewer',
  tester: 'Tester',
  devops: 'DevOps',
  orchestrator: 'Orchestrator',
};

export const AGENT_COLORS: Record<string, { border: string; bg: string; text: string }> = {
  developer: { border: 'border-l-blue-500', bg: 'bg-blue-500', text: 'text-blue-400' },
  reviewer: { border: 'border-l-purple-500', bg: 'bg-purple-500', text: 'text-purple-400' },
  tester: { border: 'border-l-amber-500', bg: 'bg-amber-500', text: 'text-amber-400' },
  devops: { border: 'border-l-cyan-500', bg: 'bg-cyan-500', text: 'text-cyan-400' },
  orchestrator: { border: 'border-l-gray-500', bg: 'bg-gray-500', text: 'text-gray-400' },
};

/**
 * Format a Unix timestamp to a time string.
 * @param ts Unix timestamp in seconds
 * @param showSeconds Whether to include seconds
 */
export function formatTime(ts: number, showSeconds = false): string {
  const opts: Intl.DateTimeFormatOptions = { hour: '2-digit', minute: '2-digit' };
  if (showSeconds) opts.second = '2-digit';
  return new Date(ts * 1000).toLocaleTimeString([], opts);
}

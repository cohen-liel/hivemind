// Shared agent constants — single source of truth
// Matches the backend AGENT_EMOJI map and DEFAULT_AGENTS list in config.py

export const AGENT_ICONS: Record<string, string> = {
  // Layer 1: Brain
  orchestrator:       '\u{1F3AF}',  // 🎯
  pm:                 '\u{1F9E0}',  // 🧠
  memory:             '\u{1F4DA}',  // 📚
  // Layer 2: Execution
  developer:          '\u{1F4BB}',  // 💻
  frontend_developer: '\u{1F3A8}',  // 🎨
  backend_developer:  '\u{26A1}',   // ⚡
  database_expert:    '\u{1F5C4}\uFE0F', // 🗄️
  devops:             '\u{1F680}',  // 🚀
  // Layer 3: Quality
  reviewer:           '\u{1F50D}',  // 🔍
  security_auditor:   '\u{1F510}',  // 🔐
  test_engineer:      '\u{1F9EA}',  // 🧪
  tester:             '\u{1F9EA}',  // 🧪 (legacy alias)
  researcher:         '\u{1F50E}',  // 🔎
  ux_critic:          '\u{1F3AD}',  // 🎭
  // Special
  user:               '\u{1F464}',  // 👤
};

export const AGENT_LABELS: Record<string, string> = {
  orchestrator:       'Orchestrator',
  pm:                 'PM',
  memory:             'Memory',
  developer:          'Developer',
  frontend_developer: 'Frontend',
  backend_developer:  'Backend',
  database_expert:    'Database',
  devops:             'DevOps',
  reviewer:           'Reviewer',
  security_auditor:   'Security',
  test_engineer:      'Tester',
  tester:             'Tester',
  researcher:         'Researcher',
  ux_critic:          'UX',
};

export const AGENT_COLORS: Record<string, { border: string; bg: string; text: string }> = {
  developer:          { border: 'border-l-blue-500',    bg: 'bg-blue-500',    text: 'text-blue-400' },
  frontend_developer: { border: 'border-l-pink-500',    bg: 'bg-pink-500',    text: 'text-pink-400' },
  backend_developer:  { border: 'border-l-yellow-500',  bg: 'bg-yellow-500',  text: 'text-yellow-400' },
  database_expert:    { border: 'border-l-indigo-500',  bg: 'bg-indigo-500',  text: 'text-indigo-400' },
  reviewer:           { border: 'border-l-purple-500',  bg: 'bg-purple-500',  text: 'text-purple-400' },
  tester:             { border: 'border-l-amber-500',   bg: 'bg-amber-500',   text: 'text-amber-400' },
  test_engineer:      { border: 'border-l-amber-500',   bg: 'bg-amber-500',   text: 'text-amber-400' },
  devops:             { border: 'border-l-cyan-500',    bg: 'bg-cyan-500',    text: 'text-cyan-400' },
  researcher:         { border: 'border-l-emerald-500', bg: 'bg-emerald-500', text: 'text-emerald-400' },
  security_auditor:   { border: 'border-l-red-500',     bg: 'bg-red-500',     text: 'text-red-400' },
  ux_critic:          { border: 'border-l-fuchsia-500', bg: 'bg-fuchsia-500', text: 'text-fuchsia-400' },
  memory:             { border: 'border-l-teal-500',    bg: 'bg-teal-500',    text: 'text-teal-400' },
  pm:                 { border: 'border-l-orange-500',  bg: 'bg-orange-500',  text: 'text-orange-400' },
  orchestrator:       { border: 'border-l-gray-500',    bg: 'bg-gray-500',    text: 'text-gray-400' },
};

/** Accent colors for per-agent styling (used by AgentStatusPanel, ConductorMode, PlanView) */
export const AGENT_ACCENTS: Record<string, { color: string; glow: string; bg: string }> = {
  developer:          { color: '#638cff', glow: 'rgba(99,140,255,0.2)',  bg: 'rgba(99,140,255,0.06)' },
  frontend_developer: { color: '#ec4899', glow: 'rgba(236,72,153,0.2)', bg: 'rgba(236,72,153,0.06)' },
  backend_developer:  { color: '#eab308', glow: 'rgba(234,179,8,0.2)',  bg: 'rgba(234,179,8,0.06)' },
  database_expert:    { color: '#6366f1', glow: 'rgba(99,102,241,0.2)', bg: 'rgba(99,102,241,0.06)' },
  reviewer:           { color: '#a78bfa', glow: 'rgba(167,139,250,0.2)', bg: 'rgba(167,139,250,0.06)' },
  tester:             { color: '#f5a623', glow: 'rgba(245,166,35,0.2)',  bg: 'rgba(245,166,35,0.06)' },
  test_engineer:      { color: '#f5a623', glow: 'rgba(245,166,35,0.2)',  bg: 'rgba(245,166,35,0.06)' },
  devops:             { color: '#22d3ee', glow: 'rgba(34,211,238,0.2)',  bg: 'rgba(34,211,238,0.06)' },
  researcher:         { color: '#34d399', glow: 'rgba(52,211,153,0.2)',  bg: 'rgba(52,211,153,0.06)' },
  security_auditor:   { color: '#ef4444', glow: 'rgba(239,68,68,0.2)',   bg: 'rgba(239,68,68,0.06)' },
  ux_critic:          { color: '#d946ef', glow: 'rgba(217,70,239,0.2)',  bg: 'rgba(217,70,239,0.06)' },
  memory:             { color: '#14b8a6', glow: 'rgba(20,184,166,0.2)',  bg: 'rgba(20,184,166,0.06)' },
  pm:                 { color: '#f97316', glow: 'rgba(249,115,22,0.2)',  bg: 'rgba(249,115,22,0.06)' },
  orchestrator:       { color: '#8b90a5', glow: 'rgba(139,144,165,0.15)', bg: 'rgba(139,144,165,0.05)' },
};

export function getAgentAccent(name: string) {
  return AGENT_ACCENTS[name] || AGENT_ACCENTS.orchestrator;
}

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

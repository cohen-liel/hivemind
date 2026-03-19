/**
 * Agent Registry — Frontend Single Source of Truth
 *
 * Fetches agent metadata from the backend AGENT_REGISTRY via /api/agent-registry.
 * Provides derived maps (AGENT_ICONS, AGENT_LABELS, AGENT_COLORS, AGENT_ACCENTS)
 * that are populated on first load and used throughout the UI.
 *
 * Fallback: if the API is unreachable, uses hardcoded defaults so the UI
 * still works offline or during development.
 */

// ── Types ──────────────────────────────────────────────────────────

interface AgentMeta {
  emoji: string;
  label: string;
  layer: string;
  legacy: boolean;
  tw_color: string;
  accent: string;
}

interface WsConfig {
  keepalive_interval_ms: number;
  reconnect_base_delay_ms: number;
  reconnect_max_delay_ms: number;
}

interface RegistryResponse {
  agents: Record<string, AgentMeta>;
  ws: WsConfig;
}

// ── Hardcoded fallback (used if API is unreachable) ────────────────

const FALLBACK_AGENTS: Record<string, AgentMeta> = {
  pm:                 { emoji: '🧠', label: 'PM',           layer: 'brain',     legacy: false, tw_color: 'orange',  accent: '#f97316' },
  orchestrator:       { emoji: '🎯', label: 'Orchestrator', layer: 'brain',     legacy: false, tw_color: 'gray',    accent: '#8b90a5' },
  memory:             { emoji: '📚', label: 'Memory',       layer: 'brain',     legacy: false, tw_color: 'teal',    accent: '#14b8a6' },
  frontend_developer: { emoji: '🎨', label: 'Frontend',     layer: 'execution', legacy: false, tw_color: 'pink',    accent: '#ec4899' },
  backend_developer:  { emoji: '⚡', label: 'Backend',      layer: 'execution', legacy: false, tw_color: 'yellow',  accent: '#eab308' },
  database_expert:    { emoji: '🗄️', label: 'Database',     layer: 'execution', legacy: false, tw_color: 'indigo',  accent: '#6366f1' },
  devops:             { emoji: '🚀', label: 'DevOps',       layer: 'execution', legacy: false, tw_color: 'cyan',    accent: '#22d3ee' },
  security_auditor:   { emoji: '🔐', label: 'Security',     layer: 'quality',   legacy: false, tw_color: 'red',     accent: '#ef4444' },
  test_engineer:      { emoji: '🧪', label: 'Tester',       layer: 'quality',   legacy: false, tw_color: 'amber',   accent: '#f5a623' },
  reviewer:           { emoji: '🔍', label: 'Reviewer',     layer: 'quality',   legacy: false, tw_color: 'purple',  accent: '#a78bfa' },
  researcher:         { emoji: '🔎', label: 'Researcher',   layer: 'quality',   legacy: false, tw_color: 'emerald', accent: '#34d399' },
  ux_critic:          { emoji: '🎭', label: 'UX',           layer: 'quality',   legacy: false, tw_color: 'fuchsia', accent: '#d946ef' },
  developer:          { emoji: '💻', label: 'Developer',    layer: 'execution', legacy: true,  tw_color: 'blue',    accent: '#638cff' },
  tester:             { emoji: '🧪', label: 'Tester',       layer: 'quality',   legacy: true,  tw_color: 'amber',   accent: '#f5a623' },
  user:               { emoji: '👤', label: 'User',         layer: 'special',   legacy: false, tw_color: 'gray',    accent: '#8b90a5' },
};

const FALLBACK_WS: WsConfig = {
  keepalive_interval_ms: 10_000,
  reconnect_base_delay_ms: 1_000,
  reconnect_max_delay_ms: 30_000,
};

// ── Mutable state (populated by init()) ───────────────────────────

let _agents: Record<string, AgentMeta> = { ...FALLBACK_AGENTS };
let _ws: WsConfig = { ...FALLBACK_WS };
let _initialized = false;

// ── Helper: convert hex to rgba ───────────────────────────────────

function hexToRgba(hex: string, alpha: number): string {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}

// ── Public API ────────────────────────────────────────────────────

/**
 * Initialize the registry by fetching from the backend.
 * Safe to call multiple times — only fetches once.
 */
export async function initAgentRegistry(): Promise<void> {
  if (_initialized) return;
  try {
    // Include auth headers for environments where device auth is enabled.
    // The endpoint is auth-exempt on the backend, but including the token
    // ensures compatibility with any future auth changes or proxies.
    const headers: Record<string, string> = {};
    const meta = document.querySelector<HTMLMetaElement>('meta[name="hivemind-auth-token"]');
    const token = meta?.content || (() => {
      try { return localStorage.getItem('hivemind-auth-token') || ''; } catch { return ''; }
    })();
    if (token) {
      headers['X-API-Key'] = token;
    }

    const resp = await fetch('/api/agent-registry', { headers });
    if (resp.ok) {
      const data: RegistryResponse = await resp.json();
      _agents = { ...FALLBACK_AGENTS, ...data.agents };
      _ws = data.ws;
      _initialized = true;
    }
  } catch {
    // API unreachable — use fallback silently
    console.warn('[AgentRegistry] API unreachable, using fallback data');
  }
}

/** Get the emoji icon for an agent role */
export function getAgentIcon(name: string): string {
  return _agents[name]?.emoji ?? '🤖';
}

/** Get the display label for an agent role */
export function getAgentLabel(name: string): string {
  return _agents[name]?.label ?? name;
}

/** Get Tailwind color classes for an agent role */
export function getAgentColors(name: string): { border: string; bg: string; text: string } {
  const color = _agents[name]?.tw_color ?? 'blue';
  return {
    border: `border-l-${color}-500`,
    bg: `bg-${color}-500`,
    text: `text-${color}-400`,
  };
}

/** Get accent colors for an agent role (hex + rgba for glow/bg) */
export function getAgentAccent(name: string): { color: string; glow: string; bg: string } {
  const accent = _agents[name]?.accent ?? '#8b90a5';
  return {
    color: accent,
    glow: hexToRgba(accent, 0.2),
    bg: hexToRgba(accent, 0.06),
  };
}

/** Get all agent metadata (for iteration) */
export function getAllAgents(): Record<string, AgentMeta> {
  return _agents;
}

/** Get WebSocket timing configuration */
export function getWsConfig(): WsConfig {
  return _ws;
}

// ── Backward-compatible static maps ───────────────────────────────
// These are exported for components that still use the old Record<string, X> pattern.
// They are derived from the registry and stay in sync.

export function getAgentIcons(): Record<string, string> {
  const result: Record<string, string> = {};
  for (const [role, meta] of Object.entries(_agents)) {
    result[role] = meta.emoji;
  }
  return result;
}

export function getAgentLabels(): Record<string, string> {
  const result: Record<string, string> = {};
  for (const [role, meta] of Object.entries(_agents)) {
    result[role] = meta.label;
  }
  return result;
}

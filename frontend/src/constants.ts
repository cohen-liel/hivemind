/**
 * Shared agent constants — derived from the backend AGENT_REGISTRY.
 *
 * These exports maintain backward compatibility with existing components.
 * The actual data comes from agentRegistry.ts which fetches from /api/agent-registry.
 *
 * For new code, prefer importing directly from agentRegistry.ts:
 *   import { getAgentIcon, getAgentLabel, getAgentAccent } from './agentRegistry';
 */

import {
  getAgentIcons,
  getAgentLabels,
  getAgentAccent as _getAgentAccent,
  getAgentColors,
} from './agentRegistry';

// ── Backward-compatible static-like exports ───────────────────────
// These are getter-based so they always reflect the latest registry data.
// Components that import AGENT_ICONS['developer'] will still work.

/** Agent emoji icons — derived from AGENT_REGISTRY */
export const AGENT_ICONS: Record<string, string> = new Proxy(
  {} as Record<string, string>,
  {
    get(_target, prop: string) {
      return getAgentIcons()[prop] ?? '🤖';
    },
    ownKeys() {
      return Object.keys(getAgentIcons());
    },
    getOwnPropertyDescriptor(_target, prop: string) {
      const icons = getAgentIcons();
      if (prop in icons) {
        return { configurable: true, enumerable: true, value: icons[prop] };
      }
      return undefined;
    },
    has(_target, prop: string) {
      return prop in getAgentIcons();
    },
  }
);

/** Agent display labels — derived from AGENT_REGISTRY */
export const AGENT_LABELS: Record<string, string> = new Proxy(
  {} as Record<string, string>,
  {
    get(_target, prop: string) {
      return getAgentLabels()[prop] ?? prop;
    },
    ownKeys() {
      return Object.keys(getAgentLabels());
    },
    getOwnPropertyDescriptor(_target, prop: string) {
      const labels = getAgentLabels();
      if (prop in labels) {
        return { configurable: true, enumerable: true, value: labels[prop] };
      }
      return undefined;
    },
    has(_target, prop: string) {
      return prop in getAgentLabels();
    },
  }
);

/** Agent Tailwind color classes — derived from AGENT_REGISTRY */
export const AGENT_COLORS: Record<string, { border: string; bg: string; text: string }> = new Proxy(
  {} as Record<string, { border: string; bg: string; text: string }>,
  {
    get(_target, prop: string) {
      return getAgentColors(prop);
    },
  }
);

/** Accent colors for per-agent styling — derived from AGENT_REGISTRY */
export const AGENT_ACCENTS: Record<string, { color: string; glow: string; bg: string }> = new Proxy(
  {} as Record<string, { color: string; glow: string; bg: string }>,
  {
    get(_target, prop: string) {
      return _getAgentAccent(prop);
    },
  }
);

/** Get accent colors for an agent (with fallback) */
export function getAgentAccent(name: string) {
  return _getAgentAccent(name);
}

// ── Shared numeric constants ────────────────────────────────────────
// Time formatting
export const SECONDS_PER_MINUTE = 60;

// LiveStatusStrip: threshold (ms) after which an agent is considered "stale"
export const STALE_THRESHOLD_MS = 90_000;

// AgentOrchestraViz: debounce delay (ms) for center status text updates
export const STATUS_DEBOUNCE_MS = 500;

// Inline tool-display max widths (px)
export const TOOL_MAX_WIDTH_SM = 200;
export const TOOL_MAX_WIDTH_MD = 250;
export const TOOL_MAX_WIDTH_LG = 300;

// AgentOrchestraViz center foreignObject dimensions
export const CENTER_LABEL_WIDTH = 120;
export const CENTER_LABEL_MAX_CHARS = 32;

// HivemindTabContent: max working-agent avatars shown in the summary strip
export const MAX_WORKING_AVATARS = 5;

// NewProjectDialog: stagger delay (ms) between swarm agent entrance animations
export const SWARM_STAGGER_DELAY_MS = 60;

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

/** Format elapsed seconds as human-readable duration string (e.g. "2m30s" or "45s") */
export function formatElapsed(sec: number): string {
  if (sec >= SECONDS_PER_MINUTE) {
    return `${Math.floor(sec / SECONDS_PER_MINUTE)}m${sec % SECONDS_PER_MINUTE}s`;
  }
  return `${sec}s`;
}

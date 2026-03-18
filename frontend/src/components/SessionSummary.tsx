/**
 * SessionSummary — Card shown at the bottom of the activity feed when a
 * session finishes.  Displays the orchestrator's final summary message plus
 * high-level stats (turns, cost) so the user can see what was accomplished
 * at a glance — similar to how Claude Code shows a text summary at the end.
 *
 * Rendered inside ActivityPanel (desktop) and the mobile equivalent.
 * Hidden while the project is running or has never run.
 */

import React, { useEffect, useRef, useState, useCallback } from 'react';
import { getSessionSummary } from '../api';
import type { SessionSummary as SessionSummaryData } from '../api';

// ── Lightweight markdown renderer ────────────────────────────────────────────
// Reuses the same inline-markdown strategy as ActivityFeed (no external deps).

function renderInline(text: string, key: string): React.ReactNode[] {
  const parts = text.split(/(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*)/g);
  return parts.map((part, i) => {
    if (part.startsWith('`') && part.endsWith('`') && part.length > 2) {
      return (
        <code
          key={`${key}-ic-${i}`}
          style={{
            background: 'rgba(0,0,0,0.35)',
            color: 'var(--accent-cyan)',
            fontFamily: 'var(--font-mono)',
            padding: '1px 5px',
            borderRadius: 4,
            fontSize: '0.8em',
          }}
        >
          {part.slice(1, -1)}
        </code>
      );
    }
    if (part.startsWith('**') && part.endsWith('**') && part.length > 4) {
      return (
        <strong key={`${key}-b-${i}`} style={{ color: 'var(--text-primary)', fontWeight: 600 }}>
          {part.slice(2, -2)}
        </strong>
      );
    }
    if (part.startsWith('*') && part.endsWith('*') && part.length > 2) {
      return <em key={`${key}-em-${i}`}>{part.slice(1, -1)}</em>;
    }
    return part.split('\n').map((line, li, arr) => (
      <React.Fragment key={`${key}-l-${i}-${li}`}>
        {line}
        {li < arr.length - 1 && <br />}
      </React.Fragment>
    ));
  });
}

function renderSummaryMarkdown(text: string): React.ReactNode[] {
  const parts = text.split(/(```[\s\S]*?```)/g);
  return parts.map((part, i) => {
    if (part.startsWith('```') && part.endsWith('```')) {
      const inner = part.slice(3, -3);
      const nlIdx = inner.indexOf('\n');
      const lang = nlIdx >= 0 ? inner.slice(0, nlIdx).trim() : '';
      const code = nlIdx >= 0 ? inner.slice(nlIdx + 1) : inner;
      return (
        <pre
          key={i}
          style={{
            background: 'rgba(0,0,0,0.4)',
            border: '1px solid var(--border-dim)',
            color: 'var(--text-primary)',
            fontFamily: 'var(--font-mono)',
            fontSize: 12,
            borderRadius: 8,
            padding: '10px 14px',
            margin: '6px 0',
            overflowX: 'auto',
            whiteSpace: 'pre',
          }}
        >
          {lang && (
            <div
              style={{
                color: 'var(--text-muted)',
                fontSize: 10,
                marginBottom: 6,
                textTransform: 'uppercase',
                letterSpacing: '0.08em',
                fontFamily: 'var(--font-display)',
              }}
            >
              {lang}
            </div>
          )}
          {code}
        </pre>
      );
    }
    return <React.Fragment key={i}>{renderInline(part, String(i))}</React.Fragment>;
  });
}

// ── Stat pill ────────────────────────────────────────────────────────────────

function StatPill({ label, value }: { label: string; value: string }): React.ReactElement {
  return (
    <div
      className="flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px]"
      style={{
        background: 'var(--bg-elevated)',
        border: '1px solid var(--border-subtle)',
        color: 'var(--text-secondary)',
        fontFamily: 'var(--font-mono)',
      }}
    >
      <span style={{ color: 'var(--text-muted)' }}>{label}</span>
      <span style={{ color: 'var(--text-primary)', fontWeight: 600 }}>{value}</span>
    </div>
  );
}

// ── Main component ───────────────────────────────────────────────────────────

export interface SessionSummaryProps {
  projectId: string;
  /** Current project status — used to decide when to show the card */
  projectStatus: string;
}

const SessionSummary = React.memo(function SessionSummary({
  projectId,
  projectStatus,
}: SessionSummaryProps): React.ReactElement | null {
  const [data, setData] = useState<SessionSummaryData | null>(null);
  const [loading, setLoading] = useState(false);
  const [prevStatus, setPrevStatus] = useState(projectStatus);

  // Persist dismissed state in localStorage so it survives page reloads.
  // Key is scoped to projectId so dismissing one project doesn't affect others.
  const dismissKey = `hivemind:session-dismissed:${projectId}`;
  const [dismissed, setDismissed] = useState(() => {
    try { return localStorage.getItem(dismissKey) === 'true'; }
    catch { return false; }
  });

  const handleDismiss = useCallback(() => {
    setDismissed(true);
    try { localStorage.setItem(dismissKey, 'true'); } catch { /* noop */ }
  }, [dismissKey]);

  // Determine whether we should show the summary card.
  // Show when status is "idle" or "completed" (i.e. after having run something).
  const shouldShow =
    !dismissed &&
    data !== null &&
    data.summary_text != null &&
    (projectStatus === 'idle' || projectStatus === 'completed' || projectStatus === 'stopped');

  const fetchSummary = useCallback(async () => {
    if (!projectId) return;
    setLoading(true);
    try {
      const result = await getSessionSummary(projectId);
      setData(result);
    } catch {
      // Non-critical — silently ignore
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  // Fetch when the project transitions from running → idle/completed/stopped
  useEffect(() => {
    const wasRunning = prevStatus === 'running';
    const isNowDone =
      projectStatus === 'idle' || projectStatus === 'completed' || projectStatus === 'stopped';

    if (wasRunning && isNowDone) {
      // New session completed — clear the old dismiss flag so the new summary shows
      setDismissed(false);
      try { localStorage.removeItem(dismissKey); } catch { /* noop */ }
      fetchSummary();
    }

    setPrevStatus(projectStatus);
  }, [projectStatus, prevStatus, fetchSummary]);

  // Auto-scroll the feed container to show the summary when it appears
  const cardRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (shouldShow && cardRef.current) {
      cardRef.current.scrollIntoView({ behavior: 'smooth', block: 'end' });
    }
  }, [shouldShow]);

  // Also fetch on initial mount if project is already idle/completed
  useEffect(() => {
    if (projectStatus === 'idle' || projectStatus === 'completed' || projectStatus === 'stopped') {
      fetchSummary();
    }
    // Only on mount
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (loading) {
    return null; // silent loading — don't flash a skeleton
  }

  if (!shouldShow) {
    return null;
  }

  const summaryText = data!.summary_text!;
  const turnCount = data!.turn_count;
  const costUsd = data!.total_cost_usd;

  return (
    <div
      ref={cardRef}
      className="mx-3 mb-3 rounded-2xl overflow-hidden animate-[fadeSlideIn_0.35s_ease-out_both]"
      style={{
        background: 'var(--bg-card)',
        border: '1px solid rgba(61, 214, 140, 0.2)',
        boxShadow: '0 4px 24px rgba(61, 214, 140, 0.06)',
      }}
      role="region"
      aria-label="Session complete summary"
    >
      {/* Green accent stripe at top */}
      <div
        className="h-0.5 w-full"
        style={{ background: 'linear-gradient(90deg, var(--accent-green), var(--accent-cyan))' }}
      />

      {/* Header row */}
      <div
        className="flex items-center justify-between px-4 py-2.5"
        style={{ borderBottom: '1px solid var(--border-dim)' }}
      >
        <div className="flex items-center gap-2">
          <span
            className="text-xs font-bold uppercase tracking-widest"
            style={{ color: 'var(--accent-green)', fontFamily: 'var(--font-mono)' }}
          >
            Session Complete
          </span>
        </div>

        <button
          onClick={handleDismiss}
          className="w-5 h-5 rounded flex items-center justify-center transition-colors focus:outline-none focus-visible:ring-2"
          style={{ color: 'var(--text-muted)' }}
          aria-label="Dismiss session summary"
          onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--text-primary)'; e.currentTarget.style.background = 'var(--bg-elevated)'; }}
          onMouseLeave={(e) => { e.currentTarget.style.color = 'var(--text-muted)'; e.currentTarget.style.background = 'transparent'; }}
        >
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
            <path d="M18 6 6 18M6 6l12 12" />
          </svg>
        </button>
      </div>

      {/* Summary body — parent scroll container handles overflow */}
      <div
        className="px-4 py-3 text-sm leading-relaxed"
        style={{
          color: 'var(--text-secondary)',
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
          fontFamily: 'var(--font-display)',
        }}
      >
        {renderSummaryMarkdown(summaryText)}
      </div>

      {/* Stats footer */}
      <div
        className="flex flex-wrap items-center gap-2 px-4 py-2.5"
        style={{ borderTop: '1px solid var(--border-dim)' }}
      >
        {turnCount > 0 && (
          <StatPill label="turns" value={String(turnCount)} />
        )}
        {costUsd > 0 && (
          <StatPill label="cost" value={`$${costUsd.toFixed(4)}`} />
        )}
      </div>
    </div>
  );
});

export default SessionSummary;

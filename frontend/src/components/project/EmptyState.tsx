/**
 * EmptyState — Empty state components for ProjectView panels.
 *
 * Shows contextual messages with SVG illustrations and entrance
 * animations when panels have no data to display.
 */

import React, { useEffect, useState } from 'react';

// ============================================================================
// SVG Illustration Components
// ============================================================================

function ActivityIcon(): React.ReactElement {
  return (
    <svg width="40" height="40" viewBox="0 0 40 40" fill="none" aria-hidden="true">
      <rect x="4" y="8" width="32" height="24" rx="4" stroke="var(--accent-blue)" strokeWidth="1.5" opacity="0.4" />
      <line x1="10" y1="16" x2="24" y2="16" stroke="var(--accent-blue)" strokeWidth="1.5" strokeLinecap="round" opacity="0.6" />
      <line x1="10" y1="20" x2="30" y2="20" stroke="var(--accent-blue)" strokeWidth="1.5" strokeLinecap="round" opacity="0.35" />
      <line x1="10" y1="24" x2="18" y2="24" stroke="var(--accent-blue)" strokeWidth="1.5" strokeLinecap="round" opacity="0.2" />
      <circle cx="32" cy="10" r="4" fill="var(--accent-green)" opacity="0.7" />
    </svg>
  );
}

function CodeIcon(): React.ReactElement {
  return (
    <svg width="40" height="40" viewBox="0 0 40 40" fill="none" aria-hidden="true">
      <rect x="6" y="6" width="28" height="28" rx="3" stroke="var(--accent-purple)" strokeWidth="1.5" opacity="0.35" />
      <path d="M15 16l-4 4 4 4" stroke="var(--accent-purple)" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" opacity="0.7" />
      <path d="M25 16l4 4-4 4" stroke="var(--accent-purple)" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" opacity="0.7" />
      <line x1="22" y1="13" x2="18" y2="27" stroke="var(--accent-cyan)" strokeWidth="1.5" strokeLinecap="round" opacity="0.5" />
    </svg>
  );
}

function TraceIcon(): React.ReactElement {
  return (
    <svg width="40" height="40" viewBox="0 0 40 40" fill="none" aria-hidden="true">
      <circle cx="20" cy="20" r="13" stroke="var(--accent-cyan)" strokeWidth="1.5" opacity="0.25" />
      <circle cx="20" cy="20" r="7" stroke="var(--accent-cyan)" strokeWidth="1.5" opacity="0.4" />
      <circle cx="20" cy="20" r="2" fill="var(--accent-cyan)" opacity="0.8" />
      <line x1="20" y1="3" x2="20" y2="9" stroke="var(--accent-cyan)" strokeWidth="1" strokeLinecap="round" opacity="0.3" />
      <line x1="20" y1="31" x2="20" y2="37" stroke="var(--accent-cyan)" strokeWidth="1" strokeLinecap="round" opacity="0.3" />
      <line x1="3" y1="20" x2="9" y2="20" stroke="var(--accent-cyan)" strokeWidth="1" strokeLinecap="round" opacity="0.3" />
      <line x1="31" y1="20" x2="37" y2="20" stroke="var(--accent-cyan)" strokeWidth="1" strokeLinecap="round" opacity="0.3" />
    </svg>
  );
}

function PlanIcon(): React.ReactElement {
  return (
    <svg width="40" height="40" viewBox="0 0 40 40" fill="none" aria-hidden="true">
      {/* DAG nodes */}
      <circle cx="20" cy="8" r="4" stroke="var(--accent-blue)" strokeWidth="1.5" opacity="0.6" />
      <circle cx="10" cy="22" r="4" stroke="var(--accent-green)" strokeWidth="1.5" opacity="0.6" />
      <circle cx="30" cy="22" r="4" stroke="var(--accent-purple)" strokeWidth="1.5" opacity="0.6" />
      <circle cx="20" cy="34" r="4" stroke="var(--accent-amber)" strokeWidth="1.5" opacity="0.6" />
      {/* Edges */}
      <line x1="17" y1="11" x2="12" y2="19" stroke="var(--accent-blue)" strokeWidth="1" opacity="0.3" />
      <line x1="23" y1="11" x2="28" y2="19" stroke="var(--accent-blue)" strokeWidth="1" opacity="0.3" />
      <line x1="13" y1="25" x2="18" y2="31" stroke="var(--accent-green)" strokeWidth="1" opacity="0.3" />
      <line x1="27" y1="25" x2="22" y2="31" stroke="var(--accent-purple)" strokeWidth="1" opacity="0.3" />
    </svg>
  );
}

function ChangesIcon(): React.ReactElement {
  return (
    <svg width="40" height="40" viewBox="0 0 40 40" fill="none" aria-hidden="true">
      <rect x="8" y="6" width="24" height="28" rx="3" stroke="var(--accent-green)" strokeWidth="1.5" opacity="0.35" />
      {/* Plus lines (additions) */}
      <line x1="13" y1="14" x2="16" y2="14" stroke="var(--accent-green)" strokeWidth="1.5" strokeLinecap="round" opacity="0.7" />
      <line x1="18" y1="14" x2="27" y2="14" stroke="var(--accent-green)" strokeWidth="1.5" strokeLinecap="round" opacity="0.4" />
      <line x1="13" y1="20" x2="16" y2="20" stroke="var(--accent-green)" strokeWidth="1.5" strokeLinecap="round" opacity="0.7" />
      <line x1="18" y1="20" x2="24" y2="20" stroke="var(--accent-green)" strokeWidth="1.5" strokeLinecap="round" opacity="0.4" />
      {/* Minus line (deletion) */}
      <line x1="13" y1="26" x2="16" y2="26" stroke="var(--accent-red)" strokeWidth="1.5" strokeLinecap="round" opacity="0.5" />
      <line x1="18" y1="26" x2="25" y2="26" stroke="var(--accent-red)" strokeWidth="1.5" strokeLinecap="round" opacity="0.3" strokeDasharray="2 2" />
    </svg>
  );
}

/** Map preset names to their SVG icon component */
const SVG_ICONS: Record<string, () => React.ReactElement> = {
  activity: ActivityIcon,
  code: CodeIcon,
  trace: TraceIcon,
  plan: PlanIcon,
  changes: ChangesIcon,
};

// ============================================================================
// Props Interface
// ============================================================================

export interface EmptyStateProps {
  /** SVG icon key (activity|code|trace|plan|changes) or fallback emoji string */
  icon: string;
  /** Title text */
  title: string;
  /** Description text */
  description: string;
  /** Optional action button */
  action?: {
    label: string;
    onClick: () => void;
  };
}

// ============================================================================
// Generic Empty State
// ============================================================================

const EmptyState = React.memo(function EmptyState({
  icon,
  title,
  description,
  action,
}: EmptyStateProps): React.ReactElement {
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    const id = requestAnimationFrame(() => setVisible(true));
    return () => cancelAnimationFrame(id);
  }, []);

  const SvgComponent = SVG_ICONS[icon];

  return (
    <div
      className="flex flex-col items-center justify-center py-12 px-6 text-center"
      style={{
        opacity: visible ? 1 : 0,
        transform: visible ? 'translateY(0)' : 'translateY(12px)',
        transition: 'opacity 0.4s ease-out, transform 0.4s ease-out',
      }}
    >
      <div
        className="w-16 h-16 rounded-2xl flex items-center justify-center mb-4"
        style={{
          background: 'var(--bg-elevated)',
          border: '1px solid var(--border-dim)',
          boxShadow: '0 4px 16px rgba(0,0,0,0.08)',
        }}
        aria-hidden="true"
      >
        {SvgComponent ? <SvgComponent /> : (
          <span className="text-2xl">{icon}</span>
        )}
      </div>
      <h4
        className="text-sm font-bold mb-1"
        style={{
          color: 'var(--text-primary)',
          fontFamily: 'var(--font-display)',
        }}
      >
        {title}
      </h4>
      <p
        className="text-xs max-w-[260px] leading-relaxed"
        style={{ color: 'var(--text-muted)' }}
      >
        {description}
      </p>
      {action && (
        <button
          onClick={action.onClick}
          className="mt-4 px-4 py-2 text-xs font-medium rounded-xl transition-all active:scale-95 focus:outline-none focus:ring-2 focus:ring-[var(--accent-blue)]"
          style={{
            background: 'var(--bg-elevated)',
            color: 'var(--text-secondary)',
            border: '1px solid var(--border-subtle)',
          }}
          aria-label={action.label}
        >
          {action.label}
        </button>
      )}
    </div>
  );
});

// ============================================================================
// Preset Empty States
// ============================================================================

export function EmptyActivityState(): React.ReactElement {
  return (
    <EmptyState
      icon="activity"
      title="No Activity Yet"
      description="Send a message to start a task and agent activity will appear here."
    />
  );
}

export function EmptyCodeState(): React.ReactElement {
  return (
    <EmptyState
      icon="code"
      title="No Files Yet"
      description="File changes will appear here once agents start modifying the codebase."
    />
  );
}

export function EmptyTraceState(): React.ReactElement {
  return (
    <EmptyState
      icon="trace"
      title="No Traces Yet"
      description="SDK call traces will appear here when agents start processing tasks."
    />
  );
}

export function EmptyPlanState(): React.ReactElement {
  return (
    <EmptyState
      icon="plan"
      title="No Plan Yet"
      description="The task execution plan (DAG) will appear here once the PM agent creates one."
    />
  );
}

export function EmptyChangesState(): React.ReactElement {
  return (
    <EmptyState
      icon="changes"
      title="No Changes Yet"
      description="File diffs and changes will appear here as agents modify your codebase."
    />
  );
}

export default EmptyState;

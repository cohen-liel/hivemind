/**
 * EmptyState — Empty state components for ProjectView panels.
 *
 * Shows contextual messages when panels have no data to display,
 * guiding users on what to do next.
 */

import React from 'react';

// ============================================================================
// Props Interface
// ============================================================================

export interface EmptyStateProps {
  /** Icon (emoji) to display */
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
  return (
    <div className="flex flex-col items-center justify-center py-12 px-6 text-center">
      <div
        className="w-14 h-14 rounded-2xl flex items-center justify-center text-2xl mb-4"
        style={{
          background: 'var(--bg-elevated)',
          border: '1px solid var(--border-dim)',
        }}
        aria-hidden="true"
      >
        {icon}
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
        className="text-xs max-w-[240px] leading-relaxed"
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
      icon="💬"
      title="No Activity Yet"
      description="Send a message to start a task and agent activity will appear here."
    />
  );
}

export function EmptyCodeState(): React.ReactElement {
  return (
    <EmptyState
      icon="📁"
      title="No Files Yet"
      description="File changes will appear here once agents start modifying the codebase."
    />
  );
}

export function EmptyTraceState(): React.ReactElement {
  return (
    <EmptyState
      icon="🔍"
      title="No Traces Yet"
      description="SDK call traces will appear here when agents start processing tasks."
    />
  );
}

export function EmptyPlanState(): React.ReactElement {
  return (
    <EmptyState
      icon="📋"
      title="No Plan Yet"
      description="The task execution plan (DAG) will appear here once the PM agent creates one."
    />
  );
}

export function EmptyChangesState(): React.ReactElement {
  return (
    <EmptyState
      icon="🔄"
      title="No Changes Yet"
      description="File diffs and changes will appear here as agents modify your codebase."
    />
  );
}

export default EmptyState;

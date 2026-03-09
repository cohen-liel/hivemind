import React from 'react';
import { SkeletonBlock } from './Skeleton';
import type { ResumableTask } from '../reducers/projectReducer';

// ============================================================================
// Props Interfaces
// ============================================================================

export interface ResumableTaskBannerProps {
  resumableTask: ResumableTask;
  onResume: () => void;
  onDiscard: () => void;
}

export interface ProjectErrorStateProps {
  error: string;
  onRetry: () => void;
}

// ============================================================================
// ResumableTaskBanner
// ============================================================================

/** Banner shown at the top of ProjectView when an interrupted task can be resumed. */
const ResumableTaskBanner = React.memo(function ResumableTaskBanner({
  resumableTask,
  onResume,
  onDiscard,
}: ResumableTaskBannerProps): React.ReactElement {
  return (
    <div className="px-4 py-3 flex items-center justify-between gap-3 z-50" style={{
      background: 'linear-gradient(90deg, rgba(245,166,35,0.08), rgba(245,166,35,0.04))',
      borderBottom: '1px solid rgba(245,166,35,0.15)',
      animation: 'slideUp 0.3s ease-out',
    }}>
      <div className="flex items-center gap-3 flex-1 min-w-0">
        <div className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0" style={{
          background: 'rgba(245,166,35,0.12)',
        }}>
          <span className="text-sm">⚠️</span>
        </div>
        <div className="min-w-0">
          <div className="text-sm font-medium" style={{ color: 'var(--accent-amber)' }}>Interrupted Task Found</div>
          <div className="text-xs truncate" style={{ color: 'var(--text-muted)' }}>
            {resumableTask.last_message.slice(0, 100)}
            {' — '}{resumableTask.current_loop} rounds, ${resumableTask.total_cost_usd.toFixed(4)}
          </div>
        </div>
      </div>
      <div className="flex gap-2 shrink-0">
        <button
          className="px-4 py-1.5 text-xs font-medium rounded-lg transition-all active:scale-95"
          style={{
            background: 'var(--accent-amber)',
            color: '#000',
            boxShadow: '0 2px 8px rgba(245,166,35,0.3)',
          }}
          onClick={onResume}
          aria-label="Resume interrupted task"
        >
          Resume Task
        </button>
        <button
          className="px-3 py-1.5 text-xs font-medium rounded-lg transition-all active:scale-95"
          style={{
            background: 'var(--bg-elevated)',
            color: 'var(--text-muted)',
            border: '1px solid var(--border-dim)',
          }}
          onClick={onDiscard}
          aria-label="Discard interrupted task"
        >
          Discard
        </button>
      </div>
    </div>
  );
});

// ============================================================================
// ProjectErrorState
// ============================================================================

/** Full-screen error state when the project fails to load. */
const ProjectErrorState = React.memo(function ProjectErrorState({
  error,
  onRetry,
}: ProjectErrorStateProps): React.ReactElement {
  return (
    <div className="min-h-screen flex items-center justify-center" style={{ background: 'var(--bg-void)' }}>
      <div className="text-center px-4 max-w-sm mx-auto animate-[fadeSlideIn_0.3s_ease-out]">
        <div className="w-14 h-14 mx-auto mb-4 rounded-2xl flex items-center justify-center text-2xl"
          style={{ background: 'var(--glow-red)', border: '1px solid rgba(245,71,91,0.2)' }}>
          ⚠️
        </div>
        <h3 className="text-sm font-bold mb-1" style={{ color: 'var(--accent-red)' }}>Failed to load project</h3>
        <p className="text-xs mb-4" style={{ color: 'var(--text-muted)' }}>{error}</p>
        <button
          onClick={onRetry}
          className="px-4 py-2 text-xs font-medium rounded-xl transition-all active:scale-95 focus:outline-none focus:ring-2 focus:ring-[var(--accent-red)] focus:ring-offset-2 focus:ring-offset-[var(--bg-void)]"
          style={{
            background: 'var(--glow-red)',
            color: 'var(--accent-red)',
            border: '1px solid rgba(245,71,91,0.2)',
          }}
          aria-label="Retry loading project"
        >
          ↻ Retry
        </button>
      </div>
    </div>
  );
});

// ============================================================================
// ProjectLoadingSkeleton
// ============================================================================

/** Full-screen skeleton placeholder while project data is loading. */
function ProjectLoadingSkeleton(): React.ReactElement {
  return (
    <div className="min-h-screen" style={{ background: 'var(--bg-void)' }}>
      {/* Conductor bar skeleton */}
      <div className="h-14" style={{ background: 'var(--bg-panel)', borderBottom: '1px solid var(--border-dim)' }}>
        <div className="flex items-center gap-3 px-4 h-full">
          <SkeletonBlock width="32px" height="32px" className="rounded-lg" />
          <SkeletonBlock width="140px" height="16px" />
          <div className="ml-auto flex items-center gap-2">
            <SkeletonBlock width="60px" height="24px" className="rounded-full" />
            <SkeletonBlock width="60px" height="24px" className="rounded-full" />
          </div>
        </div>
      </div>
      {/* Main content skeleton */}
      <div className="flex gap-4 p-4 animate-[fadeSlideIn_0.3s_ease-out]">
        {/* Left panel — 2/3 width, two placeholder cards */}
        <div className="flex-[2] space-y-4">
          <div className="rounded-2xl p-5 space-y-3" style={{ background: 'var(--bg-card)', border: '1px solid var(--border-dim)' }}>
            <SkeletonBlock width="60%" height="14px" />
            <SkeletonBlock width="100%" height="80px" className="rounded-lg" />
            <SkeletonBlock width="40%" height="12px" />
          </div>
          <div className="rounded-2xl p-5 space-y-3" style={{ background: 'var(--bg-card)', border: '1px solid var(--border-dim)' }}>
            <SkeletonBlock width="30%" height="14px" />
            <SkeletonBlock width="100%" height="120px" className="rounded-lg" />
          </div>
        </div>
        {/* Right sidebar — 1/3 width */}
        <div className="flex-1 hidden lg:block space-y-4">
          <div className="rounded-2xl p-4 space-y-3" style={{ background: 'var(--bg-card)', border: '1px solid var(--border-dim)' }}>
            <SkeletonBlock width="50%" height="12px" />
            <div className="space-y-2">
              {[1, 2, 3].map(i => (
                <div key={i} className="flex items-center gap-2">
                  <SkeletonBlock width="28px" height="28px" className="rounded-lg" />
                  <SkeletonBlock width="80px" height="10px" />
                </div>
              ))}
            </div>
          </div>
          <div className="rounded-2xl p-4 space-y-2" style={{ background: 'var(--bg-card)', border: '1px solid var(--border-dim)' }}>
            <SkeletonBlock width="40%" height="12px" />
            <SkeletonBlock width="100%" height="60px" className="rounded-lg" />
          </div>
        </div>
      </div>
    </div>
  );
}

export { ResumableTaskBanner, ProjectErrorState, ProjectLoadingSkeleton };
export default ResumableTaskBanner;

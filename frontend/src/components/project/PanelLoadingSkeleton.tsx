/**
 * PanelLoadingSkeleton — Loading skeletons for individual ProjectView panels.
 *
 * Provides contextual loading states for each panel type instead of
 * showing a generic spinner.
 */

import React from 'react';
import { SkeletonBlock } from '../Skeleton';

// ============================================================================
// Activity Feed Skeleton
// ============================================================================

export function ActivityFeedSkeleton(): React.ReactElement {
  return (
    <div
      className="flex flex-col gap-3 p-4"
      role="status"
      aria-busy="true"
      aria-label="Loading activity feed…"
    >
      {[1, 2, 3, 4, 5].map((i) => (
        <div key={i} className="flex items-start gap-3">
          <SkeletonBlock width="28px" height="28px" className="rounded-lg flex-shrink-0" />
          <div className="flex-1 space-y-1.5">
            <SkeletonBlock width={`${60 + (i % 3) * 20}%`} height="12px" />
            <SkeletonBlock width={`${30 + (i % 2) * 25}%`} height="10px" />
          </div>
        </div>
      ))}
    </div>
  );
}

// ============================================================================
// Code Panel Skeleton
// ============================================================================

export function CodePanelSkeleton(): React.ReactElement {
  return (
    <div
      className="p-4 space-y-3"
      role="status"
      aria-busy="true"
      aria-label="Loading code panel…"
    >
      {/* File tree skeleton */}
      <div className="space-y-2">
        <SkeletonBlock width="120px" height="14px" />
        {[1, 2, 3, 4].map((i) => (
          <div key={i} className="flex items-center gap-2" style={{ paddingLeft: `${(i % 3) * 12}px` }}>
            <SkeletonBlock width="16px" height="16px" className="rounded" />
            <SkeletonBlock width={`${80 + i * 15}px`} height="12px" />
          </div>
        ))}
      </div>
      {/* Code area skeleton */}
      <div
        className="rounded-xl p-4 space-y-2"
        style={{ background: 'var(--bg-card)', border: '1px solid var(--border-dim)' }}
      >
        {[1, 2, 3, 4, 5, 6].map((i) => (
          <SkeletonBlock key={i} width={`${40 + (i * 13) % 50}%`} height="10px" />
        ))}
      </div>
    </div>
  );
}

// ============================================================================
// Agent Card Skeleton
// ============================================================================

export function AgentCardSkeleton(): React.ReactElement {
  return (
    <div
      className="rounded-xl p-4 space-y-3"
      role="status"
      aria-busy="true"
      aria-label="Loading agent card…"
      style={{ background: 'var(--bg-card)', border: '1px solid var(--border-dim)' }}
    >
      <div className="flex items-center gap-3">
        <SkeletonBlock width="36px" height="36px" className="rounded-xl" />
        <div className="flex-1 space-y-1">
          <SkeletonBlock width="100px" height="14px" />
          <SkeletonBlock width="60px" height="10px" />
        </div>
        <SkeletonBlock width="50px" height="20px" className="rounded-full" />
      </div>
      <SkeletonBlock width="80%" height="10px" />
      <div className="flex gap-4">
        <SkeletonBlock width="60px" height="10px" />
        <SkeletonBlock width="60px" height="10px" />
        <SkeletonBlock width="60px" height="10px" />
      </div>
    </div>
  );
}

// ============================================================================
// Trace Panel Skeleton
// ============================================================================

export function TracePanelSkeleton(): React.ReactElement {
  return (
    <div
      className="p-4 space-y-3"
      role="status"
      aria-busy="true"
      aria-label="Loading trace panel…"
    >
      {[1, 2, 3].map((i) => (
        <div
          key={i}
          className="rounded-lg p-3 space-y-2"
          style={{ background: 'var(--bg-card)', border: '1px solid var(--border-dim)' }}
        >
          <div className="flex items-center gap-2">
            <SkeletonBlock width="8px" height="8px" className="rounded-full" />
            <SkeletonBlock width={`${80 + i * 20}px`} height="12px" />
            <div className="ml-auto">
              <SkeletonBlock width="40px" height="10px" />
            </div>
          </div>
          <SkeletonBlock width="60%" height="10px" />
        </div>
      ))}
    </div>
  );
}

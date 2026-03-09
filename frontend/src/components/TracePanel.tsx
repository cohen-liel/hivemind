import React from 'react';
import NetworkTrace from './NetworkTrace';
import type { SdkCall } from '../reducers/projectReducer';

// ============================================================================
// Props Interface
// ============================================================================

export interface TracePanelProps {
  calls: SdkCall[];
  /** When true, wraps in padded container (desktop). Mobile renders directly. */
  variant?: 'desktop' | 'mobile';
}

// ============================================================================
// TracePanel — Network trace / SDK call timeline
// ============================================================================

/** Network trace tab content showing SDK call timeline for both mobile and desktop. */
const TracePanel = React.memo(function TracePanel({ calls, variant = 'desktop' }: TracePanelProps): React.ReactElement {
  if (variant === 'mobile') {
    return <NetworkTrace calls={calls} />;
  }

  return (
    <div className="p-6">
      <NetworkTrace calls={calls} />
    </div>
  );
});

export default TracePanel;

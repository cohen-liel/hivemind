import React from 'react';
import FileDiff from './FileDiff';
import type { FileChanges } from '../types';

// ============================================================================
// Props Interface
// ============================================================================

export interface ChangesPanelProps {
  files: FileChanges | null;
  /** When true, wraps content in a padded card (desktop). Mobile uses tighter layout. */
  variant?: 'desktop' | 'mobile';
}

// ============================================================================
// ChangesPanel — Displays file diff in a styled card container
// ============================================================================

/** File changes / diff tab content for both mobile and desktop layouts. */
const ChangesPanel = React.memo(function ChangesPanel({ files, variant = 'desktop' }: ChangesPanelProps): React.ReactElement {
  if (variant === 'mobile') {
    return (
      <div className="p-3">
        <div className="rounded-xl p-3" style={{ background: 'var(--bg-card)', border: '1px solid var(--border-dim)' }}>
          <FileDiff files={files} />
        </div>
      </div>
    );
  }

  return (
    <div className="p-6">
      <div className="rounded-xl p-4" style={{ background: 'var(--bg-card)', border: '1px solid var(--border-dim)' }}>
        <FileDiff files={files} />
      </div>
    </div>
  );
});

export default ChangesPanel;

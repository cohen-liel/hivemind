/**
 * ClearHistoryModal — Confirmation dialog for clearing project history.
 *
 * Displays a destructive action confirmation with cancel/confirm buttons.
 * Includes proper ARIA roles and keyboard accessibility.
 */

import React, { useCallback } from 'react';

// ============================================================================
// Props Interface
// ============================================================================

export interface ClearHistoryModalProps {
  /** Called when user confirms history clearing */
  onConfirm: () => void;
  /** Called when user cancels or clicks backdrop */
  onCancel: () => void;
}

// ============================================================================
// Component
// ============================================================================

const ClearHistoryModal = React.memo(function ClearHistoryModal({
  onConfirm,
  onCancel,
}: ClearHistoryModalProps): React.ReactElement {
  const handleBackdropClick = useCallback(
    (e: React.MouseEvent<HTMLDivElement>): void => {
      if (e.target === e.currentTarget) {
        onCancel();
      }
    },
    [onCancel],
  );

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLDivElement>): void => {
      if (e.key === 'Escape') {
        onCancel();
      }
    },
    [onCancel],
  );

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center animate-[fadeSlideIn_0.15s_ease-out]"
      style={{ background: 'rgba(0,0,0,0.6)', backdropFilter: 'blur(4px)' }}
      onClick={handleBackdropClick}
      onKeyDown={handleKeyDown}
      role="presentation"
    >
      <div
        className="rounded-2xl w-full max-w-sm mx-4 overflow-hidden"
        style={{
          background: 'var(--bg-card)',
          border: '1px solid var(--border-dim)',
          boxShadow: '0 25px 50px rgba(0,0,0,0.4)',
        }}
        role="dialog"
        aria-labelledby="clear-confirm-title"
        aria-modal="true"
      >
        <div
          className="h-1 w-full"
          style={{
            background: 'linear-gradient(90deg, var(--accent-red), var(--accent-amber))',
          }}
        />
        <div className="p-5">
          <div className="flex items-start gap-3 mb-4">
            <div
              className="w-10 h-10 rounded-xl flex items-center justify-center text-lg flex-shrink-0"
              style={{ background: 'var(--glow-red)' }}
              aria-hidden="true"
            >
              🗑️
            </div>
            <div>
              <h3
                id="clear-confirm-title"
                className="text-base font-bold"
                style={{
                  color: 'var(--text-primary)',
                  fontFamily: 'var(--font-display)',
                }}
              >
                Clear History?
              </h3>
              <p
                className="text-xs mt-1 leading-relaxed"
                style={{ color: 'var(--text-muted)' }}
              >
                This will permanently delete all conversation history, agent
                states, and activity logs for this project. The agent will start
                fresh with no memory.
              </p>
            </div>
          </div>
          <div className="flex justify-end gap-2">
            <button
              onClick={onCancel}
              className="px-4 py-2 text-sm font-medium rounded-xl transition-all focus:outline-none focus:ring-2 focus:ring-[var(--border-dim)]"
              style={{
                color: 'var(--text-secondary)',
                border: '1px solid var(--border-dim)',
              }}
              aria-label="Cancel clearing history"
            >
              Cancel
            </button>
            <button
              onClick={onConfirm}
              className="px-4 py-2 text-sm font-semibold rounded-xl transition-all text-white active:scale-[0.97] focus:outline-none focus:ring-2 focus:ring-[var(--accent-red)]"
              style={{
                background: 'var(--accent-red)',
                boxShadow: '0 2px 10px var(--glow-red)',
              }}
              aria-label="Confirm clearing all history"
            >
              Clear All History
            </button>
          </div>
        </div>
      </div>
    </div>
  );
});

export default ClearHistoryModal;

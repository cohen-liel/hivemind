/**
 * PanelErrorBoundary — Granular error boundary for individual panels.
 *
 * Unlike the app-level ErrorBoundary, this renders a compact, inline
 * error card that allows retry without losing the entire page context.
 */

import React from 'react';

// ============================================================================
// Props Interface
// ============================================================================

export interface PanelErrorBoundaryProps {
  /** Panel name shown in the error message */
  panelName: string;
  /** Content to render when no error */
  children: React.ReactNode;
}

interface PanelErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
}

// ============================================================================
// Component
// ============================================================================

export class PanelErrorBoundary extends React.Component<
  PanelErrorBoundaryProps,
  PanelErrorBoundaryState
> {
  constructor(props: PanelErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): PanelErrorBoundaryState {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo): void {
    console.error(
      `[PanelErrorBoundary:${this.props.panelName}] Caught rendering error:`,
      error,
      info.componentStack,
    );
  }

  handleRetry = (): void => {
    this.setState({ hasError: false, error: null });
  };

  render(): React.ReactNode {
    if (this.state.hasError) {
      return (
        <div
          className="flex flex-col items-center justify-center p-6 min-h-[200px]"
          role="alert"
          aria-live="assertive"
        >
          <div
            className="max-w-xs w-full rounded-xl p-5 text-center"
            style={{
              background: 'var(--bg-card)',
              border: '1px solid var(--border-dim)',
              boxShadow: '0 0 30px rgba(245, 71, 91, 0.06)',
            }}
          >
            <div
              className="w-10 h-10 rounded-lg flex items-center justify-center text-lg mx-auto mb-3"
              style={{ background: 'var(--glow-red)' }}
              aria-hidden="true"
            >
              ⚠️
            </div>
            <h4
              className="text-sm font-bold mb-1"
              style={{
                color: 'var(--text-primary)',
                fontFamily: 'var(--font-display)',
              }}
            >
              {this.props.panelName} Error
            </h4>
            <p
              className="text-xs mb-3 leading-relaxed"
              style={{ color: 'var(--text-muted)' }}
            >
              Something went wrong rendering this panel.
            </p>

            {this.state.error && (
              <details className="text-left mb-3">
                <summary
                  className="text-[11px] cursor-pointer select-none"
                  style={{ color: 'var(--text-muted)' }}
                >
                  Details
                </summary>
                <pre
                  className="text-[10px] rounded-lg p-2 overflow-auto max-h-20 mt-1"
                  style={{
                    background: 'rgba(0,0,0,0.3)',
                    border: '1px solid var(--border-dim)',
                    color: 'var(--accent-red)',
                    fontFamily: 'var(--font-mono)',
                  }}
                >
                  {this.state.error.message}
                </pre>
              </details>
            )}

            <button
              onClick={this.handleRetry}
              className="px-4 py-1.5 text-xs font-medium rounded-lg transition-all active:scale-95 focus:outline-none focus:ring-2 focus:ring-[var(--accent-blue)]"
              style={{
                background: 'var(--bg-elevated)',
                color: 'var(--text-secondary)',
                border: '1px solid var(--border-subtle)',
              }}
              aria-label={`Retry loading ${this.props.panelName}`}
            >
              ↻ Retry
            </button>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}

export default PanelErrorBoundary;

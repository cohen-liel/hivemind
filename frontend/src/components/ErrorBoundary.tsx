import React from 'react';

interface ErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
}

interface ErrorBoundaryProps {
  children: React.ReactNode;
}

/**
 * Catches React rendering errors and displays a beautiful fallback UI
 * instead of crashing the entire app.
 */
export class ErrorBoundary extends React.Component<ErrorBoundaryProps, ErrorBoundaryState> {
  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error('[ErrorBoundary] Caught rendering error:', error, info.componentStack);
  }

  handleRetry = () => {
    this.setState({ hasError: false, error: null });
  };

  render() {
    if (this.state.hasError) {
      return (
        <div
          className="min-h-screen flex items-center justify-center p-6"
          style={{ background: 'var(--bg-void)' }}
        >
          <div
            className="max-w-md w-full rounded-2xl p-8 text-center"
            style={{
              background: 'var(--bg-card)',
              border: '1px solid var(--border-dim)',
              boxShadow: '0 0 60px rgba(245, 71, 91, 0.08), 0 25px 50px rgba(0,0,0,0.4)',
            }}
          >
            {/* Error icon with glow */}
            <div
              className="w-16 h-16 rounded-2xl flex items-center justify-center text-3xl mx-auto mb-5"
              style={{
                background: 'var(--glow-red)',
                boxShadow: '0 0 30px var(--glow-red)',
              }}
            >
              💥
            </div>

            <h1
              className="text-xl font-bold mb-2"
              style={{ color: 'var(--text-primary)', fontFamily: 'var(--font-display)' }}
            >
              Something went wrong
            </h1>

            <p className="text-sm mb-6 leading-relaxed" style={{ color: 'var(--text-muted)' }}>
              An unexpected error occurred while rendering the interface.
              This is usually temporary — try refreshing.
            </p>

            {/* Error detail (collapsed) */}
            {this.state.error && (
              <details className="text-left mb-6">
                <summary
                  className="text-xs cursor-pointer select-none mb-2"
                  style={{ color: 'var(--text-muted)' }}
                >
                  Technical details
                </summary>
                <pre
                  className="text-[11px] rounded-lg p-3 overflow-auto max-h-32"
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

            {/* Actions */}
            <div className="flex items-center justify-center gap-3">
              <button
                onClick={this.handleRetry}
                className="px-5 py-2.5 text-sm font-semibold rounded-xl transition-all duration-200 active:scale-[0.97]"
                style={{
                  background: 'linear-gradient(135deg, var(--accent-blue), #4f6ef5)',
                  color: 'white',
                  boxShadow: '0 3px 12px rgba(99,140,255,0.3)',
                }}
              >
                ↻ Try Again
              </button>
              <button
                onClick={() => window.location.reload()}
                className="px-5 py-2.5 text-sm font-medium rounded-xl transition-all duration-200 active:scale-[0.97]"
                style={{
                  background: 'var(--bg-elevated)',
                  color: 'var(--text-secondary)',
                  border: '1px solid var(--border-subtle)',
                }}
              >
                Reload Page
              </button>
            </div>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}

export default ErrorBoundary;

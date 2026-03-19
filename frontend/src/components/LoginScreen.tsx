import { useState, useRef, useEffect, useCallback } from 'react';
import { QRCodeSVG } from 'qrcode.react';
import { setAuthToken } from '../WebSocketContext';

const CODE_LENGTH = 8;
const ONBOARDING_KEY = 'hivemind-onboarding-seen';
const API_KEY_STORAGE_KEY = 'hivemind-api-key';

interface LoginScreenProps {
  onAuthenticated: () => void;
}

type ConnectionPhase = 'idle' | 'verifying' | 'connecting' | 'success';
type LoginMode = 'code' | 'apikey';

/** Tooltip wrapper for login screen elements */
function Tooltip({ text, children }: { text: string; children: React.ReactNode }): React.ReactElement {
  return (
    <div className="login-tooltip-wrapper" style={{ position: 'relative', display: 'inline-flex' }}>
      {children}
      <span className="login-tooltip" role="tooltip">{text}</span>
    </div>
  );
}

export default function LoginScreen({ onAuthenticated }: LoginScreenProps): React.ReactElement {
  const [chars, setChars] = useState<string[]>(Array(CODE_LENGTH).fill(''));
  const [password, setPassword] = useState('');
  const [passwordRequired, setPasswordRequired] = useState(false);
  const [error, setError] = useState('');
  const [phase, setPhase] = useState<ConnectionPhase>('idle');
  const [shake, setShake] = useState(false);
  const [loginMode, setLoginMode] = useState<LoginMode>('code');
  const [apiKey, setApiKey] = useState('');
  const [showOnboarding, setShowOnboarding] = useState(false);
  const inputRefs = useRef<(HTMLInputElement | null)[]>([]);
  const passwordRef = useRef<HTMLInputElement | null>(null);
  const apiKeyRef = useRef<HTMLInputElement | null>(null);

  const loading = phase === 'verifying' || phase === 'connecting';
  const dashboardUrl = typeof window !== 'undefined' ? window.location.origin : '';

  // Check onboarding status
  useEffect(() => {
    const seen = localStorage.getItem(ONBOARDING_KEY);
    if (!seen) {
      setShowOnboarding(true);
    }
  }, []);

  // Load persisted API key from localStorage
  useEffect(() => {
    const stored = localStorage.getItem(API_KEY_STORAGE_KEY);
    if (stored) {
      setApiKey(stored);
    }
  }, []);

  const dismissOnboarding = useCallback((): void => {
    setShowOnboarding(false);
    localStorage.setItem(ONBOARDING_KEY, 'true');
  }, []);

  // Check if password is required
  useEffect(() => {
    fetch('/api/auth/status')
      .then(res => res.json())
      .then(data => {
        if (data.password_required) setPasswordRequired(true);
      })
      .catch(() => {});
  }, []);

  // Focus first input on mount (delayed for mobile keyboards)
  useEffect(() => {
    const timer = setTimeout(() => {
      if (loginMode === 'code') {
        inputRefs.current[0]?.focus();
      } else {
        apiKeyRef.current?.focus();
      }
    }, 100);
    return () => clearTimeout(timer);
  }, [loginMode]);

  const triggerError = useCallback((msg: string): void => {
    setError(msg);
    setShake(true);
    setTimeout(() => setShake(false), 600);
  }, []);

  const submitCode = useCallback(async (code: string): Promise<void> => {
    if (passwordRequired && !password) {
      passwordRef.current?.focus();
      triggerError('Password is required.');
      return;
    }
    setPhase('verifying');
    setError('');
    try {
      const res = await fetch('/api/auth/verify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code, password }),
      });
      if (res.ok) {
        const data = await res.json().catch(() => ({}));
        if (data.device_token) {
          setAuthToken(data.device_token);
        }
        setPhase('success');
        setTimeout(() => onAuthenticated(), 600);
      } else {
        const data = await res.json().catch(() => ({}));
        setPhase('idle');
        triggerError(data.detail || data.error || 'Invalid code. Please try again.');
        setChars(Array(CODE_LENGTH).fill(''));
        inputRefs.current[0]?.focus();
      }
    } catch {
      setPhase('idle');
      triggerError('Unable to reach the server. Please check your connection.');
    }
  }, [onAuthenticated, password, passwordRequired, triggerError]);

  const submitApiKey = useCallback(async (): Promise<void> => {
    if (!apiKey.trim()) {
      triggerError('Please enter your API key.');
      return;
    }
    setPhase('verifying');
    setError('');
    // Persist API key securely in localStorage (never in URL/logs)
    localStorage.setItem(API_KEY_STORAGE_KEY, apiKey.trim());
    try {
      const res = await fetch('/api/auth/verify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ api_key: apiKey.trim() }),
      });
      if (res.ok) {
        const data = await res.json().catch(() => ({}));
        if (data.device_token) {
          setAuthToken(data.device_token);
        }
        setPhase('success');
        setTimeout(() => onAuthenticated(), 600);
      } else {
        const data = await res.json().catch(() => ({}));
        setPhase('idle');
        triggerError(data.detail || data.error || 'Invalid API key.');
      }
    } catch {
      setPhase('idle');
      triggerError('Unable to reach the server. Please check your connection.');
    }
  }, [apiKey, onAuthenticated, triggerError]);

  const handleInput = useCallback((index: number, value: string): void => {
    const char = value.replace(/[^a-zA-Z0-9]/g, '').slice(-1).toUpperCase();
    const newChars = [...chars];
    newChars[index] = char;
    setChars(newChars);
    setError('');

    if (char && index < CODE_LENGTH - 1) {
      inputRefs.current[index + 1]?.focus();
    }

    if (char && index === CODE_LENGTH - 1) {
      const code = newChars.join('');
      if (code.length === CODE_LENGTH) {
        if (passwordRequired) {
          passwordRef.current?.focus();
        } else {
          submitCode(code);
        }
      }
    }
  }, [chars, submitCode, passwordRequired]);

  const handleKeyDown = useCallback((index: number, e: React.KeyboardEvent): void => {
    if (e.key === 'Backspace' && !chars[index] && index > 0) {
      inputRefs.current[index - 1]?.focus();
    }
    if (e.key === 'Enter') {
      const code = chars.join('');
      if (code.length === CODE_LENGTH) {
        submitCode(code);
      }
    }
  }, [chars, submitCode]);

  const handlePaste = useCallback((e: React.ClipboardEvent): void => {
    e.preventDefault();
    const pasted = e.clipboardData.getData('text').replace(/[^a-zA-Z0-9]/g, '').toUpperCase().slice(0, CODE_LENGTH);
    if (pasted.length > 0) {
      const newChars = [...chars];
      for (let i = 0; i < pasted.length && i < CODE_LENGTH; i++) {
        newChars[i] = pasted[i];
      }
      setChars(newChars);
      if (pasted.length === CODE_LENGTH) {
        if (passwordRequired) {
          passwordRef.current?.focus();
        } else {
          submitCode(pasted);
        }
      } else {
        inputRefs.current[Math.min(pasted.length, CODE_LENGTH - 1)]?.focus();
      }
    }
  }, [chars, submitCode, passwordRequired]);

  const handlePasswordKeyDown = useCallback((e: React.KeyboardEvent): void => {
    if (e.key === 'Enter') {
      const code = chars.join('');
      if (code.length === CODE_LENGTH) {
        submitCode(code);
      }
    }
  }, [chars, submitCode]);

  const handleApiKeyKeyDown = useCallback((e: React.KeyboardEvent): void => {
    if (e.key === 'Enter') {
      submitApiKey();
    }
  }, [submitApiKey]);

  // Phase-specific status message
  const statusMessage = phase === 'verifying'
    ? 'Verifying access code...'
    : phase === 'connecting'
    ? 'Setting up your session...'
    : phase === 'success'
    ? 'Connected!'
    : null;

  return (
    <div className="login-screen" style={{
      minHeight: '100dvh',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      background: 'var(--bg-void, #0a0b0f)',
      padding: 'var(--space-md) var(--space-md)',
      paddingTop: 'max(var(--space-md), env(safe-area-inset-top, 0px))',
      paddingBottom: 'max(var(--space-md), env(safe-area-inset-bottom, 0px))',
      paddingLeft: 'max(var(--space-md), env(safe-area-inset-left, 0px))',
      paddingRight: 'max(var(--space-md), env(safe-area-inset-right, 0px))',
      overscrollBehavior: 'none',
    }}>
      <div className="login-container" style={{
        width: '100%',
        maxWidth: '440px',
        textAlign: 'center',
      }}>
        {/* Onboarding hint for first-time users */}
        {showOnboarding && phase !== 'success' && (
          <div
            className="login-onboarding"
            role="status"
            aria-live="polite"
            style={{
              padding: '14px 18px',
              borderRadius: 'var(--radius-md)',
              background: 'rgba(99, 140, 255, 0.08)',
              border: '1px solid rgba(99, 140, 255, 0.15)',
              marginBottom: 'var(--space-lg)',
              animation: 'fadeSlideIn 0.5s ease-out',
              textAlign: 'left',
              position: 'relative',
            }}
          >
            <button
              onClick={dismissOnboarding}
              aria-label="Dismiss onboarding hint"
              style={{
                position: 'absolute',
                top: '8px',
                right: '8px',
                background: 'none',
                border: 'none',
                color: 'var(--text-muted)',
                cursor: 'pointer',
                padding: '4px',
                lineHeight: 1,
                fontSize: '16px',
              }}
            >
              <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
                <path d="M4 4l8 8M12 4l-8 8"/>
              </svg>
            </button>
            <div style={{ display: 'flex', alignItems: 'flex-start', gap: '10px' }}>
              <span style={{ fontSize: '20px', lineHeight: 1.2, flexShrink: 0 }} aria-hidden="true">👋</span>
              <div>
                <p style={{
                  fontSize: '14px',
                  fontWeight: 600,
                  color: 'var(--text-primary)',
                  margin: '0 0 4px',
                  paddingRight: '20px',
                }}>
                  Welcome to Hivemind!
                </p>
                <p style={{
                  fontSize: '13px',
                  color: 'var(--text-secondary)',
                  margin: 0,
                  lineHeight: 1.5,
                }}>
                  Enter the 8-character access code from your terminal to connect. Your device will be remembered for future visits.
                </p>
              </div>
            </div>
          </div>
        )}

        {/* Logo */}
        <div style={{
          width: '72px',
          height: '72px',
          margin: '0 auto var(--space-lg)',
          borderRadius: '18px',
          background: phase === 'success'
            ? 'linear-gradient(135deg, var(--accent-green) 0%, #16a34a 100%)'
            : 'linear-gradient(135deg, var(--accent-blue) 0%, var(--accent-purple) 100%)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          boxShadow: phase === 'success'
            ? '0 8px 32px var(--glow-green)'
            : '0 8px 32px var(--glow-blue)',
          transition: 'background 0.4s ease, box-shadow 0.4s ease, transform 0.4s ease',
          transform: phase === 'success' ? 'scale(1.05)' : 'scale(1)',
        }}>
          {phase === 'success' ? (
            <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M20 6L9 17l-5-5" className="login-checkmark-draw"/>
            </svg>
          ) : (
            <img src="/favicon-32x32.png" alt="Hivemind" width="48" height="48" style={{ borderRadius: '10px' }} />
          )}
        </div>

        {/* Title */}
        <h1 style={{
          fontSize: 'clamp(24px, 5vw, 32px)',
          fontWeight: 700,
          color: 'var(--text-primary)',
          margin: '0 0 var(--space-sm)',
          letterSpacing: '-0.02em',
          fontFamily: 'var(--font-display)',
        }}>
          {phase === 'success' ? 'Welcome!' : 'Hivemind'}
        </h1>
        <p style={{
          fontSize: 'clamp(13px, 3.5vw, 15px)',
          color: 'var(--text-muted)',
          margin: '0 0 var(--space-xl)',
          lineHeight: 1.5,
          fontFamily: 'var(--font-display)',
        }}>
          {phase === 'success'
            ? 'Opening your dashboard...'
            : 'Connect to your AI engineering team'}
        </p>

        {/* Main content area */}
        {phase !== 'success' && (
          <>
            {/* Login mode toggle */}
            <div
              role="tablist"
              aria-label="Login method"
              style={{
                display: 'flex',
                gap: '2px',
                marginBottom: 'var(--space-lg)',
                padding: '3px',
                borderRadius: 'var(--radius-md)',
                background: 'var(--bg-panel)',
                border: '1px solid var(--border-dim)',
                maxWidth: '320px',
                margin: '0 auto var(--space-lg)',
              }}
            >
              <button
                role="tab"
                aria-selected={loginMode === 'code'}
                aria-controls="login-panel-code"
                onClick={() => setLoginMode('code')}
                style={{
                  flex: 1,
                  padding: '8px 12px',
                  borderRadius: 'calc(var(--radius-md) - 3px)',
                  border: 'none',
                  background: loginMode === 'code' ? 'var(--bg-elevated)' : 'transparent',
                  color: loginMode === 'code' ? 'var(--text-primary)' : 'var(--text-muted)',
                  fontSize: '13px',
                  fontWeight: 600,
                  cursor: 'pointer',
                  transition: 'all 0.2s ease',
                  fontFamily: 'var(--font-display)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  gap: '6px',
                }}
              >
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" aria-hidden="true">
                  <rect x="2" y="4" width="12" height="8" rx="1.5"/>
                  <path d="M5 7h1M7 7h1M9 7h1M5 9h6"/>
                </svg>
                Access Code
              </button>
              <button
                role="tab"
                aria-selected={loginMode === 'apikey'}
                aria-controls="login-panel-apikey"
                onClick={() => setLoginMode('apikey')}
                style={{
                  flex: 1,
                  padding: '8px 12px',
                  borderRadius: 'calc(var(--radius-md) - 3px)',
                  border: 'none',
                  background: loginMode === 'apikey' ? 'var(--bg-elevated)' : 'transparent',
                  color: loginMode === 'apikey' ? 'var(--text-primary)' : 'var(--text-muted)',
                  fontSize: '13px',
                  fontWeight: 600,
                  cursor: 'pointer',
                  transition: 'all 0.2s ease',
                  fontFamily: 'var(--font-display)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  gap: '6px',
                }}
              >
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" aria-hidden="true">
                  <path d="M10 2a3.5 3.5 0 00-3.24 4.8L2 11.6V14h2.4v-1.6H6v-1.6h1.6l1.2-1.2A3.5 3.5 0 1010 2z"/>
                  <circle cx="11" cy="5" r="1"/>
                </svg>
                API Key
              </button>
            </div>

            {/* Access Code Panel */}
            {loginMode === 'code' && (
              <div id="login-panel-code" role="tabpanel" aria-labelledby="tab-code" style={{ animation: 'fadeSlideIn 0.25s ease-out' }}>
                {/* Code Input — 8 chars */}
                <div
                  role="group"
                  aria-label="Access code input"
                  style={{
                    display: 'flex',
                    gap: 'clamp(4px, 1.2vw, 8px)',
                    justifyContent: 'center',
                    marginBottom: passwordRequired ? 'var(--space-md)' : 'var(--space-lg)',
                    animation: shake ? 'shake 0.5s ease-in-out' : 'none',
                    flexWrap: 'nowrap',
                  }}
                  onPaste={handlePaste}
                >
                  {chars.map((char, i) => (
                    <input
                      key={i}
                      ref={el => { inputRefs.current[i] = el; }}
                      type="text"
                      inputMode="text"
                      maxLength={1}
                      value={char}
                      onChange={e => handleInput(i, e.target.value)}
                      onKeyDown={e => handleKeyDown(i, e)}
                      disabled={loading}
                      autoComplete="one-time-code"
                      aria-label={`Code character ${i + 1} of ${CODE_LENGTH}`}
                      className="login-code-input"
                      style={{
                        width: 'clamp(36px, calc((100vw - 120px) / 8), 48px)',
                        height: 'clamp(44px, 12vw, 56px)',
                        borderRadius: 'var(--radius-sm)',
                        border: `2px solid ${error ? 'var(--accent-red)' : char ? 'var(--accent-blue)' : 'var(--border-dim)'}`,
                        background: 'var(--bg-panel)',
                        color: 'var(--text-primary)',
                        fontSize: 'clamp(16px, 4.5vw, 20px)',
                        fontWeight: 700,
                        textAlign: 'center',
                        outline: 'none',
                        transition: 'border-color 0.2s, box-shadow 0.2s, transform 0.15s',
                        caretColor: 'var(--accent-blue)',
                        boxShadow: char ? '0 0 0 3px var(--glow-blue)' : 'none',
                        textTransform: 'uppercase' as const,
                        fontFamily: 'var(--font-mono)',
                        WebkitTextSizeAdjust: '100%',
                        transform: char ? 'scale(1.02)' : 'scale(1)',
                      }}
                      onFocus={e => {
                        e.target.style.borderColor = 'var(--accent-blue)';
                        e.target.style.boxShadow = '0 0 0 3px var(--glow-blue)';
                      }}
                      onBlur={e => {
                        if (!char) {
                          e.target.style.borderColor = error ? 'var(--accent-red)' : 'var(--border-dim)';
                          e.target.style.boxShadow = 'none';
                        }
                      }}
                    />
                  ))}
                </div>

                {/* Password field (only if required) */}
                {passwordRequired && (
                  <div style={{ marginBottom: 'var(--space-lg)' }}>
                    <input
                      ref={passwordRef}
                      type="password"
                      placeholder="Password"
                      value={password}
                      onChange={e => setPassword(e.target.value)}
                      onKeyDown={handlePasswordKeyDown}
                      disabled={loading}
                      aria-label="Password"
                      className="login-text-input"
                      style={{
                        width: '100%',
                        maxWidth: '320px',
                        height: '48px',
                        borderRadius: 'var(--radius-md)',
                        border: `2px solid ${error && !password ? 'var(--accent-red)' : 'var(--border-dim)'}`,
                        background: 'var(--bg-panel)',
                        color: 'var(--text-primary)',
                        fontSize: '16px',
                        padding: '0 var(--space-md)',
                        outline: 'none',
                        transition: 'border-color 0.2s',
                        fontFamily: 'var(--font-display)',
                      }}
                      onFocus={e => { e.target.style.borderColor = 'var(--accent-blue)'; }}
                      onBlur={e => { e.target.style.borderColor = 'var(--border-dim)'; }}
                    />
                  </div>
                )}

                {/* Submit button */}
                {(passwordRequired || chars.every(c => c)) && (
                  <button
                    onClick={() => {
                      const code = chars.join('');
                      if (code.length === CODE_LENGTH) submitCode(code);
                    }}
                    disabled={loading || chars.some(c => !c)}
                    aria-label="Connect to Hivemind"
                    className="login-submit-btn"
                    style={{
                      width: '100%',
                      maxWidth: '320px',
                      height: '48px',
                      borderRadius: 'var(--radius-md)',
                      border: 'none',
                      background: 'linear-gradient(135deg, var(--accent-blue), var(--accent-purple))',
                      color: 'white',
                      fontSize: '15px',
                      fontWeight: 600,
                      cursor: loading || chars.some(c => !c) ? 'not-allowed' : 'pointer',
                      marginBottom: 'var(--space-lg)',
                      opacity: loading || chars.some(c => !c) ? 0.5 : 1,
                      transition: 'opacity 0.2s, transform 0.15s, box-shadow 0.2s',
                      fontFamily: 'var(--font-display)',
                      boxShadow: '0 4px 16px var(--glow-blue)',
                    }}
                  >
                    {loading ? 'Connecting...' : 'Connect'}
                  </button>
                )}
              </div>
            )}

            {/* API Key Panel */}
            {loginMode === 'apikey' && (
              <div id="login-panel-apikey" role="tabpanel" aria-labelledby="tab-apikey" style={{ animation: 'fadeSlideIn 0.25s ease-out' }}>
                <div style={{ marginBottom: 'var(--space-lg)', maxWidth: '320px', margin: '0 auto var(--space-lg)' }}>
                  <div style={{ position: 'relative' }}>
                    <input
                      ref={apiKeyRef}
                      type="password"
                      placeholder="Paste your API key"
                      value={apiKey}
                      onChange={e => setApiKey(e.target.value)}
                      onKeyDown={handleApiKeyKeyDown}
                      disabled={loading}
                      aria-label="API key"
                      autoComplete="off"
                      className="login-text-input"
                      style={{
                        width: '100%',
                        height: '48px',
                        borderRadius: 'var(--radius-md)',
                        border: `2px solid ${error ? 'var(--accent-red)' : 'var(--border-dim)'}`,
                        background: 'var(--bg-panel)',
                        color: 'var(--text-primary)',
                        fontSize: '16px',
                        padding: '0 var(--space-md)',
                        paddingRight: '44px',
                        outline: 'none',
                        transition: 'border-color 0.2s',
                        fontFamily: 'var(--font-mono)',
                        animation: shake ? 'shake 0.5s ease-in-out' : 'none',
                      }}
                      onFocus={e => { e.target.style.borderColor = 'var(--accent-blue)'; }}
                      onBlur={e => { e.target.style.borderColor = 'var(--border-dim)'; }}
                    />
                    {/* Lock icon */}
                    <Tooltip text="Stored locally, never sent to logs">
                      <div style={{
                        position: 'absolute',
                        right: '12px',
                        top: '50%',
                        transform: 'translateY(-50%)',
                        color: 'var(--text-muted)',
                        pointerEvents: 'auto',
                      }}>
                        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" aria-hidden="true">
                          <rect x="3" y="7" width="10" height="7" rx="1.5"/>
                          <path d="M5 7V5a3 3 0 016 0v2"/>
                        </svg>
                      </div>
                    </Tooltip>
                  </div>
                  <p style={{
                    fontSize: '11px',
                    color: 'var(--text-muted)',
                    margin: 'var(--space-sm) 0 0',
                    textAlign: 'left',
                    opacity: 0.8,
                  }}>
                    Your API key is stored in your browser only, never sent to logs or URLs.
                  </p>
                </div>

                <button
                  onClick={submitApiKey}
                  disabled={loading || !apiKey.trim()}
                  aria-label="Connect with API key"
                  className="login-submit-btn"
                  style={{
                    width: '100%',
                    maxWidth: '320px',
                    height: '48px',
                    borderRadius: 'var(--radius-md)',
                    border: 'none',
                    background: 'linear-gradient(135deg, var(--accent-blue), var(--accent-purple))',
                    color: 'white',
                    fontSize: '15px',
                    fontWeight: 600,
                    cursor: loading || !apiKey.trim() ? 'not-allowed' : 'pointer',
                    marginBottom: 'var(--space-lg)',
                    opacity: loading || !apiKey.trim() ? 0.5 : 1,
                    transition: 'opacity 0.2s, transform 0.15s, box-shadow 0.2s',
                    fontFamily: 'var(--font-display)',
                    boxShadow: '0 4px 16px var(--glow-blue)',
                  }}
                >
                  {loading ? 'Connecting...' : 'Connect'}
                </button>
              </div>
            )}
          </>
        )}

        {/* Status message */}
        {statusMessage && (
          <div style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: '10px',
            color: phase === 'success' ? 'var(--accent-green)' : 'var(--text-muted)',
            fontSize: '14px',
            marginBottom: 'var(--space-md)',
            animation: 'fadeSlideIn 0.3s ease',
          }} role="status" aria-live="polite">
            {phase !== 'success' && (
              <div style={{
                width: '16px',
                height: '16px',
                border: '2px solid var(--border-dim)',
                borderTopColor: 'var(--accent-blue)',
                borderRadius: '50%',
                animation: 'spin 0.8s linear infinite',
                flexShrink: 0,
              }} />
            )}
            {phase === 'success' && (
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={{ flexShrink: 0 }}>
                <path d="M20 6L9 17l-5-5"/>
              </svg>
            )}
            <span>{statusMessage}</span>
          </div>
        )}

        {/* Error message */}
        {error && (
          <div
            role="alert"
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: '8px',
              fontSize: '14px',
              color: 'var(--accent-red)',
              margin: '0 0 var(--space-md)',
              animation: 'fadeSlideIn 0.3s ease',
            }}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true" style={{ flexShrink: 0 }}>
              <circle cx="12" cy="12" r="10"/>
              <path d="M12 8v4M12 16h.01"/>
            </svg>
            <span>{error}</span>
          </div>
        )}

        {/* QR Code + Help section */}
        {phase !== 'success' && (
          <div style={{
            display: 'flex',
            gap: 'var(--space-md)',
            alignItems: 'stretch',
          }} className="login-bottom-section">
            {/* QR Code card — for mobile scanning */}
            <div className="login-qr-card" style={{
              padding: 'var(--space-md)',
              borderRadius: 'var(--radius-md)',
              background: 'var(--bg-panel)',
              border: '1px solid var(--border-dim)',
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              justifyContent: 'center',
              gap: 'var(--space-sm)',
              flexShrink: 0,
            }}>
              <div style={{
                padding: '8px',
                borderRadius: 'var(--radius-sm)',
                background: 'white',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
              }}>
                <QRCodeSVG
                  value={dashboardUrl}
                  size={80}
                  level="M"
                  bgColor="white"
                  fgColor="#0a0b0f"
                  aria-label={`QR code to open ${dashboardUrl} on mobile`}
                />
              </div>
              <span style={{
                fontSize: '10px',
                color: 'var(--text-muted)',
                textAlign: 'center',
                lineHeight: 1.3,
                fontWeight: 500,
              }}>
                Scan to open<br/>on your phone
              </span>
            </div>

            {/* Help section */}
            <div className="login-help-card" style={{
              padding: 'var(--space-md) var(--space-md)',
              borderRadius: 'var(--radius-md)',
              background: 'var(--bg-panel)',
              border: '1px solid var(--border-dim)',
              textAlign: 'left',
              flex: 1,
              minWidth: 0,
            }}>
              <p style={{
                fontSize: '13px',
                color: 'var(--text-secondary)',
                margin: '0 0 8px',
                lineHeight: 1.6,
                fontWeight: 600,
              }}>
                How to connect:
              </p>
              <ol style={{
                fontSize: '12px',
                color: 'var(--text-muted)',
                margin: '0 0 10px',
                paddingLeft: '18px',
                lineHeight: 1.8,
              }}>
                <li>Open your terminal where Hivemind is running</li>
                <li>Find the access code displayed there:</li>
              </ol>
              <code style={{
                display: 'block',
                padding: '8px 12px',
                borderRadius: 'var(--radius-sm)',
                background: 'var(--bg-void)',
                color: 'var(--accent-green)',
                fontSize: '12px',
                fontFamily: 'var(--font-mono)',
                letterSpacing: '0.08em',
              }}>
                ACCESS CODE: ????????
              </code>
              <p style={{
                fontSize: '11px',
                color: 'var(--text-muted)',
                margin: '8px 0 0',
                opacity: 0.7,
                lineHeight: 1.4,
              }}>
                This device will be remembered for future visits.
              </p>
            </div>
          </div>
        )}

        {/* CSS Animations */}
        <style>{`
          @keyframes shake {
            0%, 100% { transform: translateX(0); }
            10%, 30%, 50%, 70%, 90% { transform: translateX(-4px); }
            20%, 40%, 60%, 80% { transform: translateX(4px); }
          }
          @keyframes spin {
            to { transform: rotate(360deg); }
          }
          @keyframes fadeSlideIn {
            from { opacity: 0; transform: translateY(-6px); }
            to { opacity: 1; transform: translateY(0); }
          }
          .login-checkmark-draw {
            stroke-dasharray: 30;
            stroke-dashoffset: 30;
            animation: drawCheck 0.5s ease-out 0.1s forwards;
          }
          @keyframes drawCheck {
            to { stroke-dashoffset: 0; }
          }
          .login-submit-btn:not(:disabled):hover {
            transform: translateY(-1px);
            box-shadow: 0 6px 24px var(--glow-blue) !important;
          }
          .login-submit-btn:not(:disabled):active {
            transform: translateY(0) scale(0.98);
          }
          /* Tooltip styles */
          .login-tooltip-wrapper:hover .login-tooltip,
          .login-tooltip-wrapper:focus-within .login-tooltip {
            opacity: 1;
            transform: translateX(0) translateY(-100%);
            pointer-events: auto;
          }
          .login-tooltip {
            position: absolute;
            bottom: calc(100% + 6px);
            left: 50%;
            transform: translateX(-50%) translateY(-100%);
            white-space: nowrap;
            padding: 6px 10px;
            border-radius: 6px;
            background: var(--bg-elevated, #191c27);
            color: var(--text-secondary, #8b90a5);
            font-size: 11px;
            line-height: 1.3;
            pointer-events: none;
            opacity: 0;
            transition: opacity 0.15s ease, transform 0.15s ease;
            z-index: 100;
            border: 1px solid var(--border-subtle);
            box-shadow: 0 4px 12px rgba(0,0,0,0.3);
          }
          /* Bottom section responsive — stack vertically on narrow */
          .login-bottom-section {
            flex-direction: row;
          }
          @media (max-width: 420px) {
            .login-bottom-section {
              flex-direction: column;
            }
            .login-qr-card {
              flex-direction: row !important;
              gap: var(--space-md) !important;
            }
          }
          /* 4K: scale up login container */
          @media (min-width: 2560px) {
            .login-container {
              max-width: 520px !important;
            }
          }
        `}</style>
      </div>
    </div>
  );
}

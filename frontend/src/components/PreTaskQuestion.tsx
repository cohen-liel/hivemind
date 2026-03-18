/**
 * PreTaskQuestion — Inline question bubble shown when the orchestrator
 * needs clarification before dispatching agents.
 *
 * Renders above the Controls input, styled as a blue callout.
 * The user types an answer and hits Send (or Enter) to reply.
 */

import React, { useState, useRef, useEffect } from 'react';

export interface PreTaskQuestionProps {
  question: string;
  onSend: (answer: string) => void;
  onDismiss: () => void;
}

const PreTaskQuestion = React.memo(function PreTaskQuestion({
  question,
  onSend,
  onDismiss,
}: PreTaskQuestionProps): React.ReactElement {
  const [answer, setAnswer] = useState('');
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // Auto-focus so user can start typing immediately
  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const handleSend = () => {
    const trimmed = answer.trim();
    if (!trimmed) return;
    onSend(trimmed);
    setAnswer('');
  };

  const handleKey = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    } else if (e.key === 'Escape') {
      onDismiss();
    }
  };

  return (
    <div
      className="mx-3 mb-2 rounded-xl overflow-hidden animate-[fadeSlideIn_0.3s_ease-out_both]"
      style={{
        background: 'var(--bg-card)',
        border: '1px solid rgba(99, 179, 237, 0.35)',
        boxShadow: '0 2px 16px rgba(99, 179, 237, 0.08)',
      }}
    >
      {/* Blue accent stripe */}
      <div
        className="h-0.5 w-full"
        style={{ background: 'linear-gradient(90deg, #63b3ed, #76e4f7)' }}
      />

      {/* Header */}
      <div
        className="flex items-center justify-between px-3 py-2"
        style={{ borderBottom: '1px solid var(--border-dim)' }}
      >
        <div className="flex items-center gap-2">
          <span style={{ color: '#63b3ed', fontSize: 14 }}>❓</span>
          <span
            className="text-[11px] font-bold uppercase tracking-widest"
            style={{ color: '#63b3ed', fontFamily: 'var(--font-mono)' }}
          >
            Orchestrator question
          </span>
        </div>
        <button
          onClick={onDismiss}
          className="w-5 h-5 rounded flex items-center justify-center transition-colors focus:outline-none"
          style={{ color: 'var(--text-muted)' }}
          aria-label="Dismiss question"
          onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--text-primary)'; e.currentTarget.style.background = 'var(--bg-elevated)'; }}
          onMouseLeave={(e) => { e.currentTarget.style.color = 'var(--text-muted)'; e.currentTarget.style.background = 'transparent'; }}
        >
          <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
            <path d="M18 6 6 18M6 6l12 12" />
          </svg>
        </button>
      </div>

      {/* Question text */}
      <div
        className="px-3 py-2.5 text-sm leading-relaxed"
        style={{
          color: 'var(--text-secondary)',
          fontFamily: 'var(--font-display)',
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
        }}
      >
        {question}
      </div>

      {/* Answer input */}
      <div
        className="flex items-end gap-2 px-3 pb-3"
        style={{ borderTop: '1px solid var(--border-dim)', paddingTop: 8 }}
      >
        <textarea
          ref={inputRef}
          value={answer}
          onChange={(e) => setAnswer(e.target.value)}
          onKeyDown={handleKey}
          placeholder="Type your answer… (Enter to send)"
          rows={2}
          className="flex-1 resize-none rounded-lg px-3 py-2 text-sm focus:outline-none"
          style={{
            background: 'var(--bg-elevated)',
            border: '1px solid var(--border-subtle)',
            color: 'var(--text-primary)',
            fontFamily: 'var(--font-display)',
            fontSize: 13,
            lineHeight: 1.5,
          }}
        />
        <button
          onClick={handleSend}
          disabled={!answer.trim()}
          className="flex-shrink-0 px-3 py-2 rounded-lg text-xs font-semibold transition-all focus:outline-none disabled:opacity-40"
          style={{
            background: answer.trim() ? '#63b3ed' : 'var(--bg-elevated)',
            color: answer.trim() ? '#0a1628' : 'var(--text-muted)',
            fontFamily: 'var(--font-mono)',
          }}
        >
          Send
        </button>
      </div>
    </div>
  );
});

export default PreTaskQuestion;

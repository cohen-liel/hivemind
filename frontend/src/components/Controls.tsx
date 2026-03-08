import { useState } from 'react';

interface Props {
  projectId: string;
  status: string;
  agents: string[];
  onPause: () => void;
  onResume: () => void;
  onStop: () => void;
  onSend: (message: string, agent?: string) => void;
}

export default function Controls({ status, agents, onPause, onResume, onStop, onSend }: Props) {
  const [message, setMessage] = useState('');
  const [targetAgent, setTargetAgent] = useState('orchestrator');
  const [sending, setSending] = useState(false);

  const handleSend = async () => {
    if (!message.trim() || sending) return;
    setSending(true);
    try {
      await onSend(message.trim(), targetAgent === 'orchestrator' ? undefined : targetAgent);
      setMessage('');
    } finally {
      setSending(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const isActive = status === 'running' || status === 'paused';

  return (
    <div className="sticky bottom-0 z-20 safe-area-bottom"
      style={{
        background: 'var(--bg-panel)',
        borderTop: '1px solid var(--border-dim)',
      }}>

      {/* Control buttons */}
      {isActive && (
        <div className="flex items-center gap-1 px-4 pt-2.5 pb-1">
          {status === 'running' && (
            <button onClick={onPause}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all hover:bg-[rgba(245,166,35,0.08)]"
              style={{ color: 'var(--accent-amber)' }}>
              <svg width="11" height="11" viewBox="0 0 16 16" fill="currentColor"><rect x="4" y="3" width="3" height="10" rx="0.5"/><rect x="9" y="3" width="3" height="10" rx="0.5"/></svg>
              Pause
            </button>
          )}
          {status === 'paused' && (
            <button onClick={onResume}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all hover:bg-[rgba(61,214,140,0.08)]"
              style={{ color: 'var(--accent-green)' }}>
              <svg width="11" height="11" viewBox="0 0 16 16" fill="currentColor"><path d="M4 3l9 5-9 5V3z"/></svg>
              Resume
            </button>
          )}
          <button onClick={onStop}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all hover:bg-[rgba(245,71,91,0.08)]"
            style={{ color: 'var(--accent-red)' }}>
            <svg width="11" height="11" viewBox="0 0 16 16" fill="currentColor"><rect x="3" y="3" width="10" height="10" rx="1.5"/></svg>
            Stop
          </button>
        </div>
      )}

      {/* Input row */}
      <div className="flex items-end gap-2 px-3 py-2.5">
        {agents.length > 1 && (
          <select
            value={targetAgent}
            onChange={(e) => setTargetAgent(e.target.value)}
            className="flex-shrink-0 appearance-none cursor-pointer max-w-[70px] rounded-lg px-2.5 py-2.5 text-[13px] focus:outline-none"
            style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border-subtle)', color: 'var(--text-secondary)' }}>
            <option value="orchestrator">all</option>
            {agents.filter(a => a !== 'orchestrator').map(a => (
              <option key={a} value={a}>{a}</option>
            ))}
          </select>
        )}

        <textarea
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={status === 'idle' ? 'Send a task…' : 'Send a message…'}
          rows={1}
          className="flex-1 min-w-0 rounded-xl px-4 py-2.5 text-[15px] resize-none focus:outline-none transition-colors"
          style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border-subtle)', color: 'var(--text-primary)' }}
        />

        <button
          onClick={handleSend}
          disabled={!message.trim() || sending}
          className="flex-shrink-0 p-2.5 rounded-xl transition-all"
          style={{
            background: message.trim() && !sending ? 'var(--accent-blue)' : 'var(--bg-elevated)',
            color: message.trim() && !sending ? 'white' : 'var(--text-muted)',
            boxShadow: message.trim() && !sending ? '0 2px 10px rgba(99,140,255,0.3)' : 'none',
          }}>
          {sending ? (
            <svg className="w-4 h-4 animate-spin" viewBox="0 0 16 16" fill="none">
              <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="2" strokeDasharray="28" strokeDashoffset="8" strokeLinecap="round"/>
            </svg>
          ) : (
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
              <path d="M14 2L7 9M14 2l-5 12-2-5-5-2 12-5z" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
          )}
        </button>
      </div>
    </div>
  );
}

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
  const [showConsole, setShowConsole] = useState(false);

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
    <div className="border-t border-gray-800 bg-gray-900/95 backdrop-blur-md sticky bottom-0 z-20 safe-area-bottom">
      {/* Control buttons row */}
      {isActive && (
        <div className="flex items-center justify-between px-4 pt-2 pb-1">
          <div className="flex items-center gap-1">
            {status === 'running' && (
              <button
                onClick={onPause}
                className="flex items-center gap-1.5 px-3 py-1.5 hover:bg-yellow-500/10 text-yellow-500 rounded-lg transition-colors text-xs font-medium"
              >
                <svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor">
                  <rect x="4" y="3" width="3" height="10" rx="0.5"/>
                  <rect x="9" y="3" width="3" height="10" rx="0.5"/>
                </svg>
                Pause
              </button>
            )}
            {status === 'paused' && (
              <button
                onClick={onResume}
                className="flex items-center gap-1.5 px-3 py-1.5 hover:bg-green-500/10 text-green-500 rounded-lg transition-colors text-xs font-medium"
              >
                <svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor">
                  <path d="M4 3l9 5-9 5V3z"/>
                </svg>
                Resume
              </button>
            )}
            <button
              onClick={onStop}
              className="flex items-center gap-1.5 px-3 py-1.5 hover:bg-red-500/10 text-red-500 rounded-lg transition-colors text-xs font-medium"
            >
              <svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor">
                <rect x="3" y="3" width="10" height="10" rx="1"/>
              </svg>
              Stop
            </button>
          </div>

          {/* Console toggle */}
          <button
            onClick={() => setShowConsole(!showConsole)}
            className={`p-1.5 rounded-lg transition-colors text-xs ${showConsole ? 'bg-gray-800 text-gray-300' : 'text-gray-600 hover:text-gray-400'}`}
            title="Toggle activity log"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <polyline points="4 17 10 11 4 5"/>
              <line x1="12" y1="19" x2="20" y2="19"/>
            </svg>
          </button>
        </div>
      )}

      {/* Input row */}
      <div className="flex items-end gap-2 px-3 py-2.5">
        {/* Agent selector */}
        {agents.length > 1 && (
          <select
            value={targetAgent}
            onChange={(e) => setTargetAgent(e.target.value)}
            className="bg-gray-800 border border-gray-700/50 text-gray-400 text-[11px] rounded-lg
                       px-2 py-2.5 focus:border-blue-500 focus:outline-none flex-shrink-0
                       appearance-none cursor-pointer"
          >
            <option value="orchestrator">all</option>
            {agents.filter(a => a !== 'orchestrator').map(a => (
              <option key={a} value={a}>{a}</option>
            ))}
          </select>
        )}

        {/* Input */}
        <textarea
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={status === 'idle' ? 'Send a task...' : 'Send a message...'}
          rows={1}
          className="flex-1 bg-gray-800/80 border border-gray-700/50 text-gray-200 text-base rounded-xl px-4 py-2.5
                     focus:border-blue-500/50 focus:outline-none resize-none min-w-0
                     placeholder-gray-600"
        />

        {/* Send button */}
        <button
          onClick={handleSend}
          disabled={!message.trim() || sending}
          className={`p-2.5 rounded-xl transition-all flex-shrink-0
            ${message.trim() && !sending
              ? 'bg-blue-600 hover:bg-blue-500 text-white shadow-[0_0_12px_rgba(59,130,246,0.3)]'
              : 'bg-gray-800/50 text-gray-600'}`}
        >
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

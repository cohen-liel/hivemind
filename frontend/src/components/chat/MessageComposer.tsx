import { useState, useRef, useCallback } from 'react';

interface MessageComposerProps {
  onSend: (content: string) => Promise<boolean>;
  onTyping: (isTyping: boolean) => void;
  disabled?: boolean;
  placeholder?: string;
}

export default function MessageComposer({
  onSend,
  onTyping,
  disabled = false,
  placeholder = 'Type a message...',
}: MessageComposerProps): JSX.Element {
  const [value, setValue] = useState('');
  const [sending, setSending] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const adjustHeight = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, []);

  const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>): void => {
    setValue(e.target.value);
    onTyping(e.target.value.length > 0);
    adjustHeight();
  };

  const handleSend = async (): Promise<void> => {
    if (!value.trim() || sending || disabled) return;
    setSending(true);
    const ok = await onSend(value.trim());
    if (ok) {
      setValue('');
      onTyping(false);
      if (textareaRef.current) {
        textareaRef.current.style.height = 'auto';
      }
    }
    setSending(false);
    textareaRef.current?.focus();
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>): void => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div
      className="flex items-end gap-2 px-4 py-3 flex-shrink-0"
      style={{ borderTop: '1px solid var(--border-dim)' }}
    >
      <div
        className="flex-1 rounded-xl overflow-hidden transition-all duration-150"
        style={{
          background: 'var(--bg-card)',
          border: '1px solid var(--border-subtle)',
        }}
      >
        <textarea
          ref={textareaRef}
          value={value}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          placeholder={placeholder}
          disabled={disabled}
          rows={1}
          className="w-full px-3 py-2.5 text-sm bg-transparent resize-none outline-none"
          style={{
            color: 'var(--text-primary)',
            fontFamily: 'var(--font-display)',
            maxHeight: '160px',
          }}
          aria-label="Message input"
        />
      </div>

      <button
        onClick={handleSend}
        disabled={!value.trim() || sending || disabled}
        className="p-2.5 rounded-xl transition-all duration-200 disabled:opacity-30 flex-shrink-0"
        style={{
          background: value.trim() ? 'var(--accent-blue)' : 'var(--bg-elevated)',
          color: value.trim() ? 'white' : 'var(--text-muted)',
        }}
        aria-label="Send message"
      >
        {sending ? (
          <span className="inline-block w-5 h-5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
        ) : (
          <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
            <path
              d="M3 10l14-7-7 14v-7H3z"
              fill="currentColor"
            />
          </svg>
        )}
      </button>
    </div>
  );
}

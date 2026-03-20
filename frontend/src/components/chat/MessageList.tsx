import { useRef, useEffect } from 'react';
import type { ChatMessage } from '../../types';

interface MessageListProps {
  messages: ChatMessage[];
  hasMore: boolean;
  onLoadMore: () => void;
}

function formatTime(ts: number): string {
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function formatDate(ts: number): string {
  const d = new Date(ts * 1000);
  const now = new Date();
  const diff = Math.floor((now.getTime() - d.getTime()) / 86400000);
  if (diff === 0) return 'Today';
  if (diff === 1) return 'Yesterday';
  return d.toLocaleDateString([], { month: 'short', day: 'numeric' });
}

function shouldShowDate(messages: ChatMessage[], index: number): boolean {
  if (index === 0) return true;
  const curr = new Date(messages[index].created_at * 1000).toDateString();
  const prev = new Date(messages[index - 1].created_at * 1000).toDateString();
  return curr !== prev;
}

function renderMarkdown(content: string): string {
  // Basic markdown: **bold**, *italic*, `code`, ```code blocks```
  return content
    .replace(/```([^`]+)```/g, '<pre class="code-block my-1"><code>$1</code></pre>')
    .replace(/`([^`]+)`/g, '<code style="background:var(--bg-elevated);padding:1px 4px;border-radius:4px;font-family:var(--font-mono);font-size:0.85em">$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/\*([^*]+)\*/g, '<em>$1</em>')
    .replace(/\n/g, '<br/>');
}

export default function MessageList({ messages, hasMore, onLoadMore }: MessageListProps): JSX.Element {
  const scrollRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const prevLengthRef = useRef(0);

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    if (messages.length > prevLengthRef.current) {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
    prevLengthRef.current = messages.length;
  }, [messages.length]);

  return (
    <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-3 space-y-1">
      {/* Load more */}
      {hasMore && (
        <div className="text-center py-2">
          <button
            onClick={onLoadMore}
            className="text-xs px-3 py-1.5 rounded-lg transition-colors duration-150"
            style={{ color: 'var(--accent-blue)', background: 'var(--bg-elevated)' }}
            aria-label="Load older messages"
          >
            Load older messages
          </button>
        </div>
      )}

      {messages.map((msg, i) => {
        if (msg.is_deleted) {
          return (
            <div
              key={msg.id}
              className="px-3 py-1.5 text-xs italic"
              style={{ color: 'var(--text-muted)' }}
            >
              Message deleted
            </div>
          );
        }

        const showDate = shouldShowDate(messages, i);

        return (
          <div key={msg.id}>
            {/* Date separator */}
            {showDate && (
              <div className="flex items-center gap-3 py-3">
                <div className="flex-1 h-px" style={{ background: 'var(--border-dim)' }} />
                <span
                  className="text-[10px] font-semibold uppercase tracking-wider"
                  style={{ color: 'var(--text-muted)' }}
                >
                  {formatDate(msg.created_at)}
                </span>
                <div className="flex-1 h-px" style={{ background: 'var(--border-dim)' }} />
              </div>
            )}

            {/* Message */}
            <div
              className="group flex gap-3 px-3 py-2 rounded-xl transition-colors duration-100 message-enter"
              style={{ '--animation-delay': `${(i % 10) * 30}ms` } as React.CSSProperties}
            >
              {/* Avatar */}
              <div
                className="w-8 h-8 rounded-lg flex items-center justify-center text-xs font-bold flex-shrink-0 mt-0.5"
                style={{
                  background: 'var(--bg-elevated)',
                  color: 'var(--text-secondary)',
                  border: '1px solid var(--border-dim)',
                }}
              >
                {msg.sender_id.slice(0, 2).toUpperCase()}
              </div>

              <div className="flex-1 min-w-0">
                <div className="flex items-baseline gap-2">
                  <span
                    className="text-sm font-semibold"
                    style={{ color: 'var(--text-primary)' }}
                  >
                    {msg.sender_id}
                  </span>
                  <span
                    className="text-[10px] tabular-nums"
                    style={{ color: 'var(--text-muted)' }}
                  >
                    {formatTime(msg.created_at)}
                  </span>
                  {msg.updated_at && (
                    <span
                      className="text-[9px]"
                      style={{ color: 'var(--text-muted)' }}
                    >
                      (edited)
                    </span>
                  )}
                </div>

                <div
                  className="text-sm mt-0.5 leading-relaxed break-words"
                  style={{ color: 'var(--text-secondary)' }}
                  dangerouslySetInnerHTML={{ __html: renderMarkdown(msg.content) }}
                />

                {/* Thread indicator */}
                {msg.thread_count && msg.thread_count > 0 && (
                  <button
                    className="flex items-center gap-1 mt-1.5 text-[11px] font-medium rounded-md px-2 py-0.5 transition-colors duration-150"
                    style={{ color: 'var(--accent-blue)', background: 'var(--bg-elevated)' }}
                    aria-label={`${msg.thread_count} replies`}
                  >
                    <svg width="12" height="12" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                      <path d="M2 5h9a3 3 0 010 6H8" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
                      <path d="M10 8l-3 3M10 8l-3-3" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                    {msg.thread_count} {msg.thread_count === 1 ? 'reply' : 'replies'}
                  </button>
                )}

                {/* Reactions */}
                {msg.reactions && Object.keys(msg.reactions).length > 0 && (
                  <div className="flex flex-wrap gap-1 mt-1.5">
                    {Object.entries(msg.reactions).map(([emoji, users]) => (
                      <span
                        key={emoji}
                        className="inline-flex items-center gap-1 text-xs px-1.5 py-0.5 rounded-md cursor-pointer transition-colors duration-150"
                        style={{
                          background: 'var(--bg-elevated)',
                          border: '1px solid var(--border-dim)',
                          color: 'var(--text-secondary)',
                        }}
                        title={users.join(', ')}
                      >
                        {emoji}
                        <span className="text-[10px] tabular-nums">{users.length}</span>
                      </span>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>
        );
      })}

      {messages.length === 0 && (
        <div className="flex flex-col items-center justify-center h-full py-12" style={{ color: 'var(--text-muted)' }}>
          <svg width="48" height="48" viewBox="0 0 48 48" fill="none" className="mb-4 opacity-30">
            <rect x="6" y="10" width="36" height="28" rx="4" stroke="currentColor" strokeWidth="2" />
            <path d="M14 22h20M14 28h12" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
          </svg>
          <p className="text-sm font-medium">No messages yet</p>
          <p className="text-xs mt-1">Start the conversation!</p>
        </div>
      )}

      <div ref={bottomRef} />
    </div>
  );
}

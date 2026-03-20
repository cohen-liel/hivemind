import { useChat } from '../../hooks/useChat';
import ChannelList from './ChannelList';
import MessageList from './MessageList';
import MessageComposer from './MessageComposer';
import TypingIndicator from './TypingIndicator';
import { useState } from 'react';

interface ChatPanelProps {
  circleId?: string;
}

export default function ChatPanel({ circleId }: ChatPanelProps): JSX.Element {
  const {
    channels,
    activeChannel,
    messages,
    typingUsers,
    loading,
    hasMore,
    totalUnread,
    selectChannel,
    sendMessage,
    loadMore,
    createChannel,
    setTyping,
  } = useChat(circleId);

  const [showNewChannel, setShowNewChannel] = useState(false);
  const [newChannelName, setNewChannelName] = useState('');

  const handleCreateChannel = async (): Promise<void> => {
    if (!newChannelName.trim()) return;
    await createChannel(newChannelName.trim());
    setNewChannelName('');
    setShowNewChannel(false);
  };

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center" style={{ color: 'var(--text-muted)' }}>
        <div className="flex flex-col items-center gap-3">
          <span className="inline-block w-6 h-6 border-2 rounded-full animate-spin"
            style={{ borderColor: 'var(--border-subtle)', borderTopColor: 'var(--accent-blue)' }} />
          <span className="text-sm">Loading chat...</span>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full rounded-2xl overflow-hidden"
      style={{ background: 'var(--bg-panel)', border: '1px solid var(--border-dim)' }}>

      {/* Channel sidebar */}
      <div
        className="w-56 flex-shrink-0 hidden sm:flex flex-col"
        style={{ borderRight: '1px solid var(--border-dim)' }}
      >
        <ChannelList
          channels={channels}
          activeChannelId={activeChannel?.id ?? null}
          onSelectChannel={selectChannel}
          onCreateChannel={() => setShowNewChannel(true)}
        />
      </div>

      {/* Main chat area */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Channel header */}
        {activeChannel && (
          <div
            className="flex items-center gap-3 px-4 py-3 flex-shrink-0"
            style={{ borderBottom: '1px solid var(--border-dim)' }}
          >
            {/* Mobile channel selector */}
            <div className="sm:hidden">
              <select
                value={activeChannel.id}
                onChange={e => selectChannel(e.target.value)}
                className="hivemind-input text-sm px-2 py-1 rounded-lg"
                aria-label="Select channel"
              >
                {channels.map(ch => (
                  <option key={ch.id} value={ch.id}>
                    # {ch.name} {ch.unread_count > 0 ? `(${ch.unread_count})` : ''}
                  </option>
                ))}
              </select>
            </div>

            <div className="hidden sm:flex items-center gap-2">
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true"
                style={{ color: 'var(--text-muted)' }}>
                <path d="M3 6h10M3 10h10M6 3l-1.5 10M11 3l-1.5 10"
                  stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
              </svg>
              <h2 className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>
                {activeChannel.name}
              </h2>
            </div>

            {activeChannel.description && (
              <span
                className="text-xs truncate hidden md:block"
                style={{ color: 'var(--text-muted)' }}
              >
                {activeChannel.description}
              </span>
            )}

            {totalUnread > 0 && (
              <span
                className="ml-auto text-[10px] font-bold px-1.5 py-0.5 rounded-full"
                style={{ background: 'var(--accent-blue)', color: 'white' }}
              >
                {totalUnread} unread
              </span>
            )}
          </div>
        )}

        {/* Messages */}
        <MessageList messages={messages} hasMore={hasMore} onLoadMore={loadMore} />

        {/* Typing indicator */}
        <TypingIndicator users={typingUsers} />

        {/* Composer */}
        <MessageComposer
          onSend={sendMessage}
          onTyping={setTyping}
          disabled={!activeChannel}
          placeholder={activeChannel ? `Message #${activeChannel.name}` : 'Select a channel...'}
        />
      </div>

      {/* New channel dialog */}
      {showNewChannel && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4" role="dialog" aria-modal="true">
          <div
            className="absolute inset-0"
            style={{ background: 'rgba(0,0,0,0.6)', backdropFilter: 'blur(4px)' }}
            onClick={() => setShowNewChannel(false)}
          />
          <div
            className="relative w-full max-w-sm rounded-2xl p-5 space-y-4"
            style={{
              background: 'var(--bg-panel)',
              border: '1px solid var(--border-subtle)',
              boxShadow: '0 24px 48px rgba(0,0,0,0.4)',
              animation: 'slideUp 0.25s ease-out',
            }}
          >
            <h3 className="text-base font-bold" style={{ color: 'var(--text-primary)' }}>
              New Channel
            </h3>
            <input
              type="text"
              value={newChannelName}
              onChange={e => setNewChannelName(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') handleCreateChannel(); }}
              placeholder="Channel name"
              className="hivemind-input w-full px-3 py-2.5 text-sm rounded-xl"
              autoFocus
              aria-label="Channel name"
            />
            <div className="flex gap-2 justify-end">
              <button
                onClick={() => setShowNewChannel(false)}
                className="px-3 py-2 text-sm rounded-xl"
                style={{ color: 'var(--text-secondary)', background: 'var(--bg-elevated)' }}
              >
                Cancel
              </button>
              <button
                onClick={handleCreateChannel}
                disabled={!newChannelName.trim()}
                className="px-4 py-2 text-sm font-semibold rounded-xl disabled:opacity-40"
                style={{ background: 'var(--accent-blue)', color: 'white' }}
              >
                Create
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

import type { ChatChannel } from '../../types';

interface ChannelListProps {
  channels: ChatChannel[];
  activeChannelId: string | null;
  onSelectChannel: (id: string) => void;
  onCreateChannel: () => void;
}

export default function ChannelList({
  channels,
  activeChannelId,
  onSelectChannel,
  onCreateChannel,
}: ChannelListProps): JSX.Element {
  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div
        className="flex items-center justify-between px-4 py-3 flex-shrink-0"
        style={{ borderBottom: '1px solid var(--border-dim)' }}
      >
        <h3
          className="text-xs font-bold uppercase tracking-wider"
          style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}
        >
          Channels
        </h3>
        <button
          onClick={onCreateChannel}
          className="p-1.5 rounded-lg transition-colors duration-150"
          style={{ color: 'var(--text-muted)' }}
          aria-label="Create new channel"
          title="New channel"
        >
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none">
            <path d="M8 3v10M3 8h10" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
          </svg>
        </button>
      </div>

      {/* Channel list */}
      <div className="flex-1 overflow-y-auto py-1 px-2 space-y-0.5">
        {channels.map(channel => {
          const isActive = channel.id === activeChannelId;
          return (
            <button
              key={channel.id}
              onClick={() => onSelectChannel(channel.id)}
              className={`w-full flex items-center gap-2.5 px-3 py-2 text-[13px] rounded-xl transition-all duration-150 text-left ${
                isActive ? 'font-semibold' : 'font-medium'
              }`}
              style={{
                background: isActive ? 'var(--bg-elevated)' : 'transparent',
                color: isActive ? 'var(--text-primary)' : 'var(--text-secondary)',
                borderLeft: isActive ? '2px solid var(--accent-blue)' : '2px solid transparent',
              }}
              aria-current={isActive ? 'page' : undefined}
              aria-label={`${channel.name}${channel.unread_count ? `, ${channel.unread_count} unread` : ''}`}
            >
              {/* Hash icon */}
              <svg
                width="14"
                height="14"
                viewBox="0 0 16 16"
                fill="none"
                className="flex-shrink-0"
                aria-hidden="true"
                style={{ opacity: isActive ? 1 : 0.5 }}
              >
                <path
                  d="M3 6h10M3 10h10M6 3l-1.5 10M11 3l-1.5 10"
                  stroke="currentColor"
                  strokeWidth="1.3"
                  strokeLinecap="round"
                />
              </svg>

              <span className="flex-1 truncate">{channel.name}</span>

              {/* Unread badge */}
              {channel.unread_count > 0 && (
                <span
                  className="min-w-[18px] h-[18px] flex items-center justify-center text-[10px] font-bold rounded-full px-1"
                  style={{
                    background: 'var(--accent-blue)',
                    color: 'white',
                  }}
                >
                  {channel.unread_count > 99 ? '99+' : channel.unread_count}
                </span>
              )}
            </button>
          );
        })}

        {channels.length === 0 && (
          <div className="text-center py-8" style={{ color: 'var(--text-muted)' }}>
            <p className="text-sm">No channels yet</p>
            <button
              onClick={onCreateChannel}
              className="text-xs mt-2 underline"
              style={{ color: 'var(--accent-blue)' }}
            >
              Create one
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

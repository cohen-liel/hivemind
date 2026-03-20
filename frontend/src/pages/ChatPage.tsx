import ChatPanel from '../components/chat/ChatPanel';

export default function ChatPage(): JSX.Element {
  return (
    <div className="h-full flex flex-col p-4 lg:p-6">
      {/* Header */}
      <div className="flex items-center gap-3 mb-4 flex-shrink-0">
        <div
          className="w-8 h-8 rounded-lg flex items-center justify-center"
          style={{
            background: 'linear-gradient(135deg, var(--accent-blue), var(--accent-cyan))',
          }}
        >
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
            <path d="M2 3.5A1.5 1.5 0 013.5 2h9A1.5 1.5 0 0114 3.5v7a1.5 1.5 0 01-1.5 1.5H6l-3 2.5V12H3.5A1.5 1.5 0 012 10.5v-7z"
              stroke="white" strokeWidth="1.3" strokeLinejoin="round" />
            <path d="M5.5 6h5M5.5 8.5h3" stroke="white" strokeWidth="1.3" strokeLinecap="round" />
          </svg>
        </div>
        <div>
          <h1
            className="text-lg font-bold"
            style={{ color: 'var(--text-primary)', fontFamily: 'var(--font-display)' }}
          >
            Chat
          </h1>
          <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
            Real-time team communication
          </p>
        </div>
      </div>

      {/* Chat panel takes remaining space */}
      <div className="flex-1 min-h-0">
        <ChatPanel />
      </div>
    </div>
  );
}

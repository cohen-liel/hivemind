import { useState } from 'react';
import { useCircles, useCircleDetail } from '../hooks/useCircles';
import CircleCard from '../components/circles/CircleCard';
import CircleMembers from '../components/circles/CircleMembers';
import CreateCircleDialog from '../components/circles/CreateCircleDialog';
import ChatPanel from '../components/chat/ChatPanel';

export default function CirclesPage(): JSX.Element {
  const { circles, loading, error, create, remove } = useCircles();
  const [activeCircleId, setActiveCircleId] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [activeTab, setActiveTab] = useState<'projects' | 'members' | 'chat'>('projects');

  const { circle, members, projects, loading: detailLoading, addMember, removeMember } =
    useCircleDetail(activeCircleId);

  const handleDelete = async (id: string): Promise<void> => {
    if (!window.confirm('Delete this circle? This cannot be undone.')) return;
    const ok = await remove(id);
    if (ok && activeCircleId === id) setActiveCircleId(null);
  };

  // Loading state
  if (loading) {
    return (
      <div className="flex h-full items-center justify-center" style={{ color: 'var(--text-muted)' }}>
        <div className="flex flex-col items-center gap-3">
          <span className="inline-block w-6 h-6 border-2 rounded-full animate-spin"
            style={{ borderColor: 'var(--border-subtle)', borderTopColor: 'var(--accent-purple)' }} />
          <span className="text-sm">Loading circles...</span>
        </div>
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col lg:flex-row">
      {/* Circle list sidebar */}
      <div
        className={`lg:w-80 flex-shrink-0 flex flex-col overflow-hidden ${
          activeCircleId ? 'hidden lg:flex' : 'flex'
        }`}
        style={{
          borderRight: '1px solid var(--border-dim)',
          background: 'var(--bg-panel)',
        }}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 flex-shrink-0"
          style={{ borderBottom: '1px solid var(--border-dim)' }}>
          <div>
            <h1
              className="text-lg font-bold"
              style={{ color: 'var(--text-primary)', fontFamily: 'var(--font-display)' }}
            >
              Circles
            </h1>
            <p className="text-xs mt-0.5" style={{ color: 'var(--text-muted)' }}>
              {circles.length} circle{circles.length !== 1 ? 's' : ''}
            </p>
          </div>
          <button
            onClick={() => setShowCreate(true)}
            className="p-2.5 rounded-xl transition-all duration-200"
            style={{
              background: 'linear-gradient(135deg, var(--accent-purple), var(--accent-blue))',
              color: 'white',
              boxShadow: '0 3px 12px rgba(167, 139, 250, 0.3)',
            }}
            aria-label="Create new circle"
            title="New circle"
          >
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
              <path d="M8 3v10M3 8h10" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
            </svg>
          </button>
        </div>

        {/* Error */}
        {error && (
          <div
            className="mx-4 mt-3 px-3 py-2 rounded-xl text-xs"
            style={{ background: 'var(--status-stopped-bg)', color: 'var(--status-stopped-text)' }}
          >
            {error}
          </div>
        )}

        {/* Circle cards */}
        <div className="flex-1 overflow-y-auto p-3 space-y-2">
          {circles.map((c, i) => (
            <div
              key={c.id}
              className="stagger-item"
              style={{ animationDelay: `${i * 50}ms` }}
            >
              <CircleCard
                circle={c}
                isActive={activeCircleId === c.id}
                onClick={() => {
                  setActiveCircleId(c.id);
                  setActiveTab('projects');
                }}
              />
            </div>
          ))}

          {circles.length === 0 && (
            <div className="text-center py-12" style={{ color: 'var(--text-muted)' }}>
              <div className="text-4xl mb-3 opacity-50">⭕</div>
              <p className="text-sm font-medium">No circles yet</p>
              <p className="text-xs mt-1">Create one to start collaborating</p>
              <button
                onClick={() => setShowCreate(true)}
                className="mt-4 px-4 py-2 text-sm font-medium rounded-xl"
                style={{ background: 'var(--bg-elevated)', color: 'var(--accent-purple)' }}
              >
                Create Circle
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Detail panel */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        {activeCircleId && circle ? (
          <>
            {/* Circle header */}
            <div
              className="flex items-center gap-4 px-5 py-4 flex-shrink-0"
              style={{ borderBottom: '1px solid var(--border-dim)' }}
            >
              {/* Mobile back */}
              <button
                onClick={() => setActiveCircleId(null)}
                className="lg:hidden p-1.5 rounded-lg"
                style={{ color: 'var(--text-muted)' }}
                aria-label="Back to circles"
              >
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                  <path d="M10 3L5 8l5 5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </button>

              <div className="flex-1 min-w-0">
                <h2 className="text-base font-bold truncate" style={{ color: 'var(--text-primary)' }}>
                  {circle.name}
                </h2>
                {circle.description && (
                  <p className="text-xs truncate" style={{ color: 'var(--text-muted)' }}>
                    {circle.description}
                  </p>
                )}
              </div>

              <button
                onClick={() => handleDelete(circle.id)}
                className="p-2 rounded-lg transition-colors duration-150"
                style={{ color: 'var(--accent-red)' }}
                aria-label="Delete circle"
                title="Delete circle"
              >
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                  <path d="M3 4h10M5.5 4V3a1 1 0 011-1h3a1 1 0 011 1v1M6 7v4M10 7v4M4 4l.7 9.1a1 1 0 001 .9h4.6a1 1 0 001-.9L12 4"
                    stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </button>
            </div>

            {/* Tabs */}
            <div
              className="flex px-5 gap-1 flex-shrink-0"
              style={{ borderBottom: '1px solid var(--border-dim)' }}
              role="tablist"
            >
              {(['projects', 'members', 'chat'] as const).map(tab => (
                <button
                  key={tab}
                  onClick={() => setActiveTab(tab)}
                  className="px-3 py-2.5 text-sm font-medium capitalize transition-colors duration-150 relative"
                  style={{
                    color: activeTab === tab ? 'var(--text-primary)' : 'var(--text-muted)',
                  }}
                  role="tab"
                  aria-selected={activeTab === tab}
                  aria-controls={`panel-${tab}`}
                >
                  {tab}
                  {activeTab === tab && (
                    <span
                      className="absolute bottom-0 left-1 right-1 h-0.5 rounded-full"
                      style={{ background: 'var(--accent-purple)' }}
                    />
                  )}
                </button>
              ))}
            </div>

            {/* Tab content */}
            <div className="flex-1 overflow-y-auto">
              {activeTab === 'projects' && (
                <div className="p-5 space-y-3" id="panel-projects" role="tabpanel">
                  {detailLoading ? (
                    <div className="space-y-3">
                      {[1, 2, 3].map(i => (
                        <div key={i} className="skeleton h-16 rounded-xl" />
                      ))}
                    </div>
                  ) : projects.length > 0 ? (
                    projects.map(p => (
                      <div
                        key={p.project_id}
                        className="flex items-center gap-3 px-4 py-3 rounded-xl interactive-card"
                        style={{
                          background: 'var(--bg-card)',
                          border: '1px solid var(--border-dim)',
                        }}
                      >
                        <span
                          className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${
                            p.status === 'running' ? 'animate-pulse' : ''
                          }`}
                          style={{
                            background: p.status === 'running'
                              ? 'var(--accent-green)'
                              : p.status === 'paused'
                                ? 'var(--accent-amber)'
                                : 'var(--text-muted)',
                          }}
                        />
                        <div className="flex-1 min-w-0">
                          <span className="text-sm font-medium truncate block" style={{ color: 'var(--text-primary)' }}>
                            {p.project_name}
                          </span>
                        </div>
                        <span
                          className="text-[10px] font-medium uppercase px-2 py-0.5 rounded-md"
                          style={{
                            color: `var(--status-${p.status}-text, var(--text-muted))`,
                            background: `var(--status-${p.status}-bg, var(--bg-elevated))`,
                          }}
                        >
                          {p.status}
                        </span>
                      </div>
                    ))
                  ) : (
                    <div className="text-center py-8" style={{ color: 'var(--text-muted)' }}>
                      <p className="text-sm">No projects in this circle</p>
                    </div>
                  )}
                </div>
              )}

              {activeTab === 'members' && (
                <div className="p-5" id="panel-members" role="tabpanel">
                  {detailLoading ? (
                    <div className="space-y-2">
                      {[1, 2, 3].map(i => (
                        <div key={i} className="skeleton h-12 rounded-xl" />
                      ))}
                    </div>
                  ) : (
                    <CircleMembers
                      members={members}
                      onAddMember={addMember}
                      onRemoveMember={removeMember}
                    />
                  )}
                </div>
              )}

              {activeTab === 'chat' && (
                <div className="h-full" id="panel-chat" role="tabpanel">
                  <ChatPanel circleId={activeCircleId} />
                </div>
              )}
            </div>
          </>
        ) : (
          /* Empty state */
          <div className="hidden lg:flex flex-1 items-center justify-center" style={{ color: 'var(--text-muted)' }}>
            <div className="text-center">
              <div className="text-5xl mb-4 opacity-30">⭕</div>
              <p className="text-base font-medium">Select a circle</p>
              <p className="text-sm mt-1">or create a new one to get started</p>
            </div>
          </div>
        )}
      </div>

      {/* Create dialog */}
      <CreateCircleDialog
        isOpen={showCreate}
        onClose={() => setShowCreate(false)}
        onCreate={create}
      />
    </div>
  );
}

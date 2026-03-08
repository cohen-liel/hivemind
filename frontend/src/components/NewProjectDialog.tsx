import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { createProject, browseDirs } from '../api';
import type { DirEntry } from '../types';

export default function NewProjectDialog() {
  const [name, setName] = useState('');
  const [directory, setDirectory] = useState('');
  const [agentsCount, setAgentsCount] = useState(2);
  const [description, setDescription] = useState('');
  const [error, setError] = useState('');
  const [creating, setCreating] = useState(false);
  const [showBrowser, setShowBrowser] = useState(false);
  const [browsePath, setBrowsePath] = useState('~');
  const [dirEntries, setDirEntries] = useState<DirEntry[]>([]);
  const [currentDir, setCurrentDir] = useState('');
  const [parentDir, setParentDir] = useState<string | null>(null);
  const navigate = useNavigate();

  useEffect(() => {
    if (showBrowser) {
      browseDirs(browsePath).then(res => {
        setDirEntries(res.entries);
        setCurrentDir(res.current);
        setParentDir(res.parent);
      }).catch(() => setDirEntries([]));
    }
  }, [showBrowser, browsePath]);

  const handleCreate = async () => {
    setError('');
    if (!name.trim()) {
      setError('Project name is required');
      return;
    }
    if (!directory.trim()) {
      setError('Directory is required');
      return;
    }

    setCreating(true);
    try {
      const result = await createProject({
        name: name.trim(),
        directory: directory.trim(),
        agents_count: agentsCount,
        description: description.trim() || undefined,
      });
      navigate(`/project/${result.project_id}`);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to create project');
    } finally {
      setCreating(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleCreate();
    }
  };

  const selectDir = (path: string) => {
    setDirectory(path);
    setShowBrowser(false);
  };

  const agentOptions = [
    { value: 1, label: 'Solo', desc: 'Single agent', icon: '🎯' },
    { value: 2, label: 'Team', desc: 'Orchestrator + dev', icon: '👥' },
    { value: 3, label: 'Full Team', desc: '+ code reviewer', icon: '🏢' },
  ];

  const isValid = name.trim() && directory.trim();

  return (
    <div className="min-h-full flex items-start justify-center pt-8 sm:pt-12 px-4" style={{ background: 'var(--bg-void)' }}>
      {/* Gradient accent */}
      <div className="fixed top-0 left-0 right-0 h-[300px] pointer-events-none" style={{
        background: 'radial-gradient(ellipse at 50% 0%, rgba(99,140,255,0.08) 0%, transparent 60%)',
      }} />

      <div className="relative w-full max-w-lg animate-[slideUp_0.3s_ease-out]">
        {/* Header */}
        <div className="mb-8">
          <div className="flex items-center gap-3 mb-3">
            <div className="w-10 h-10 rounded-xl flex items-center justify-center text-xl"
              style={{ background: 'var(--glow-blue)', boxShadow: '0 0 20px var(--glow-blue)' }}>
              🚀
            </div>
            <div>
              <h1 className="text-2xl font-bold" style={{ color: 'var(--text-primary)', fontFamily: 'var(--font-display)' }}>
                New Project
              </h1>
              <p className="text-xs" style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
                Configure your agent workspace
              </p>
            </div>
          </div>
        </div>

        <div className="space-y-5">
          {/* Name */}
          <div>
            <label className="block text-sm font-medium mb-1.5" style={{ color: 'var(--text-secondary)' }}>
              Project Name
            </label>
            <input
              type="text"
              value={name}
              onChange={e => setName(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="my-awesome-project"
              className="w-full text-base rounded-xl px-4 py-3 focus:outline-none transition-all duration-200"
              style={{
                background: 'var(--bg-panel)',
                border: '1px solid var(--border-subtle)',
                color: 'var(--text-primary)',
                fontFamily: 'var(--font-display)',
              }}
              onFocus={e => { e.currentTarget.style.borderColor = 'var(--border-active)'; e.currentTarget.style.boxShadow = '0 0 0 3px rgba(99,140,255,0.08)'; }}
              onBlur={e => { e.currentTarget.style.borderColor = 'var(--border-subtle)'; e.currentTarget.style.boxShadow = 'none'; }}
              autoFocus
            />
          </div>

          {/* Directory */}
          <div>
            <label className="block text-sm font-medium mb-1.5" style={{ color: 'var(--text-secondary)' }}>
              Working Directory
            </label>
            <div className="flex gap-2">
              <input
                type="text"
                value={directory}
                onChange={e => setDirectory(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="~/projects/my-project"
                className="flex-1 text-base rounded-xl px-4 py-3 focus:outline-none transition-all duration-200"
                style={{
                  background: 'var(--bg-panel)',
                  border: '1px solid var(--border-subtle)',
                  color: 'var(--text-primary)',
                  fontFamily: 'var(--font-mono)',
                }}
                onFocus={e => { e.currentTarget.style.borderColor = 'var(--border-active)'; e.currentTarget.style.boxShadow = '0 0 0 3px rgba(99,140,255,0.08)'; }}
                onBlur={e => { e.currentTarget.style.borderColor = 'var(--border-subtle)'; e.currentTarget.style.boxShadow = 'none'; }}
              />
              <button
                onClick={() => setShowBrowser(!showBrowser)}
                className="px-4 py-3 text-sm font-medium rounded-xl transition-all duration-200 active:scale-95 flex-shrink-0"
                style={{
                  background: 'var(--bg-elevated)',
                  border: '1px solid var(--border-subtle)',
                  color: showBrowser ? 'var(--accent-blue)' : 'var(--text-secondary)',
                }}
                onMouseEnter={e => { e.currentTarget.style.borderColor = 'var(--border-active)'; }}
                onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--border-subtle)'; }}
              >
                📂 Browse
              </button>
            </div>

            {/* Directory browser */}
            {showBrowser && (
              <div className="mt-2 rounded-xl overflow-hidden animate-[slideUp_0.2s_ease-out]"
                style={{ background: 'var(--bg-panel)', border: '1px solid var(--border-subtle)' }}>
                <div className="px-3 py-2 flex items-center gap-2"
                  style={{ borderBottom: '1px solid var(--border-dim)' }}>
                  {parentDir && (
                    <button onClick={() => setBrowsePath(parentDir)}
                      className="p-1 rounded-lg transition-all active:scale-90"
                      style={{ color: 'var(--text-muted)' }}
                      onMouseEnter={e => { e.currentTarget.style.color = 'var(--text-primary)'; }}
                      onMouseLeave={e => { e.currentTarget.style.color = 'var(--text-muted)'; }}>
                      ←
                    </button>
                  )}
                  <span className="text-xs truncate" style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
                    {currentDir}
                  </span>
                  <button onClick={() => selectDir(currentDir)}
                    className="ml-auto text-xs font-medium flex-shrink-0 px-2 py-1 rounded-lg transition-all"
                    style={{ color: 'var(--accent-blue)' }}>
                    Select this
                  </button>
                </div>
                <div className="max-h-48 overflow-y-auto">
                  {dirEntries.map(entry => (
                    <button
                      key={entry.path}
                      onClick={() => entry.is_dir ? setBrowsePath(entry.path) : undefined}
                      onDoubleClick={() => selectDir(entry.path)}
                      className="w-full text-left px-3 py-2 text-sm flex items-center gap-2 transition-colors"
                      style={{ color: 'var(--text-secondary)' }}
                      onMouseEnter={e => { e.currentTarget.style.background = 'var(--bg-elevated)'; }}
                      onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; }}
                    >
                      <span style={{ color: 'var(--text-muted)' }}>
                        <svg width="14" height="14" viewBox="0 0 16 16" fill="none">
                          <path d="M2 4h4l2 2h6v7a1 1 0 01-1 1H3a1 1 0 01-1-1V4z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round"/>
                        </svg>
                      </span>
                      <span className="truncate">{entry.name}</span>
                    </button>
                  ))}
                  {dirEntries.length === 0 && (
                    <p className="px-3 py-6 text-xs text-center" style={{ color: 'var(--text-muted)' }}>
                      No subdirectories
                    </p>
                  )}
                </div>
              </div>
            )}
          </div>

          {/* Agent Count */}
          <div>
            <label className="block text-sm font-medium mb-2" style={{ color: 'var(--text-secondary)' }}>
              Agent Configuration
            </label>
            <div className="grid grid-cols-3 gap-2">
              {agentOptions.map(opt => {
                const isSelected = agentsCount === opt.value;
                return (
                  <button
                    key={opt.value}
                    onClick={() => setAgentsCount(opt.value)}
                    className="p-3 rounded-xl text-left transition-all duration-200 active:scale-[0.97]"
                    style={{
                      background: isSelected ? 'var(--glow-blue)' : 'var(--bg-panel)',
                      border: isSelected ? '1px solid rgba(99,140,255,0.3)' : '1px solid var(--border-dim)',
                      boxShadow: isSelected ? '0 0 15px var(--glow-blue)' : 'none',
                    }}
                    onMouseEnter={e => { if (!isSelected) e.currentTarget.style.borderColor = 'var(--border-subtle)'; }}
                    onMouseLeave={e => { if (!isSelected) e.currentTarget.style.borderColor = 'var(--border-dim)'; }}
                  >
                    <div className="text-lg mb-1">{opt.icon}</div>
                    <div className="text-sm font-semibold" style={{ color: isSelected ? 'var(--accent-blue)' : 'var(--text-primary)' }}>
                      {opt.label}
                    </div>
                    <div className="text-[11px] mt-0.5" style={{ color: 'var(--text-muted)' }}>
                      {opt.desc}
                    </div>
                  </button>
                );
              })}
            </div>
          </div>

          {/* Description */}
          <div>
            <label className="block text-sm font-medium mb-1.5" style={{ color: 'var(--text-secondary)' }}>
              Description <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>(optional)</span>
            </label>
            <textarea
              value={description}
              onChange={e => setDescription(e.target.value)}
              placeholder="Brief description of the project..."
              rows={2}
              className="w-full text-base rounded-xl px-4 py-3 focus:outline-none transition-all duration-200 resize-none"
              style={{
                background: 'var(--bg-panel)',
                border: '1px solid var(--border-subtle)',
                color: 'var(--text-primary)',
              }}
              onFocus={e => { e.currentTarget.style.borderColor = 'var(--border-active)'; e.currentTarget.style.boxShadow = '0 0 0 3px rgba(99,140,255,0.08)'; }}
              onBlur={e => { e.currentTarget.style.borderColor = 'var(--border-subtle)'; e.currentTarget.style.boxShadow = 'none'; }}
            />
          </div>

          {/* Error */}
          {error && (
            <div className="rounded-xl px-4 py-3 text-sm flex items-center gap-2 animate-[fadeSlideIn_0.2s_ease-out]"
              style={{
                background: 'var(--glow-red)',
                border: '1px solid rgba(245,71,91,0.2)',
                color: 'var(--accent-red)',
              }}>
              <span>⚠</span>
              <span>{error}</span>
            </div>
          )}

          {/* Actions */}
          <div className="flex gap-3 pt-2">
            <button
              onClick={() => navigate('/')}
              className="px-4 py-2.5 text-sm font-medium rounded-xl transition-all duration-200 active:scale-95"
              style={{ color: 'var(--text-muted)' }}
              onMouseEnter={e => { e.currentTarget.style.color = 'var(--text-primary)'; }}
              onMouseLeave={e => { e.currentTarget.style.color = 'var(--text-muted)'; }}
            >
              Cancel
            </button>
            <button
              onClick={handleCreate}
              disabled={creating || !isValid}
              className="flex-1 px-4 py-2.5 text-sm font-semibold rounded-xl transition-all duration-200 active:scale-[0.97]"
              style={{
                background: isValid && !creating
                  ? 'linear-gradient(135deg, var(--accent-blue), #4f6ef5)'
                  : 'var(--bg-elevated)',
                color: isValid && !creating ? 'white' : 'var(--text-muted)',
                boxShadow: isValid && !creating
                  ? '0 4px 20px rgba(99,140,255,0.3), inset 0 1px 0 rgba(255,255,255,0.1)'
                  : 'none',
                border: isValid && !creating ? 'none' : '1px solid var(--border-dim)',
                cursor: isValid && !creating ? 'pointer' : 'not-allowed',
              }}
            >
              {creating ? (
                <span className="flex items-center justify-center gap-2">
                  <svg className="w-4 h-4 animate-spin" viewBox="0 0 20 20" fill="none">
                    <circle cx="10" cy="10" r="8" stroke="currentColor" strokeWidth="2" strokeDasharray="36" strokeDashoffset="10" strokeLinecap="round"/>
                  </svg>
                  Creating...
                </span>
              ) : '🚀 Create Project'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

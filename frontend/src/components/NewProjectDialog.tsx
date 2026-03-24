import { useEffect, useReducer, useRef, useCallback, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { createProject, browseDirs, getSettings } from '../api';
import type { DirEntry } from '../types';

// ============================================================================
// Form State
// ============================================================================

interface NewProjectFormState {
  name: string;
  nameManuallyTyped: boolean;
  directory: string;
  baseDir: string;
  agentsCount: number;
  description: string;
  error: string;
  creating: boolean;
  showBrowser: boolean;
  browsePath: string;
  dirEntries: DirEntry[];
  currentDir: string;
  parentDir: string | null;
  browseError: string | null;
  homeDir: string;
}

const initialFormState: NewProjectFormState = {
  name: '',
  nameManuallyTyped: false,
  directory: '',
  baseDir: '',
  agentsCount: 2,
  description: '',
  error: '',
  creating: false,
  showBrowser: false,
  browsePath: '~',
  dirEntries: [],
  currentDir: '',
  parentDir: null,
  browseError: null,
  homeDir: '',
};

type FormAction =
  | { type: 'SET_NAME'; name: string; userTyped?: boolean }
  | { type: 'SET_DIRECTORY'; directory: string }
  | { type: 'SET_BASE_DIR'; baseDir: string }
  | { type: 'SET_AGENTS_COUNT'; count: number }
  | { type: 'SET_DESCRIPTION'; description: string }
  | { type: 'SET_ERROR'; error: string }
  | { type: 'SET_CREATING'; creating: boolean }
  | { type: 'TOGGLE_BROWSER' }
  | { type: 'SET_BROWSE_PATH'; path: string }
  | { type: 'BROWSE_RESULT'; entries: DirEntry[]; currentDir: string; parentDir: string | null; error?: string | null; home?: string }
  | { type: 'BROWSE_ERROR'; error: string }
  | { type: 'SELECT_DIR'; directory: string };

function formReducer(state: NewProjectFormState, action: FormAction): NewProjectFormState {
  switch (action.type) {
    case 'SET_NAME':
      return { ...state, name: action.name, nameManuallyTyped: action.userTyped ?? state.nameManuallyTyped };
    case 'SET_DIRECTORY':
      return { ...state, directory: action.directory };
    case 'SET_BASE_DIR':
      return { ...state, baseDir: action.baseDir };
    case 'SET_AGENTS_COUNT':
      return { ...state, agentsCount: action.count };
    case 'SET_DESCRIPTION':
      return { ...state, description: action.description };
    case 'SET_ERROR':
      return { ...state, error: action.error };
    case 'SET_CREATING':
      return { ...state, creating: action.creating };
    case 'TOGGLE_BROWSER':
      return { ...state, showBrowser: !state.showBrowser };
    case 'SET_BROWSE_PATH':
      return { ...state, browsePath: action.path };
    case 'BROWSE_RESULT':
      return {
        ...state,
        dirEntries: action.entries,
        currentDir: action.currentDir,
        parentDir: action.parentDir,
        browseError: action.error || null,
        homeDir: action.home || state.homeDir,
      };
    case 'BROWSE_ERROR':
      return { ...state, dirEntries: [], browseError: action.error };
    case 'SELECT_DIR': {
      const folderName = action.directory.split('/').filter(Boolean).pop() || '';
      const newName = state.nameManuallyTyped ? state.name : folderName;
      return { ...state, directory: action.directory, name: newName, showBrowser: false };
    }
    default:
      return state;
  }
}

// ============================================================================
// Component
// ============================================================================

export default function NewProjectDialog(): React.ReactElement {
  const [state, dispatch] = useReducer(formReducer, initialFormState);
  const {
    name, directory, baseDir, agentsCount, description,
    error, creating, showBrowser, browsePath, dirEntries,
    currentDir, parentDir, browseError, homeDir,
  } = state;

  const dirManuallySetRef = useRef(false);
  const navigate = useNavigate();
  const [pathInput, setPathInput] = useState('');
  const [browseLoading, setBrowseLoading] = useState(false);

  // Load projects_base_dir from settings on mount
  useEffect(() => {
    getSettings().then(s => {
      if (s.projects_base_dir) {
        dispatch({ type: 'SET_BASE_DIR', baseDir: s.projects_base_dir });
        dispatch({ type: 'SET_BROWSE_PATH', path: s.projects_base_dir });
      }
    }).catch(() => {});
  }, []);

  // Auto-fill directory when name changes
  useEffect(() => {
    if (dirManuallySetRef.current) return;
    if (baseDir && name.trim()) {
      const slug = name.trim().toLowerCase().replace(/\s+/g, '-');
      dispatch({ type: 'SET_DIRECTORY', directory: `${baseDir}/${slug}` });
    } else if (baseDir && !name.trim()) {
      dispatch({ type: 'SET_DIRECTORY', directory: '' });
    }
  }, [name, baseDir]);

  // Fetch directory listing when browsePath changes
  useEffect(() => {
    if (showBrowser) {
      setBrowseLoading(true);
      browseDirs(browsePath).then(res => {
        dispatch({
          type: 'BROWSE_RESULT',
          entries: res.entries || [],
          currentDir: res.current,
          parentDir: res.parent,
          error: res.error,
          home: res.home,
        });
        setPathInput(res.current);
      }).catch((err) => {
        dispatch({ type: 'BROWSE_ERROR', error: err.message || 'Failed to load' });
      }).finally(() => setBrowseLoading(false));
    }
  }, [showBrowser, browsePath]);

  const handleCreate = useCallback(async (): Promise<void> => {
    dispatch({ type: 'SET_ERROR', error: '' });
    if (!name.trim()) {
      dispatch({ type: 'SET_ERROR', error: 'Project name is required' });
      return;
    }
    if (!directory.trim()) {
      dispatch({ type: 'SET_ERROR', error: 'Directory is required' });
      return;
    }
    dispatch({ type: 'SET_CREATING', creating: true });
    try {
      const result = await createProject({
        name: name.trim(),
        directory: directory.trim(),
        agents_count: agentsCount,
        description: description.trim() || undefined,
      });
      navigate(`/project/${result.project_id}`);
    } catch (e: unknown) {
      dispatch({
        type: 'SET_ERROR',
        error: e instanceof Error ? e.message : 'Failed to create project',
      });
    } finally {
      dispatch({ type: 'SET_CREATING', creating: false });
    }
  }, [name, directory, agentsCount, description, navigate]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent): void => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleCreate();
    }
  }, [handleCreate]);

  const selectDir = useCallback((path: string): void => {
    dirManuallySetRef.current = true;
    dispatch({ type: 'SELECT_DIR', directory: path });
  }, []);

  const navigateTo = useCallback((path: string): void => {
    dispatch({ type: 'SET_BROWSE_PATH', path });
  }, []);

  const agentOptions = [
    { value: 1, label: 'Solo', desc: 'Single agent', icon: '🎯' },
    { value: 2, label: 'Team', desc: 'Orchestrator + dev', icon: '👥' },
    { value: 3, label: 'Full Team', desc: '+ code reviewer', icon: '🏢' },
  ];

  // Agent roster for the swarm preview
  const allAgents: Array<{ label: string; color: string }> = [
    { label: 'PM', color: '#638cff' },
    { label: 'FE', color: '#3dd68c' },
    { label: 'BE', color: '#f5a623' },
    { label: 'DB', color: '#a78bfa' },
    { label: 'QA', color: '#f472b6' },
    { label: 'DV', color: '#22d3ee' },
    { label: 'SE', color: '#fb923c' },
    { label: 'AI', color: '#818cf8' },
    { label: 'OPS', color: '#34d399' },
    { label: 'UX', color: '#e879f9' },
    { label: 'AR', color: '#fbbf24' },
    { label: 'RV', color: '#f87171' },
  ];

  const swarmAgents = agentsCount === 1
    ? allAgents.slice(0, 1)
    : agentsCount === 2
      ? allAgents.slice(0, 5)
      : allAgents;

  const isValid = name.trim() && directory.trim();

  // Quick-access paths
  const quickPaths = [
    { label: 'Home', path: homeDir || '~' },
    { label: 'Projects', path: baseDir || '~/claude-projects' },
    { label: 'Desktop', path: homeDir ? `${homeDir}/Desktop` : '~/Desktop' },
    { label: 'Documents', path: homeDir ? `${homeDir}/Documents` : '~/Documents' },
  ];

  return (
    <div className="min-h-full flex items-start justify-center pt-8 sm:pt-12 px-4" style={{ background: 'var(--bg-void)' }}>
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
                Pick a folder from your computer
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
              onChange={e => dispatch({ type: 'SET_NAME', name: e.target.value, userTyped: e.target.value.trim().length > 0 })}
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
                onChange={e => {
                  dirManuallySetRef.current = true;
                  dispatch({ type: 'SET_DIRECTORY', directory: e.target.value });
                }}
                onKeyDown={handleKeyDown}
                placeholder="~/projects/my-project"
                className="flex-1 text-base rounded-xl px-4 py-3 focus:outline-none transition-all duration-200"
                style={{
                  background: 'var(--bg-panel)',
                  border: '1px solid var(--border-subtle)',
                  color: 'var(--text-primary)',
                  fontFamily: 'var(--font-mono)',
                  fontSize: '14px',
                }}
                onFocus={e => { e.currentTarget.style.borderColor = 'var(--border-active)'; e.currentTarget.style.boxShadow = '0 0 0 3px rgba(99,140,255,0.08)'; }}
                onBlur={e => {
                  e.currentTarget.style.borderColor = 'var(--border-subtle)';
                  e.currentTarget.style.boxShadow = 'none';
                  // Auto-fill name from last path segment when leaving the field
                  if (!state.nameManuallyTyped && directory.trim()) {
                    const folderName = directory.trim().replace(/\/+$/, '').split('/').filter(Boolean).pop() || '';
                    if (folderName && folderName !== '~') {
                      dispatch({ type: 'SET_NAME', name: folderName });
                    }
                  }
                }}
              />
              <button
                onClick={() => dispatch({ type: 'TOGGLE_BROWSER' })}
                className="px-4 py-3 text-sm font-medium rounded-xl transition-all duration-200 active:scale-95 flex-shrink-0"
                style={{
                  background: showBrowser ? 'var(--glow-blue)' : 'var(--bg-elevated)',
                  border: showBrowser ? '1px solid rgba(99,140,255,0.3)' : '1px solid var(--border-subtle)',
                  color: showBrowser ? 'var(--accent-blue)' : 'var(--text-secondary)',
                }}
              >
                📂 Browse
              </button>
            </div>

            {/* ── File Browser ── */}
            {showBrowser && (
              <div className="mt-2 rounded-xl overflow-hidden animate-[slideUp_0.2s_ease-out]"
                style={{ background: 'var(--bg-panel)', border: '1px solid var(--border-subtle)' }}>

                {/* Quick access buttons */}
                <div className="px-3 py-2 flex gap-1.5 flex-wrap"
                  style={{ borderBottom: '1px solid var(--border-dim)' }}>
                  {quickPaths.map(qp => (
                    <button
                      key={qp.label}
                      onClick={() => navigateTo(qp.path)}
                      className="px-2.5 py-1 text-[11px] font-medium rounded-lg transition-all active:scale-95"
                      style={{
                        background: currentDir === qp.path ? 'var(--glow-blue)' : 'var(--bg-elevated)',
                        color: currentDir === qp.path ? 'var(--accent-blue)' : 'var(--text-muted)',
                        border: '1px solid var(--border-dim)',
                      }}
                    >
                      {qp.label}
                    </button>
                  ))}
                </div>

                {/* Path input bar */}
                <div className="px-3 py-2 flex items-center gap-2"
                  style={{ borderBottom: '1px solid var(--border-dim)' }}>
                  {parentDir && (
                    <button onClick={() => navigateTo(parentDir)}
                      className="p-1.5 rounded-lg transition-all active:scale-90 flex-shrink-0"
                      style={{ background: 'var(--bg-elevated)', color: 'var(--text-muted)' }}
                      title="Go up">
                      <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M8 12V4M4 8l4-4 4 4"/>
                      </svg>
                    </button>
                  )}
                  <form className="flex-1 flex" onSubmit={(e) => {
                    e.preventDefault();
                    if (pathInput.trim()) navigateTo(pathInput.trim());
                  }}>
                    <input
                      type="text"
                      value={pathInput}
                      onChange={e => setPathInput(e.target.value)}
                      className="flex-1 text-xs px-2 py-1.5 rounded-lg focus:outline-none"
                      style={{
                        background: 'var(--bg-void)',
                        color: 'var(--text-secondary)',
                        fontFamily: 'var(--font-mono)',
                        border: '1px solid var(--border-dim)',
                      }}
                      onFocus={e => { e.currentTarget.style.borderColor = 'var(--border-active)'; }}
                      onBlur={e => { e.currentTarget.style.borderColor = 'var(--border-dim)'; }}
                    />
                  </form>
                  <button onClick={() => selectDir(currentDir)}
                    className="text-xs font-medium flex-shrink-0 px-3 py-1.5 rounded-lg transition-all active:scale-95"
                    style={{ background: 'var(--glow-blue)', color: 'var(--accent-blue)', border: '1px solid rgba(99,140,255,0.2)' }}>
                    Select
                  </button>
                </div>

                {/* Loading */}
                {browseLoading && (
                  <div className="px-3 py-4 text-center">
                    <div className="inline-block w-4 h-4 border-2 rounded-full animate-spin"
                      style={{ borderColor: 'var(--border-dim)', borderTopColor: 'var(--accent-blue)' }} />
                  </div>
                )}

                {/* Error message */}
                {browseError && !browseLoading && (
                  <div className="px-3 py-3 text-xs text-center" style={{ color: 'var(--accent-red, #ef4444)' }}>
                    {browseError}
                  </div>
                )}

                {/* Directory listing */}
                {!browseLoading && (
                  <div className="max-h-64 overflow-y-auto">
                    {dirEntries.map(entry => (
                      <button
                        key={entry.path}
                        onClick={() => {
                          // Single click: navigate into the folder AND auto-select it as the directory
                          dirManuallySetRef.current = true;
                          dispatch({ type: 'SET_DIRECTORY', directory: entry.path });
                          // Auto-fill name from folder (unless user manually typed one)
                          if (!state.nameManuallyTyped) {
                            dispatch({ type: 'SET_NAME', name: entry.name });
                          }
                          navigateTo(entry.path);
                        }}
                        onDoubleClick={() => selectDir(entry.path)}
                        className="w-full text-left px-3 py-2 text-sm flex items-center gap-2 transition-colors"
                        style={{ color: 'var(--text-secondary)' }}
                        onMouseEnter={e => { e.currentTarget.style.background = 'var(--bg-elevated)'; }}
                        onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; }}
                      >
                        <span style={{ color: entry.is_git ? 'var(--accent-blue)' : 'var(--text-muted)', flexShrink: 0 }}>
                          {entry.is_git ? (
                            <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
                              <path d="M15.698 7.287L8.712.302a1.03 1.03 0 00-1.457 0l-1.45 1.45 1.84 1.84a1.223 1.223 0 011.55 1.56l1.773 1.774a1.224 1.224 0 11-.733.684L8.535 5.91v4.253a1.225 1.225 0 11-1.008-.036V5.794a1.224 1.224 0 01-.664-1.608L5.093 2.415l-4.79 4.79a1.03 1.03 0 000 1.457l6.986 6.986a1.03 1.03 0 001.457 0l6.953-6.953a1.031 1.031 0 000-1.457"/>
                            </svg>
                          ) : (
                            <svg width="14" height="14" viewBox="0 0 16 16" fill="none">
                              <path d="M2 4h4l2 2h6v7a1 1 0 01-1 1H3a1 1 0 01-1-1V4z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round"/>
                            </svg>
                          )}
                        </span>
                        <span className="truncate">{entry.name}</span>
                        {entry.is_git && (
                          <span className="text-[10px] px-1.5 py-0.5 rounded ml-auto flex-shrink-0"
                            style={{ background: 'var(--glow-blue)', color: 'var(--accent-blue)' }}>
                            git
                          </span>
                        )}
                      </button>
                    ))}
                    {dirEntries.length === 0 && !browseError && (
                      <p className="px-3 py-6 text-xs text-center" style={{ color: 'var(--text-muted)' }}>
                        Empty folder
                      </p>
                    )}
                  </div>
                )}
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
                    onClick={() => dispatch({ type: 'SET_AGENTS_COUNT', count: opt.value })}
                    className="p-3 rounded-xl text-left transition-all duration-200 active:scale-[0.97]"
                    style={{
                      background: isSelected ? 'var(--glow-blue)' : 'var(--bg-panel)',
                      border: isSelected ? '1px solid rgba(99,140,255,0.3)' : '1px solid var(--border-dim)',
                      boxShadow: isSelected ? '0 0 15px var(--glow-blue)' : 'none',
                    }}
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

            {/* Swarm Preview — mini agent circle */}
            <div
              className="mt-4 flex items-center justify-center"
              style={{ minHeight: '96px' }}
            >
              <div className="relative" style={{ width: '96px', height: '96px' }}>
                {swarmAgents.map((agent, i) => {
                  const count = swarmAgents.length;
                  const angle = count === 1 ? 0 : (2 * Math.PI * i) / count - Math.PI / 2;
                  const radius = count === 1 ? 0 : 34;
                  const cx = 48 + radius * Math.cos(angle);
                  const cy = 48 + radius * Math.sin(angle);
                  const size = count > 8 ? 22 : 28;

                  return (
                    <div
                      key={`${agentsCount}-${agent.label}`}
                      className="absolute flex items-center justify-center rounded-full stagger-item"
                      style={{
                        width: `${size}px`,
                        height: `${size}px`,
                        left: `${cx - size / 2}px`,
                        top: `${cy - size / 2}px`,
                        background: agent.color,
                        boxShadow: `0 0 12px ${agent.color}66`,
                        animationDelay: `${i * 60}ms`,
                      }}
                      title={agent.label}
                    >
                      <span
                        className="font-bold select-none"
                        style={{
                          fontSize: count > 8 ? '8px' : '9px',
                          color: '#fff',
                          fontFamily: 'var(--font-mono)',
                          lineHeight: 1,
                        }}
                      >
                        {agent.label}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>

          {/* Description */}
          <div>
            <label className="block text-sm font-medium mb-1.5" style={{ color: 'var(--text-secondary)' }}>
              Description <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>(optional)</span>
            </label>
            <textarea
              value={description}
              onChange={e => dispatch({ type: 'SET_DESCRIPTION', description: e.target.value })}
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
            <div className="rounded-xl px-4 py-3 text-sm flex items-center gap-2"
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
              {creating ? 'Creating...' : 'Create Project'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

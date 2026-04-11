import { useEffect, useState, useCallback } from 'react';
import ErrorState from '../components/ErrorState';

// ── Types ──────────────────────────────────────────────────────────────────

interface Plugin {
  name: string;
  description: string;
  is_writer: boolean;
  file_scope_patterns: string[];
  enabled: boolean;
}

interface PluginListResponse {
  plugins: Plugin[];
}

// ── API helpers ────────────────────────────────────────────────────────────

async function fetchPlugins(): Promise<Plugin[]> {
  const res = await fetch('/api/plugins');
  if (!res.ok) throw new Error(`Failed to load plugins (${res.status})`);
  const data: PluginListResponse = await res.json();
  return data.plugins;
}

async function togglePlugin(name: string, enable: boolean): Promise<Plugin> {
  const action = enable ? 'enable' : 'disable';
  const res = await fetch(`/api/plugins/${encodeURIComponent(name)}/${action}`, {
    method: 'POST',
  });
  if (!res.ok) throw new Error(`Failed to ${action} plugin '${name}' (${res.status})`);
  return res.json() as Promise<Plugin>;
}

interface CreatePluginPayload {
  role_name: string;
  system_prompt: string;
  file_scope_patterns: string[];
  is_writer: boolean;
}

async function createPlugin(payload: CreatePluginPayload): Promise<Plugin> {
  const res = await fetch('/api/plugins', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail ?? `Failed to create plugin (${res.status})`);
  }
  return res.json() as Promise<Plugin>;
}

async function deletePlugin(name: string): Promise<void> {
  const res = await fetch(`/api/plugins/${encodeURIComponent(name)}`, {
    method: 'DELETE',
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail ?? `Failed to delete plugin (${res.status})`);
  }
}

// ── Sub-components ─────────────────────────────────────────────────────────

function LoadingSpinner(): JSX.Element {
  return (
    <div className="min-h-screen flex items-center justify-center" style={{ background: 'var(--bg-void)' }}>
      <div className="flex flex-col items-center gap-4">
        <div
          style={{
            width: '32px',
            height: '32px',
            border: '2px solid var(--border-subtle)',
            borderTopColor: 'var(--accent-blue)',
            borderRadius: '50%',
            animation: 'spin 0.8s linear infinite',
          }}
        />
        <p className="text-sm" style={{ color: 'var(--text-muted)' }}>Loading plugins…</p>
      </div>
      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </div>
  );
}

function StatusBadge({ enabled }: { enabled: boolean }): JSX.Element {
  return (
    <span
      className="inline-flex items-center gap-1.5 text-[11px] font-medium px-2.5 py-1 rounded-full"
      style={{
        background: enabled ? 'rgba(61,214,140,0.08)' : 'var(--bg-elevated)',
        color: enabled ? 'var(--accent-green)' : 'var(--text-muted)',
        border: `1px solid ${enabled ? 'rgba(61,214,140,0.15)' : 'var(--border-dim)'}`,
      }}
    >
      <span
        className="w-1.5 h-1.5 rounded-full"
        style={{ background: enabled ? 'var(--accent-green)' : 'var(--text-muted)' }}
        aria-hidden="true"
      />
      {enabled ? 'Enabled' : 'Disabled'}
    </span>
  );
}

interface ToggleButtonProps {
  plugin: Plugin;
  toggling: boolean;
  onToggle: (name: string, enable: boolean) => void;
}

function ToggleButton({ plugin, toggling, onToggle }: ToggleButtonProps): JSX.Element {
  const willEnable = !plugin.enabled;
  return (
    <button
      onClick={() => onToggle(plugin.name, willEnable)}
      disabled={toggling}
      className="px-3 py-1.5 text-xs font-medium rounded-lg transition-all duration-200 active:scale-95 focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-1"
      style={{
        background: willEnable
          ? 'linear-gradient(135deg, var(--accent-blue), #4f6ef5)'
          : 'var(--bg-elevated)',
        color: willEnable ? 'white' : 'var(--text-muted)',
        border: willEnable ? 'none' : '1px solid var(--border-dim)',
        boxShadow: willEnable ? '0 2px 8px var(--glow-blue)' : 'none',
        opacity: toggling ? 0.6 : 1,
        cursor: toggling ? 'not-allowed' : 'pointer',
        // ring color for focus-visible
        '--tw-ring-color': 'var(--accent-blue)',
      } as React.CSSProperties}
      aria-label={`${willEnable ? 'Enable' : 'Disable'} plugin ${plugin.name}`}
    >
      {toggling ? '…' : willEnable ? 'Enable' : 'Disable'}
    </button>
  );
}

// ── Create Plugin Form ────────────────────────────────────────────────────

interface CreateFormProps {
  onCreated: (plugin: Plugin) => void;
  onCancel: () => void;
}

function CreatePluginForm({ onCreated, onCancel }: CreateFormProps): JSX.Element {
  const [roleName, setRoleName] = useState('');
  const [systemPrompt, setSystemPrompt] = useState('');
  const [filePatterns, setFilePatterns] = useState('');
  const [isWriter, setIsWriter] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState('');

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormError('');
    const name = roleName.trim().toLowerCase().replace(/\s+/g, '_');
    if (!name) { setFormError('Role name is required.'); return; }
    if (systemPrompt.trim().length < 10) { setFormError('System prompt must be at least 10 characters.'); return; }

    setSubmitting(true);
    try {
      const patterns = filePatterns
        .split(',')
        .map(p => p.trim())
        .filter(Boolean);
      const plugin = await createPlugin({
        role_name: name,
        system_prompt: systemPrompt.trim(),
        file_scope_patterns: patterns,
        is_writer: isWriter,
      });
      onCreated(plugin);
    } catch (err) {
      setFormError(err instanceof Error ? err.message : 'Failed to create plugin.');
    } finally {
      setSubmitting(false);
    }
  };

  const inputStyle: React.CSSProperties = {
    width: '100%',
    padding: '8px 12px',
    borderRadius: 10,
    border: '1px solid var(--border-dim)',
    background: 'var(--bg-elevated)',
    color: 'var(--text-primary)',
    fontSize: 13,
    fontFamily: 'var(--font-mono)',
    outline: 'none',
  };

  return (
    <form
      onSubmit={handleSubmit}
      className="rounded-2xl p-5"
      style={{
        border: '1px solid rgba(99,140,255,0.25)',
        background: 'var(--bg-card)',
      }}
    >
      <h3 className="text-sm font-bold mb-4" style={{ color: 'var(--text-primary)' }}>
        Create Plugin
      </h3>

      <div className="flex flex-col gap-3">
        {/* Role name */}
        <div>
          <label className="block text-[11px] font-medium mb-1" style={{ color: 'var(--text-muted)' }}>
            ROLE NAME
          </label>
          <input
            style={inputStyle}
            value={roleName}
            onChange={e => setRoleName(e.target.value)}
            placeholder="e.g. api_documenter"
            autoFocus
          />
        </div>

        {/* System prompt */}
        <div>
          <label className="block text-[11px] font-medium mb-1" style={{ color: 'var(--text-muted)' }}>
            SYSTEM PROMPT
          </label>
          <textarea
            style={{ ...inputStyle, minHeight: 80, resize: 'vertical', fontFamily: 'var(--font-sans)' }}
            value={systemPrompt}
            onChange={e => setSystemPrompt(e.target.value)}
            placeholder="Describe what this agent does..."
          />
        </div>

        {/* File patterns */}
        <div>
          <label className="block text-[11px] font-medium mb-1" style={{ color: 'var(--text-muted)' }}>
            FILE PATTERNS <span style={{ fontWeight: 400 }}>(comma-separated, optional)</span>
          </label>
          <input
            style={inputStyle}
            value={filePatterns}
            onChange={e => setFilePatterns(e.target.value)}
            placeholder="e.g. **/*.py, docs/**/*.md"
          />
        </div>

        {/* Is writer toggle */}
        <label className="flex items-center gap-2 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={isWriter}
            onChange={e => setIsWriter(e.target.checked)}
            style={{ accentColor: 'var(--accent-blue)' }}
          />
          <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>
            Writer — this agent creates or modifies files
          </span>
        </label>

        {/* Error */}
        {formError && (
          <p className="text-xs" style={{ color: 'var(--accent-red)' }}>{formError}</p>
        )}

        {/* Actions */}
        <div className="flex gap-2 pt-1">
          <button
            type="submit"
            disabled={submitting}
            className="px-4 py-2 text-xs font-medium rounded-lg transition-all active:scale-95"
            style={{
              background: 'linear-gradient(135deg, var(--accent-blue), #4f6ef5)',
              color: 'white',
              border: 'none',
              opacity: submitting ? 0.6 : 1,
              cursor: submitting ? 'not-allowed' : 'pointer',
            }}
          >
            {submitting ? 'Creating…' : 'Create Plugin'}
          </button>
          <button
            type="button"
            onClick={onCancel}
            className="px-4 py-2 text-xs font-medium rounded-lg"
            style={{
              background: 'var(--bg-elevated)',
              color: 'var(--text-muted)',
              border: '1px solid var(--border-dim)',
              cursor: 'pointer',
            }}
          >
            Cancel
          </button>
        </div>
      </div>
    </form>
  );
}

// ── Main page ──────────────────────────────────────────────────────────────

export default function PluginsPage(): JSX.Element {
  const [plugins, setPlugins] = useState<Plugin[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [togglingSet, setTogglingSet] = useState<Set<string>>(new Set());
  const [showCreate, setShowCreate] = useState(false);
  const [deletingSet, setDeletingSet] = useState<Set<string>>(new Set());

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const data = await fetchPlugins();
      setPlugins(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load plugins.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const handleCreate = useCallback((plugin: Plugin) => {
    setPlugins(prev => [...prev, plugin]);
    setShowCreate(false);
  }, []);

  const handleDelete = useCallback(async (name: string) => {
    if (!confirm(`Delete plugin "${name}"? This removes the plugin file.`)) return;
    setDeletingSet(prev => new Set(prev).add(name));
    try {
      await deletePlugin(name);
      setPlugins(prev => prev.filter(p => p.name !== name));
    } catch (err) {
      setError(err instanceof Error ? err.message : `Failed to delete '${name}'.`);
    } finally {
      setDeletingSet(prev => {
        const next = new Set(prev);
        next.delete(name);
        return next;
      });
    }
  }, []);

  const handleToggle = useCallback(async (name: string, enable: boolean) => {
    setTogglingSet(prev => new Set(prev).add(name));
    try {
      const updated = await togglePlugin(name, enable);
      setPlugins(prev =>
        prev.map(p => (p.name === updated.name ? updated : p))
      );
    } catch (err) {
      // Surface error but don't break the whole page
      setError(err instanceof Error ? err.message : `Failed to toggle plugin '${name}'.`);
    } finally {
      setTogglingSet(prev => {
        const next = new Set(prev);
        next.delete(name);
        return next;
      });
    }
  }, []);

  if (loading && plugins.length === 0) {
    return <LoadingSpinner />;
  }

  if (error && plugins.length === 0) {
    return (
      <div className="min-h-screen flex items-center justify-center" style={{ background: 'var(--bg-void)' }}>
        <ErrorState variant="connection" onRetry={load} />
      </div>
    );
  }

  const enabledCount = plugins.filter(p => p.enabled).length;

  return (
    <div className="min-h-screen safe-area-top page-enter" style={{ background: 'var(--bg-void)' }}>
      {/* ── Header ───────────────────────────────────────────────────── */}
      <header className="relative overflow-hidden" style={{ borderBottom: '1px solid var(--border-dim)' }}>
        <div
          className="absolute inset-0"
          style={{
            background:
              'radial-gradient(ellipse at 60% 50%, rgba(99,140,255,0.07) 0%, transparent 55%)',
          }}
        />
        <div className="relative max-w-5xl mx-auto px-4 sm:px-6 py-6">
          <div className="flex items-center justify-between gap-4 flex-wrap">
            <div className="flex items-center gap-3">
              {/* Puzzle-piece icon */}
              <div
                className="w-10 h-10 rounded-xl flex items-center justify-center flex-shrink-0"
                style={{
                  background: 'rgba(99,140,255,0.1)',
                  boxShadow: '0 0 20px rgba(99,140,255,0.1)',
                }}
                aria-hidden="true"
              >
                <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="var(--accent-blue)" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z" />
                  <path d="M9 10H4.5A1.5 1.5 0 003 11.5v0A1.5 1.5 0 004.5 13H6v2.5A1.5 1.5 0 007.5 17h9a1.5 1.5 0 001.5-1.5V13h1.5a1.5 1.5 0 001.5-1.5v0a1.5 1.5 0 00-1.5-1.5H15V7.5A1.5 1.5 0 0013.5 6h-3A1.5 1.5 0 009 7.5V10z" />
                </svg>
              </div>
              <div>
                <h1
                  className="text-2xl font-bold"
                  style={{ color: 'var(--text-primary)', fontFamily: 'var(--font-display)' }}
                >
                  Plugins
                </h1>
                <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
                  Custom agent roles · {enabledCount} of {plugins.length} enabled
                </p>
              </div>
            </div>

            <div className="flex items-center gap-2">
              {/* Create plugin button */}
              <button
                onClick={() => setShowCreate(v => !v)}
                className="flex items-center gap-1.5 px-4 py-2 text-sm font-medium rounded-xl transition-all active:scale-95"
                style={{
                  background: showCreate ? 'var(--bg-elevated)' : 'linear-gradient(135deg, var(--accent-blue), #4f6ef5)',
                  color: showCreate ? 'var(--text-secondary)' : 'white',
                  border: showCreate ? '1px solid var(--border-dim)' : 'none',
                  boxShadow: showCreate ? 'none' : '0 2px 8px var(--glow-blue)',
                }}
                aria-label={showCreate ? 'Cancel creating plugin' : 'Create new plugin'}
              >
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
                  {showCreate ? <path d="M4 4l8 8M12 4l-8 8" /> : <path d="M8 3v10M3 8h10" />}
                </svg>
                {showCreate ? 'Cancel' : 'Create Plugin'}
              </button>

              {/* Refresh button */}
              <button
                onClick={load}
                disabled={loading}
                className="flex items-center gap-1.5 px-4 py-2 text-sm font-medium rounded-xl transition-all active:scale-95"
                style={{
                  background: 'var(--bg-elevated)',
                  color: 'var(--text-secondary)',
                  border: '1px solid var(--border-dim)',
                }}
                onMouseEnter={e => {
                  e.currentTarget.style.background = 'var(--bg-card)';
                  e.currentTarget.style.color = 'var(--text-primary)';
                }}
                onMouseLeave={e => {
                  e.currentTarget.style.background = 'var(--bg-elevated)';
                  e.currentTarget.style.color = 'var(--text-secondary)';
                }}
                aria-label="Refresh plugin list"
              >
                <svg
                  width="14"
                  height="14"
                  viewBox="0 0 16 16"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  strokeLinecap="round"
                  aria-hidden="true"
                  style={{ animation: loading ? 'spin 0.8s linear infinite' : 'none' }}
                >
                  <path d="M14 8A6 6 0 112 8" />
                  <path d="M14 3v5h-5" />
                </svg>
                {loading ? 'Refreshing…' : 'Refresh'}
              </button>
            </div>
          </div>

          {/* Inline error banner (non-fatal errors) */}
          {error && plugins.length > 0 && (
            <div
              className="mt-4 flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm"
              style={{
                background: 'rgba(239,68,68,0.08)',
                border: '1px solid rgba(239,68,68,0.15)',
                color: 'var(--accent-red)',
              }}
              role="alert"
            >
              <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" aria-hidden="true">
                <circle cx="8" cy="8" r="6" />
                <path d="M8 5v3M8 10.5v.5" />
              </svg>
              {error}
              <button
                onClick={() => setError('')}
                className="ml-auto text-xs underline"
                aria-label="Dismiss error"
              >
                Dismiss
              </button>
            </div>
          )}
        </div>
      </header>

      {/* ── Content ───────────────────────────────────────────────────── */}
      <div className="max-w-5xl mx-auto px-4 sm:px-6 py-6">
        {/* ── Create form ── */}
        {showCreate && (
          <div className="mb-6">
            <CreatePluginForm
              onCreated={handleCreate}
              onCancel={() => setShowCreate(false)}
            />
          </div>
        )}

        {plugins.length === 0 && !showCreate ? (
          // ── Empty state ───────────────────────────────────────────────
          <div className="flex flex-col items-center justify-center py-20 px-4 text-center">
            <div
              className="w-16 h-16 rounded-2xl flex items-center justify-center mb-6"
              style={{
                background: 'var(--bg-elevated)',
                border: '1px solid var(--border-dim)',
              }}
              aria-hidden="true"
            >
              <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="var(--text-muted)" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round">
                <rect x="3" y="3" width="7" height="7" rx="1.5" />
                <rect x="14" y="3" width="7" height="7" rx="1.5" />
                <rect x="3" y="14" width="7" height="7" rx="1.5" />
                <rect x="14" y="14" width="7" height="7" rx="1.5" />
              </svg>
            </div>
            <h3
              className="text-lg font-semibold mb-2"
              style={{ color: 'var(--text-primary)', fontFamily: 'var(--font-display)' }}
            >
              No Plugins Found
            </h3>
            <p className="text-sm max-w-sm" style={{ color: 'var(--text-muted)' }}>
              Drop a Python class that extends <code className="font-mono text-xs" style={{ color: 'var(--accent-blue)' }}>PluginBase</code> into the{' '}
              <code className="font-mono text-xs" style={{ color: 'var(--accent-blue)' }}>plugins/</code> directory
              and restart the server — it will appear here automatically.
            </p>
          </div>
        ) : (
          // ── Plugin table ──────────────────────────────────────────────
          <div
            className="rounded-2xl overflow-hidden"
            style={{
              border: '1px solid var(--border-dim)',
              background: 'var(--bg-card)',
            }}
          >
            {/* Table wrapper — scrollable on small screens */}
            <div className="overflow-x-auto">
              <table className="w-full text-sm" role="table" aria-label="Plugin list">
                <thead>
                  <tr style={{ borderBottom: '1px solid var(--border-dim)' }}>
                    {(['Name', 'Description', 'Type', 'File Patterns', 'Status', 'Actions'] as const).map(
                      col => (
                        <th
                          key={col}
                          scope="col"
                          className="px-5 py-3 text-left text-[11px] font-bold tracking-[0.08em] uppercase"
                          style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}
                        >
                          {col}
                        </th>
                      )
                    )}
                  </tr>
                </thead>
                <tbody>
                  {plugins.map((plugin, idx) => {
                    const isToggling = togglingSet.has(plugin.name);
                    const isLast = idx === plugins.length - 1;
                    return (
                      <tr
                        key={plugin.name}
                        style={{
                          borderBottom: isLast ? 'none' : '1px solid var(--border-dim)',
                          transition: 'background 0.15s ease',
                        }}
                        onMouseEnter={e => {
                          (e.currentTarget as HTMLTableRowElement).style.background = 'var(--bg-elevated)';
                        }}
                        onMouseLeave={e => {
                          (e.currentTarget as HTMLTableRowElement).style.background = 'transparent';
                        }}
                      >
                        {/* Name */}
                        <td className="px-5 py-4 align-top">
                          <span
                            className="font-semibold text-[13px]"
                            style={{
                              color: 'var(--text-primary)',
                              fontFamily: 'var(--font-mono)',
                            }}
                          >
                            {plugin.name}
                          </span>
                        </td>

                        {/* Description */}
                        <td className="px-5 py-4 align-top max-w-[260px]">
                          <p
                            className="text-[13px] line-clamp-2"
                            style={{ color: 'var(--text-secondary)' }}
                            title={plugin.description}
                          >
                            {plugin.description || '—'}
                          </p>
                        </td>

                        {/* Type */}
                        <td className="px-5 py-4 align-top whitespace-nowrap">
                          <span
                            className="inline-flex items-center gap-1.5 text-[11px] font-medium px-2 py-1 rounded-lg"
                            style={{
                              background: plugin.is_writer
                                ? 'rgba(245,158,11,0.08)'
                                : 'rgba(99,140,255,0.08)',
                              color: plugin.is_writer
                                ? 'var(--accent-amber)'
                                : 'var(--accent-blue)',
                              border: `1px solid ${
                                plugin.is_writer
                                  ? 'rgba(245,158,11,0.15)'
                                  : 'rgba(99,140,255,0.15)'
                              }`,
                            }}
                          >
                            {plugin.is_writer ? (
                              <>
                                <svg width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" aria-hidden="true">
                                  <path d="M11 2l3 3-9 9H2v-3L11 2z" />
                                </svg>
                                Writer
                              </>
                            ) : (
                              <>
                                <svg width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" aria-hidden="true">
                                  <circle cx="8" cy="8" r="3" />
                                  <path d="M1 8h2M13 8h2M8 1v2M8 13v2" />
                                </svg>
                                Reader
                              </>
                            )}
                          </span>
                        </td>

                        {/* File patterns */}
                        <td className="px-5 py-4 align-top max-w-[200px]">
                          {plugin.file_scope_patterns.length === 0 ? (
                            <span style={{ color: 'var(--text-muted)' }}>—</span>
                          ) : (
                            <div className="flex flex-wrap gap-1">
                              {plugin.file_scope_patterns.slice(0, 3).map(pat => (
                                <code
                                  key={pat}
                                  className="text-[10px] px-1.5 py-0.5 rounded-md"
                                  style={{
                                    background: 'var(--bg-elevated)',
                                    color: 'var(--text-secondary)',
                                    fontFamily: 'var(--font-mono)',
                                    border: '1px solid var(--border-dim)',
                                  }}
                                >
                                  {pat}
                                </code>
                              ))}
                              {plugin.file_scope_patterns.length > 3 && (
                                <span
                                  className="text-[10px] px-1.5 py-0.5 rounded-md"
                                  style={{
                                    background: 'var(--bg-elevated)',
                                    color: 'var(--text-muted)',
                                    border: '1px solid var(--border-dim)',
                                  }}
                                  title={plugin.file_scope_patterns.join(', ')}
                                >
                                  +{plugin.file_scope_patterns.length - 3} more
                                </span>
                              )}
                            </div>
                          )}
                        </td>

                        {/* Status badge */}
                        <td className="px-5 py-4 align-top whitespace-nowrap">
                          <StatusBadge enabled={plugin.enabled} />
                        </td>

                        {/* Actions */}
                        <td className="px-5 py-4 align-top whitespace-nowrap">
                          <div className="flex items-center gap-2">
                            <ToggleButton
                              plugin={plugin}
                              toggling={isToggling}
                              onToggle={handleToggle}
                            />
                            <button
                              onClick={() => handleDelete(plugin.name)}
                              disabled={deletingSet.has(plugin.name)}
                              className="p-1.5 rounded-lg transition-all hover:bg-[rgba(239,68,68,0.08)]"
                              style={{
                                color: 'var(--text-muted)',
                                cursor: deletingSet.has(plugin.name) ? 'not-allowed' : 'pointer',
                                opacity: deletingSet.has(plugin.name) ? 0.4 : 1,
                              }}
                              title={`Delete ${plugin.name}`}
                              aria-label={`Delete plugin ${plugin.name}`}
                            >
                              <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" aria-hidden="true">
                                <path d="M2 4h12M5.333 4V2.667a1.333 1.333 0 011.334-1.334h2.666a1.333 1.333 0 011.334 1.334V4M6.667 7.333v4M9.333 7.333v4"/>
                                <path d="M3.333 4h9.334l-.667 9.333a1.333 1.333 0 01-1.333 1.334H5.333A1.333 1.333 0 014 13.333L3.333 4z"/>
                              </svg>
                            </button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            {/* Footer count */}
            <div
              className="px-5 py-3 flex items-center justify-between"
              style={{ borderTop: '1px solid var(--border-dim)' }}
            >
              <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
                {plugins.length} plugin{plugins.length !== 1 ? 's' : ''} discovered
              </span>
              <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
                {enabledCount} enabled
              </span>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

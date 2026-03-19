import type { FileChanges } from '../types';
import { useState, useMemo } from 'react';

interface Props {
  files: FileChanges | null;
}

// ── Status code → icon + color ──
const STATUS_META: Record<string, { icon: string; color: string; label: string }> = {
  M:  { icon: '✏️', color: 'var(--accent-amber)', label: 'modified' },
  A:  { icon: '✅', color: 'var(--accent-green)', label: 'added' },
  D:  { icon: '🗑️', color: 'var(--accent-red)',   label: 'deleted' },
  R:  { icon: '🔄', color: 'var(--accent-blue)',  label: 'renamed' },
  '?': { icon: '❓', color: 'var(--text-muted)',   label: 'untracked' },
};

function parseStatusLines(status: string): Array<{ code: string; file: string }> {
  return status
    .split('\n')
    .filter(Boolean)
    .map(line => {
      const code = line[0] !== ' ' ? line[0] : line[1];
      const file = line.slice(3).split(' -> ').pop() ?? line.slice(3);
      return { code: code ?? '?', file: file.trim() };
    });
}

function DiffLine({ line }: { line: string }) {
  let color = 'var(--text-secondary)';
  let bg = 'transparent';
  if (line.startsWith('+') && !line.startsWith('+++')) {
    color = 'var(--accent-green)';
    bg = 'rgba(61,214,140,0.06)';
  } else if (line.startsWith('-') && !line.startsWith('---')) {
    color = 'var(--accent-red)';
    bg = 'rgba(245,71,91,0.06)';
  } else if (line.startsWith('@@')) {
    color = 'var(--accent-blue)';
  } else if (line.startsWith('diff ')) {
    color = 'var(--accent-amber)';
  }
  return (
    <span style={{ color, background: bg, display: 'block' }}>
      {line}{'\n'}
    </span>
  );
}

export default function FileDiff({ files }: Props) {
  const [expanded, setExpanded] = useState(false);

  const diffLines = useMemo(() => {
    if (!files?.diff) return [];
    return files.diff.split('\n');
  }, [files?.diff]);

  const changedFiles = useMemo(() => {
    if (!files?.status) return [];
    return parseStatusLines(files.status);
  }, [files?.status]);

  // Short path label — show only last 2 segments of project_dir
  const pathLabel = useMemo(() => {
    if (!files?.project_dir) return null;
    const parts = files.project_dir.replace(/\/$/, '').split('/');
    return parts.slice(-2).join('/');
  }, [files?.project_dir]);

  if (!files || (!files.stat && !files.status && !files.diff)) {
    return (
      <div className="flex flex-col items-center justify-center h-full px-4 py-8">
        <div className="w-14 h-14 rounded-2xl flex items-center justify-center mb-3 text-2xl"
          style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border-dim)' }}>
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="var(--text-muted)" strokeWidth="1.5" strokeLinecap="round" style={{ opacity: 0.6 }}>
            <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/>
            <polyline points="14 2 14 8 20 8"/>
            <line x1="12" y1="18" x2="12" y2="12"/>
            <line x1="9" y1="15" x2="15" y2="15"/>
          </svg>
        </div>
        <p className="text-sm font-semibold" style={{ color: 'var(--text-secondary)' }}>No file changes</p>
        <p className="text-xs mt-1 text-center" style={{ color: 'var(--text-muted)' }}>
          Changes will appear here as agents modify files
        </p>
      </div>
    );
  }

  if (files.error) {
    return (
      <div className="p-4 rounded-xl" style={{ background: 'var(--glow-red)', color: 'var(--accent-red)' }}>
        <p className="text-sm">{files.error}</p>
      </div>
    );
  }

  return (
    <div className="p-3 space-y-3">

      {/* ── Header: project path + summary ── */}
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <h3 className="text-xs font-bold uppercase tracking-wider"
          style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
          File Changes
        </h3>
        {pathLabel && (
          <span
            className="text-xs px-2 py-0.5 rounded-lg font-mono truncate max-w-[60%]"
            style={{ background: 'var(--bg-elevated)', color: 'var(--accent-blue)', border: '1px solid var(--border-dim)' }}
            title={files.project_dir}
          >
            📁 {pathLabel}
          </span>
        )}
      </div>

      {/* ── File list (parsed from git status) — MOBILE FRIENDLY ── */}
      {changedFiles.length > 0 && (
        <div className="space-y-1">
          {changedFiles.map(({ code, file }, i) => {
            const meta = STATUS_META[code] ?? STATUS_META['?'];
            const fileName = file.split('/').pop() ?? file;
            const filePath = file.includes('/') ? file.slice(0, file.lastIndexOf('/') + 1) : '';
            return (
              <div key={i}
                className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg"
                style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border-dim)' }}
              >
                <span className="text-sm shrink-0">{meta.icon}</span>
                <div className="min-w-0 flex-1">
                  <span className="text-xs font-semibold font-mono block truncate"
                    style={{ color: meta.color }}>
                    {fileName}
                  </span>
                  {filePath && (
                    <span className="text-xs font-mono truncate block"
                      style={{ color: 'var(--text-muted)' }}>
                      {filePath}
                    </span>
                  )}
                </div>
                <span className="text-xs shrink-0 opacity-60" style={{ color: meta.color }}>
                  {meta.label}
                </span>
              </div>
            );
          })}
        </div>
      )}

      {/* ── Stat summary (lines added/removed) ── */}
      {files.stat && (
        <pre className="text-xs rounded-xl p-3 whitespace-pre-wrap break-all"
          style={{
            background: 'var(--bg-card)',
            border: '1px solid var(--border-dim)',
            color: 'var(--text-secondary)',
            fontFamily: 'var(--font-mono)',
          }}>
          {files.stat}
        </pre>
      )}

      {/* ── Full diff (collapsible) ── */}
      {files.diff && (
        <div>
          <button
            onClick={() => setExpanded(!expanded)}
            className="text-xs font-medium mb-2 px-2.5 py-1.5 rounded-lg transition-all w-full text-left"
            style={{
              color: 'var(--accent-blue)',
              background: expanded ? 'var(--glow-blue)' : 'var(--bg-elevated)',
              border: '1px solid var(--border-dim)',
            }}
          >
            {expanded ? '▼ Hide' : '▶ Show'} full diff ({(files.diff.length / 1024).toFixed(1)} KB)
          </button>
          {expanded && (
            <pre className="text-xs rounded-xl p-3 whitespace-pre overflow-x-auto max-h-96 overflow-y-auto"
              style={{
                background: 'var(--bg-card)',
                border: '1px solid var(--border-dim)',
                fontFamily: 'var(--font-mono)',
              }}>
              {diffLines.map((line, i) => (
                <DiffLine key={i} line={line} />
              ))}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}

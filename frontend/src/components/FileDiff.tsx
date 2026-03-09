import type { FileChanges } from '../types';
import { useState, useMemo } from 'react';

interface Props {
  files: FileChanges | null;
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
      {line}
      {'\n'}
    </span>
  );
}

export default function FileDiff({ files }: Props) {
  const [expanded, setExpanded] = useState(false);

  const diffLines = useMemo(() => {
    if (!files?.diff) return [];
    return files.diff.split('\n');
  }, [files?.diff]);

  if (!files || (!files.stat && !files.status && !files.diff)) {
    return (
      <div className="flex flex-col items-center justify-center h-full px-4">
        <div className="w-14 h-14 rounded-2xl flex items-center justify-center mb-3 text-2xl"
          style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border-dim)' }}>
          📝
        </div>
        <p className="text-sm font-semibold" style={{ color: 'var(--text-secondary)' }}>No file changes</p>
        <p className="text-xs mt-1" style={{ color: 'var(--text-muted)' }}>
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
    <div className="p-4 space-y-3">
      <h3 className="text-xs font-bold uppercase tracking-wider"
        style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
        File Changes
      </h3>

      {files.status && (
        <pre className="text-xs rounded-xl p-3 whitespace-pre-wrap overflow-x-auto"
          style={{
            background: 'var(--bg-card)',
            border: '1px solid var(--border-dim)',
            color: 'var(--text-primary)',
            fontFamily: 'var(--font-mono)',
          }}>
          {files.status}
        </pre>
      )}

      {files.stat && (
        <pre className="text-xs rounded-xl p-3 whitespace-pre-wrap overflow-x-auto"
          style={{
            background: 'var(--bg-elevated)',
            border: '1px solid var(--border-dim)',
            color: 'var(--text-secondary)',
            fontFamily: 'var(--font-mono)',
          }}>
          {files.stat}
        </pre>
      )}

      {files.diff && (
        <div>
          <button
            onClick={() => setExpanded(!expanded)}
            className="text-xs font-medium mb-2 px-2.5 py-1 rounded-lg transition-all"
            style={{
              color: 'var(--accent-blue)',
              background: expanded ? 'var(--glow-blue)' : 'transparent',
            }}
            onMouseEnter={e => { e.currentTarget.style.background = 'var(--glow-blue)'; }}
            onMouseLeave={e => { if (!expanded) e.currentTarget.style.background = 'transparent'; }}
          >
            {expanded ? '▼ Hide' : '▶ Show'} full diff ({(files.diff.length / 1024).toFixed(1)}KB)
          </button>
          {expanded && (
            <pre className="text-xs rounded-xl p-3 whitespace-pre overflow-x-auto max-h-96 overflow-y-auto animate-[fadeSlideIn_0.2s_ease-out]"
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

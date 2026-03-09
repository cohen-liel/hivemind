import { useState, useEffect, useMemo } from 'react';
import { getFileTree, readFile } from '../api';
import type { FileTreeEntry, FileContent } from '../types';

interface Props {
  projectId: string;
}

const FILE_ICONS: Record<string, string> = {
  py: '\uD83D\uDC0D',
  ts: '\uD83D\uDFE6',
  tsx: '\u269B\uFE0F',
  js: '\uD83D\uDFE8',
  jsx: '\u269B\uFE0F',
  json: '\uD83D\uDCC4',
  md: '\uD83D\uDCDD',
  yml: '\u2699\uFE0F',
  yaml: '\u2699\uFE0F',
  html: '\uD83C\uDF10',
  css: '\uD83C\uDFA8',
  sh: '\uD83D\uDCBB',
  txt: '\uD83D\uDCC4',
  env: '\uD83D\uDD12',
};

function getIcon(name: string, type: string): string {
  if (type === 'dir') return '\uD83D\uDCC1';
  const ext = name.split('.').pop()?.toLowerCase() || '';
  return FILE_ICONS[ext] || '\uD83D\uDCC4';
}

export default function CodeBrowser({ projectId }: Props) {
  const [tree, setTree] = useState<FileTreeEntry[]>([]);
  const [expandedDirs, setExpandedDirs] = useState<Set<string>>(new Set());
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [fileContent, setFileContent] = useState<FileContent | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    getFileTree(projectId).then(setTree).catch(() => setTree([]));
  }, [projectId]);

  const toggleDir = (path: string) => {
    setExpandedDirs(prev => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  };

  const openFile = async (path: string) => {
    setSelectedFile(path);
    setLoading(true);
    try {
      const content = await readFile(projectId, path);
      setFileContent(content);
    } catch {
      setFileContent({ error: 'Failed to load file' });
    }
    setLoading(false);
  };

  // Memoize line rendering for large files
  const renderedLines = useMemo(() => {
    if (!fileContent?.content) return null;
    return fileContent.content.split('\n').map((line, i) => (
      <div key={i} className="flex transition-colors"
        style={{ borderRadius: '2px' }}
        onMouseEnter={e => { e.currentTarget.style.background = 'rgba(255,255,255,0.02)'; }}
        onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; }}
      >
        <span className="w-12 text-right pr-4 select-none flex-shrink-0"
          style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>{i + 1}</span>
        <span className="flex-1" style={{ color: 'var(--text-primary)' }}>{line || ' '}</span>
      </div>
    ));
  }, [fileContent?.content]);

  const renderEntry = (entry: FileTreeEntry, depth = 0) => {
    const isExpanded = expandedDirs.has(entry.path);
    const isSelected = selectedFile === entry.path;

    return (
      <div key={entry.path}>
        <button
          onClick={() => entry.type === 'dir' ? toggleDir(entry.path) : openFile(entry.path)}
          className="w-full text-left px-2 py-1.5 text-sm flex items-center gap-2 rounded-lg transition-colors"
          style={{
            paddingLeft: `${depth * 16 + 8}px`,
            background: isSelected ? 'var(--glow-blue)' : 'transparent',
            color: isSelected ? 'var(--accent-blue)' : 'var(--text-secondary)',
          }}
          onMouseEnter={e => { if (!isSelected) e.currentTarget.style.background = 'var(--bg-elevated)'; }}
          onMouseLeave={e => { if (!isSelected) e.currentTarget.style.background = isSelected ? 'var(--glow-blue)' : 'transparent'; }}
        >
          {entry.type === 'dir' && (
            <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
              {isExpanded ? '\u25BC' : '\u25B6'}
            </span>
          )}
          <span className="text-sm">{getIcon(entry.name, entry.type)}</span>
          <span className="truncate">{entry.name}</span>
        </button>
        {entry.type === 'dir' && isExpanded && entry.children?.map(child =>
          renderEntry(child, depth + 1)
        )}
      </div>
    );
  };

  return (
    <div className="flex flex-col lg:flex-row gap-4 h-full p-4">
      {/* File tree */}
      <div
        className={`${selectedFile ? 'hidden lg:block' : ''} lg:w-64 flex-shrink-0 rounded-xl overflow-y-auto`}
        style={{
          background: 'var(--bg-card)',
          border: '1px solid var(--border-dim)',
          maxHeight: 'calc(100vh - 200px)',
        }}
      >
        <div className="p-3" style={{ borderBottom: '1px solid var(--border-dim)' }}>
          <h3 className="text-xs font-bold uppercase tracking-wider"
            style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
            Files
          </h3>
        </div>
        <div className="p-1">
          {tree.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-8">
              <div className="text-2xl mb-2">📂</div>
              <p className="text-xs" style={{ color: 'var(--text-muted)' }}>No files found</p>
            </div>
          ) : (
            tree.map(entry => renderEntry(entry))
          )}
        </div>
      </div>

      {/* File content */}
      {selectedFile && (
        <div className="flex-1 rounded-xl overflow-hidden animate-[fadeSlideIn_0.2s_ease-out]"
          style={{ background: 'var(--bg-card)', border: '1px solid var(--border-dim)' }}>
          <div className="flex items-center justify-between px-4 py-2.5"
            style={{ borderBottom: '1px solid var(--border-dim)' }}>
            <div className="flex items-center gap-2 min-w-0">
              <button
                onClick={() => { setSelectedFile(null); setFileContent(null); }}
                className="lg:hidden p-1 rounded-lg transition-colors"
                style={{ color: 'var(--text-muted)' }}
                onMouseEnter={e => { e.currentTarget.style.color = 'var(--text-primary)'; }}
                onMouseLeave={e => { e.currentTarget.style.color = 'var(--text-muted)'; }}
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
                  <path d="M15 18l-6-6 6-6"/>
                </svg>
              </button>
              <span className="text-xs truncate"
                style={{ color: 'var(--text-secondary)', fontFamily: 'var(--font-mono)' }}>
                {selectedFile}
              </span>
            </div>
            <button
              onClick={() => { setSelectedFile(null); setFileContent(null); }}
              className="p-1 rounded-lg transition-colors"
              style={{ color: 'var(--text-muted)' }}
              onMouseEnter={e => { e.currentTarget.style.color = 'var(--text-primary)'; }}
              onMouseLeave={e => { e.currentTarget.style.color = 'var(--text-muted)'; }}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                <path d="M18 6L6 18M6 6l12 12"/>
              </svg>
            </button>
          </div>
          <div className="overflow-auto" style={{ maxHeight: 'calc(100vh - 260px)' }}>
            {loading ? (
              <div className="flex items-center gap-2 p-4">
                <svg className="w-4 h-4 animate-spin" viewBox="0 0 16 16" fill="none" style={{ color: 'var(--accent-blue)' }}>
                  <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="2" strokeDasharray="28" strokeDashoffset="8" strokeLinecap="round"/>
                </svg>
                <span className="text-xs" style={{ color: 'var(--text-muted)' }}>Loading...</span>
              </div>
            ) : fileContent?.error ? (
              <div className="p-4 text-sm" style={{ color: 'var(--accent-red)' }}>{fileContent.error}</div>
            ) : fileContent?.content !== undefined ? (
              <pre className="p-4 text-sm leading-relaxed whitespace-pre overflow-x-auto"
                style={{ fontFamily: 'var(--font-mono)' }}>
                {renderedLines}
              </pre>
            ) : null}
          </div>
        </div>
      )}

      {/* Empty state when no file selected (desktop) */}
      {!selectedFile && tree.length > 0 && (
        <div className="hidden lg:flex flex-1 items-center justify-center">
          <div className="text-center">
            <div className="text-3xl mb-3">📄</div>
            <p className="text-sm" style={{ color: 'var(--text-muted)' }}>Select a file to view</p>
          </div>
        </div>
      )}
    </div>
  );
}

import { useState, useEffect, useMemo, useCallback } from 'react';
import { getFileTree, readFile } from '../api';
import type { FileTreeEntry, FileContent } from '../types';

interface Props {
  projectId: string;
}

// ── Internal / noise file patterns to hide ──
const HIDDEN_PATTERNS = [
  /^__pycache__$/,
  /\.pyc$/,
  /^\.git$/,
  /^node_modules$/,
  /^\.DS_Store$/,
  /^screenshot-notes-.*\.txt$/,
  /^\.env$/,
  /^\.venv$/,
  /^venv$/,
  /^\.mypy_cache$/,
  /^\.pytest_cache$/,
  /^\.ruff_cache$/,
  /^dist$/,
  /^build$/,
  /^\.next$/,
  /^\.cache$/,
];

function shouldHide(name: string): boolean {
  return HIDDEN_PATTERNS.some(p => p.test(name));
}

function filterTree(entries: FileTreeEntry[]): FileTreeEntry[] {
  return entries
    .filter(e => !shouldHide(e.name))
    .map(e =>
      e.type === 'dir' && e.children
        ? { ...e, children: filterTree(e.children) }
        : e,
    )
    .filter(e => e.type !== 'dir' || (e.children && e.children.length > 0));
}

// ── File type SVG icons ──
function FileIcon({ name, type }: { name: string; type: string }) {
  if (type === 'dir') {
    return (
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--accent-amber, #f59e0b)" strokeWidth="2" strokeLinecap="round">
        <path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z"/>
      </svg>
    );
  }
  const ext = name.split('.').pop()?.toLowerCase() || '';
  const iconColors: Record<string, string> = {
    py: '#3b82f6', ts: '#3b82f6', tsx: '#06b6d4', js: '#eab308', jsx: '#06b6d4',
    json: '#a855f7', md: '#6b7280', yml: '#ef4444', yaml: '#ef4444',
    html: '#f97316', css: '#8b5cf6', sh: '#22c55e', txt: '#6b7280',
    env: '#ef4444', toml: '#ef4444', cfg: '#ef4444', ini: '#ef4444',
  };
  const color = iconColors[ext] || 'var(--text-muted)';
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="2" strokeLinecap="round">
      <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/>
      <polyline points="14 2 14 8 20 8"/>
    </svg>
  );
}

// ── Basic syntax highlighting ──
function getLanguage(filename: string): string {
  const ext = filename.split('.').pop()?.toLowerCase() || '';
  const map: Record<string, string> = {
    py: 'python', ts: 'typescript', tsx: 'typescript', js: 'javascript', jsx: 'javascript',
    json: 'json', md: 'markdown', yml: 'yaml', yaml: 'yaml',
    html: 'html', css: 'css', sh: 'shell', bash: 'shell',
    toml: 'toml', cfg: 'ini', ini: 'ini',
  };
  return map[ext] || 'text';
}

interface TokenStyle {
  color: string;
  fontWeight?: string;
  fontStyle?: string;
}

const STYLES = {
  keyword: { color: 'var(--accent-purple, #a78bfa)' },
  string: { color: 'var(--accent-green, #4ade80)' },
  comment: { color: 'var(--text-muted, #71717a)', fontStyle: 'italic' },
  number: { color: 'var(--accent-amber, #fbbf24)' },
  type: { color: 'var(--accent-cyan, #22d3ee)' },
  function: { color: 'var(--accent-blue, #60a5fa)' },
  operator: { color: 'var(--text-muted, #71717a)' },
  decorator: { color: 'var(--accent-amber, #fbbf24)' },
} as const;

interface Token {
  text: string;
  style?: TokenStyle;
}

function tokenizeLine(line: string, lang: string): Token[] {
  if (lang === 'json' || lang === 'text' || lang === 'markdown') {
    // Simple: just highlight strings and numbers in JSON
    if (lang === 'json') {
      return tokenizeJSON(line);
    }
    return [{ text: line }];
  }

  const tokens: Token[] = [];
  let remaining = line;

  // Check for full-line comment first
  const commentPrefixes = lang === 'python' ? ['#'] : ['//'];
  const trimmed = remaining.trimStart();
  for (const prefix of commentPrefixes) {
    if (trimmed.startsWith(prefix)) {
      const indent = remaining.length - trimmed.length;
      if (indent > 0) tokens.push({ text: remaining.slice(0, indent) });
      tokens.push({ text: trimmed, style: STYLES.comment });
      return tokens;
    }
  }

  // Check for decorator (Python)
  if (lang === 'python' && trimmed.startsWith('@')) {
    const indent = remaining.length - trimmed.length;
    if (indent > 0) tokens.push({ text: remaining.slice(0, indent) });
    tokens.push({ text: trimmed, style: STYLES.decorator });
    return tokens;
  }

  // Token-level highlighting
  const PY_KEYWORDS = /\b(def|class|import|from|return|if|elif|else|for|while|try|except|finally|with|as|raise|yield|async|await|pass|break|continue|and|or|not|in|is|None|True|False|self|lambda|global|nonlocal)\b/;
  const TS_KEYWORDS = /\b(function|const|let|var|return|if|else|for|while|try|catch|finally|throw|new|delete|typeof|instanceof|class|extends|implements|interface|type|enum|import|export|from|default|async|await|yield|switch|case|break|continue|true|false|null|undefined|void|this|super|static|readonly|public|private|protected|abstract|as|in|of)\b/;
  const SHELL_KEYWORDS = /\b(if|then|else|elif|fi|for|while|do|done|case|esac|function|return|exit|export|source|echo|read|set|unset|local|declare)\b/;

  const keywords = lang === 'python' ? PY_KEYWORDS : lang === 'shell' ? SHELL_KEYWORDS : TS_KEYWORDS;
  const regex = new RegExp(
    `(${keywords.source})|` +           // group 1: keywords
    `("(?:[^"\\\\]|\\\\.)*"|'(?:[^'\\\\]|\\\\.)*'|\`(?:[^\`\\\\]|\\\\.)*\`)|` + // group 2: strings
    `(\\b\\d+(?:\\.\\d+)?\\b)|` +       // group 3: numbers
    `((?:\\/\\/|#).*)`,                  // group 4: inline comment
    'g',
  );

  let lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = regex.exec(remaining)) !== null) {
    if (match.index > lastIndex) {
      tokens.push({ text: remaining.slice(lastIndex, match.index) });
    }
    if (match[1]) {
      tokens.push({ text: match[0], style: STYLES.keyword });
    } else if (match[2]) {
      tokens.push({ text: match[0], style: STYLES.string });
    } else if (match[3]) {
      tokens.push({ text: match[0], style: STYLES.number });
    } else if (match[4]) {
      tokens.push({ text: match[0], style: STYLES.comment });
    } else {
      tokens.push({ text: match[0] });
    }
    lastIndex = match.index + match[0].length;
  }
  if (lastIndex < remaining.length) {
    tokens.push({ text: remaining.slice(lastIndex) });
  }

  return tokens.length > 0 ? tokens : [{ text: line }];
}

function tokenizeJSON(line: string): Token[] {
  const tokens: Token[] = [];
  const regex = /("(?:[^"\\]|\\.)*")\s*:|("(?:[^"\\]|\\.)*")|(\b\d+(?:\.\d+)?\b)|(\b(?:true|false|null)\b)/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = regex.exec(line)) !== null) {
    if (match.index > lastIndex) {
      tokens.push({ text: line.slice(lastIndex, match.index) });
    }
    if (match[1]) {
      // Key
      tokens.push({ text: match[1], style: STYLES.type });
      tokens.push({ text: line.slice(match.index + match[1].length, match.index + match[0].length) });
    } else if (match[2]) {
      tokens.push({ text: match[0], style: STYLES.string });
    } else if (match[3]) {
      tokens.push({ text: match[0], style: STYLES.number });
    } else if (match[4]) {
      tokens.push({ text: match[0], style: STYLES.keyword });
    }
    lastIndex = match.index + match[0].length;
  }
  if (lastIndex < line.length) {
    tokens.push({ text: line.slice(lastIndex) });
  }
  return tokens.length > 0 ? tokens : [{ text: line }];
}

// ============================================================================
// Component
// ============================================================================

export default function CodeBrowser({ projectId }: Props) {
  const [tree, setTree] = useState<FileTreeEntry[]>([]);
  const [expandedDirs, setExpandedDirs] = useState<Set<string>>(new Set());
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [fileContent, setFileContent] = useState<FileContent | null>(null);
  const [loading, setLoading] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');

  useEffect(() => {
    getFileTree(projectId).then(setTree).catch(() => setTree([]));
  }, [projectId]);

  const filteredTree = useMemo(() => filterTree(tree), [tree]);

  // Filter tree by search query
  const searchedTree = useMemo(() => {
    if (!searchQuery.trim()) return filteredTree;
    const q = searchQuery.toLowerCase();
    function matchTree(entries: FileTreeEntry[]): FileTreeEntry[] {
      return entries
        .map(e => {
          if (e.type === 'dir' && e.children) {
            const matched = matchTree(e.children);
            if (matched.length > 0) return { ...e, children: matched };
          }
          if (e.name.toLowerCase().includes(q)) return e;
          return null;
        })
        .filter((e): e is FileTreeEntry => e !== null);
    }
    return matchTree(filteredTree);
  }, [filteredTree, searchQuery]);

  const toggleDir = (path: string) => {
    setExpandedDirs(prev => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  };

  const openFile = useCallback(async (path: string) => {
    setSelectedFile(path);
    setLoading(true);
    try {
      const content = await readFile(projectId, path);
      setFileContent(content);
    } catch {
      setFileContent({ error: 'Failed to load file' });
    }
    setLoading(false);
  }, [projectId]);

  // Memoize syntax-highlighted line rendering
  const renderedLines = useMemo(() => {
    if (!fileContent?.content || !selectedFile) return null;
    const lang = getLanguage(selectedFile);
    const lines = fileContent.content.split('\n');
    const lineNumWidth = String(lines.length).length;

    return lines.map((line, i) => (
      <div key={i} className="flex transition-colors"
        style={{ borderRadius: '2px' }}
        onMouseEnter={e => { e.currentTarget.style.background = 'rgba(255,255,255,0.03)'; }}
        onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; }}
      >
        <span
          className="select-none flex-shrink-0 text-right pr-4"
          style={{
            width: `${Math.max(3, lineNumWidth + 1)}ch`,
            color: 'var(--text-muted)',
            fontFamily: 'var(--font-mono)',
            opacity: 0.5,
          }}
        >
          {i + 1}
        </span>
        <span className="flex-1" style={{ color: 'var(--text-primary)' }}>
          {tokenizeLine(line, lang).map((tok, j) => (
            tok.style
              ? <span key={j} style={tok.style}>{tok.text}</span>
              : <span key={j}>{tok.text || ' '}</span>
          ))}
          {line === '' && ' '}
        </span>
      </div>
    ));
  }, [fileContent?.content, selectedFile]);

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
            <span className="text-[10px] flex-shrink-0" style={{ color: 'var(--text-muted)' }}>
              {isExpanded ? '\u25BC' : '\u25B6'}
            </span>
          )}
          <FileIcon name={entry.name} type={entry.type} />
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
          <div className="flex items-center gap-2 mb-2">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="var(--text-muted)" strokeWidth="2" strokeLinecap="round">
              <path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z"/>
            </svg>
            <h3 className="text-xs font-bold uppercase tracking-wider"
              style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
              Files
            </h3>
          </div>
          {/* Search input */}
          <div className="relative">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="var(--text-muted)"
              strokeWidth="2" strokeLinecap="round"
              style={{ position: 'absolute', left: '8px', top: '50%', transform: 'translateY(-50%)' }}>
              <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
            </svg>
            <input
              type="text"
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              placeholder="Filter files..."
              className="w-full text-xs rounded-md py-1.5 pl-7 pr-2 outline-none transition-colors"
              style={{
                background: 'var(--bg-elevated)',
                color: 'var(--text-primary)',
                border: '1px solid var(--border-dim)',
                fontFamily: 'var(--font-mono)',
              }}
              onFocus={e => { e.currentTarget.style.borderColor = 'var(--accent-blue)'; }}
              onBlur={e => { e.currentTarget.style.borderColor = 'var(--border-dim)'; }}
            />
          </div>
        </div>
        <div className="p-1">
          {searchedTree.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-8 px-4 text-center">
              <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="var(--text-muted)" strokeWidth="1.5" strokeLinecap="round" className="mb-2 opacity-50">
                <path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z"/>
              </svg>
              <p className="text-xs mb-1" style={{ color: 'var(--text-muted)' }}>
                {searchQuery ? 'No matching files' : 'No files found'}
              </p>
              {!searchQuery && (
                <p className="text-xs" style={{ color: 'var(--text-muted)', opacity: 0.6 }}>
                  Files will appear here as agents create them
                </p>
              )}
            </div>
          ) : (
            searchedTree.map(entry => renderEntry(entry))
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
              <FileIcon name={selectedFile} type="file" />
              <span className="text-xs truncate"
                style={{ color: 'var(--text-secondary)', fontFamily: 'var(--font-mono)' }}>
                {selectedFile}
              </span>
              {fileContent?.size !== undefined && (
                <span className="text-[10px] px-1.5 py-0.5 rounded"
                  style={{ background: 'var(--bg-elevated)', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
                  {fileContent.size > 1024
                    ? `${(fileContent.size / 1024).toFixed(1)} KB`
                    : `${fileContent.size} B`}
                </span>
              )}
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
      {!selectedFile && filteredTree.length > 0 && (
        <div className="hidden lg:flex flex-1 items-center justify-center">
          <div className="text-center">
            <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="var(--text-muted)" strokeWidth="1.5" strokeLinecap="round" className="mx-auto mb-3 opacity-40">
              <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/>
              <polyline points="14 2 14 8 20 8"/>
              <line x1="16" y1="13" x2="8" y2="13"/>
              <line x1="16" y1="17" x2="8" y2="17"/>
              <polyline points="10 9 9 9 8 9"/>
            </svg>
            <p className="text-sm" style={{ color: 'var(--text-muted)' }}>Select a file to view</p>
          </div>
        </div>
      )}
    </div>
  );
}

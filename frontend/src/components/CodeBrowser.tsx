import { useState, useEffect } from 'react';
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

  const renderEntry = (entry: FileTreeEntry, depth = 0) => {
    const isExpanded = expandedDirs.has(entry.path);
    const isSelected = selectedFile === entry.path;

    return (
      <div key={entry.path}>
        <button
          onClick={() => entry.type === 'dir' ? toggleDir(entry.path) : openFile(entry.path)}
          className={`w-full text-left px-2 py-1.5 text-sm flex items-center gap-2 rounded
                     hover:bg-gray-700/50 transition-colors
                     ${isSelected ? 'bg-blue-900/30 text-blue-300' : 'text-gray-300'}`}
          style={{ paddingLeft: `${depth * 16 + 8}px` }}
        >
          {entry.type === 'dir' && (
            <span className="text-xs text-gray-500">{isExpanded ? '\u25BC' : '\u25B6'}</span>
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
    <div className="flex flex-col lg:flex-row gap-4 h-full">
      {/* File tree */}
      <div className={`${selectedFile ? 'hidden lg:block' : ''} lg:w-64 flex-shrink-0 bg-gray-900 border border-gray-800 rounded-xl overflow-y-auto`}
           style={{ maxHeight: 'calc(100vh - 200px)' }}>
        <div className="p-3 border-b border-gray-800">
          <h3 className="text-sm font-semibold text-gray-400 uppercase tracking-wider">Files</h3>
        </div>
        <div className="p-1">
          {tree.length === 0 ? (
            <p className="text-sm text-gray-500 p-3">No files found</p>
          ) : (
            tree.map(entry => renderEntry(entry))
          )}
        </div>
      </div>

      {/* File content */}
      {selectedFile && (
        <div className="flex-1 bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
          <div className="flex items-center justify-between p-3 border-b border-gray-800">
            <div className="flex items-center gap-2 min-w-0">
              <button
                onClick={() => { setSelectedFile(null); setFileContent(null); }}
                className="lg:hidden text-gray-400 hover:text-white text-sm"
              >
                &larr;
              </button>
              <span className="text-sm text-gray-300 font-mono truncate">{selectedFile}</span>
            </div>
            <button
              onClick={() => { setSelectedFile(null); setFileContent(null); }}
              className="text-gray-500 hover:text-white text-sm"
            >
              &times;
            </button>
          </div>
          <div className="overflow-auto" style={{ maxHeight: 'calc(100vh - 260px)' }}>
            {loading ? (
              <div className="p-4 text-gray-500">Loading...</div>
            ) : fileContent?.error ? (
              <div className="p-4 text-red-400">{fileContent.error}</div>
            ) : fileContent?.content !== undefined ? (
              <pre className="p-4 text-sm font-mono text-gray-300 leading-relaxed whitespace-pre overflow-x-auto">
                {fileContent.content.split('\n').map((line, i) => (
                  <div key={i} className="flex hover:bg-gray-800/30">
                    <span className="w-12 text-right pr-4 text-gray-600 select-none flex-shrink-0">{i + 1}</span>
                    <span className="flex-1">{line || ' '}</span>
                  </div>
                ))}
              </pre>
            ) : null}
          </div>
        </div>
      )}
    </div>
  );
}

import type { FileChanges } from '../types';
import { useState } from 'react';

interface Props {
  files: FileChanges | null;
}

export default function FileDiff({ files }: Props) {
  const [expanded, setExpanded] = useState(false);

  if (!files || (!files.stat && !files.status && !files.diff)) {
    return (
      <div className="text-gray-500 text-sm italic">No file changes</div>
    );
  }

  if (files.error) {
    return (
      <div className="text-red-400 text-sm">{files.error}</div>
    );
  }

  return (
    <div className="space-y-2">
      <h3 className="text-sm font-semibold text-gray-400 uppercase tracking-wider">
        File Changes
      </h3>

      {files.status && (
        <pre className="text-xs text-gray-300 bg-gray-800/50 rounded-lg p-3 font-mono whitespace-pre-wrap overflow-x-auto">
          {files.status}
        </pre>
      )}

      {files.stat && (
        <pre className="text-xs text-gray-400 bg-gray-800/30 rounded-lg p-3 font-mono whitespace-pre-wrap overflow-x-auto">
          {files.stat}
        </pre>
      )}

      {files.diff && (
        <div>
          <button
            onClick={() => setExpanded(!expanded)}
            className="text-xs text-blue-400 hover:text-blue-300 mb-2"
          >
            {expanded ? 'Hide' : 'Show'} full diff ({(files.diff.length / 1024).toFixed(1)}KB)
          </button>
          {expanded && (
            <pre className="text-xs bg-gray-800/30 rounded-lg p-3 font-mono whitespace-pre overflow-x-auto max-h-96 overflow-y-auto">
              {files.diff.split('\n').map((line, i) => {
                let color = 'text-gray-400';
                if (line.startsWith('+') && !line.startsWith('+++')) color = 'text-green-400';
                else if (line.startsWith('-') && !line.startsWith('---')) color = 'text-red-400';
                else if (line.startsWith('@@')) color = 'text-blue-400';
                else if (line.startsWith('diff ')) color = 'text-yellow-400';
                return (
                  <span key={i} className={color}>
                    {line}
                    {'\n'}
                  </span>
                );
              })}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}

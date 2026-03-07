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
    { value: 1, label: 'Solo', desc: 'Single agent does everything' },
    { value: 2, label: 'Team', desc: 'Orchestrator + developer (recommended)' },
    { value: 3, label: 'Full Team', desc: 'Orchestrator + developer + reviewer' },
  ];

  return (
    <div className="min-h-full flex items-start justify-center pt-12 px-4">
      <div className="w-full max-w-lg">
        <div className="mb-8">
          <h1 className="text-2xl font-bold text-white mb-2">New Project</h1>
          <p className="text-gray-400 text-sm">Create a new project to start working with Claude agents.</p>
        </div>

        <div className="space-y-5">
          {/* Name */}
          <div>
            <label className="block text-sm font-medium text-gray-300 mb-1.5">Project Name</label>
            <input
              type="text"
              value={name}
              onChange={e => setName(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="my-awesome-project"
              className="w-full bg-gray-800 border border-gray-700 text-gray-200 text-base rounded-lg px-3 py-2.5
                         focus:border-blue-500 focus:ring-1 focus:ring-blue-500 focus:outline-none
                         placeholder-gray-500"
              autoFocus
            />
          </div>

          {/* Directory */}
          <div>
            <label className="block text-sm font-medium text-gray-300 mb-1.5">Working Directory</label>
            <div className="flex gap-2">
              <input
                type="text"
                value={directory}
                onChange={e => setDirectory(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="~/projects/my-project"
                className="flex-1 bg-gray-800 border border-gray-700 text-gray-200 text-base rounded-lg px-3 py-2.5
                           focus:border-blue-500 focus:ring-1 focus:ring-blue-500 focus:outline-none
                           placeholder-gray-500 font-mono"
              />
              <button
                onClick={() => setShowBrowser(!showBrowser)}
                className="px-3 py-2 bg-gray-700 hover:bg-gray-600 text-gray-300 text-sm rounded-lg transition-colors flex-shrink-0"
              >
                Browse
              </button>
            </div>

            {/* Directory browser */}
            {showBrowser && (
              <div className="mt-2 bg-gray-800 border border-gray-700 rounded-lg overflow-hidden">
                <div className="px-3 py-2 border-b border-gray-700 flex items-center gap-2">
                  {parentDir && (
                    <button
                      onClick={() => setBrowsePath(parentDir)}
                      className="text-gray-400 hover:text-white text-sm"
                    >
                      &larr;
                    </button>
                  )}
                  <span className="text-xs text-gray-400 font-mono truncate">{currentDir}</span>
                  <button
                    onClick={() => selectDir(currentDir)}
                    className="ml-auto text-xs text-blue-400 hover:text-blue-300 flex-shrink-0"
                  >
                    Select this
                  </button>
                </div>
                <div className="max-h-48 overflow-y-auto">
                  {dirEntries.map(entry => (
                    <button
                      key={entry.path}
                      onClick={() => entry.is_dir ? setBrowsePath(entry.path) : undefined}
                      onDoubleClick={() => selectDir(entry.path)}
                      className="w-full text-left px-3 py-1.5 text-sm text-gray-300 hover:bg-gray-700/50 flex items-center gap-2"
                    >
                      <span className="text-gray-500">
                        <svg width="14" height="14" viewBox="0 0 16 16" fill="none">
                          <path d="M2 4h4l2 2h6v7a1 1 0 01-1 1H3a1 1 0 01-1-1V4z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round"/>
                        </svg>
                      </span>
                      <span className="truncate">{entry.name}</span>
                    </button>
                  ))}
                  {dirEntries.length === 0 && (
                    <p className="px-3 py-4 text-xs text-gray-500 text-center">No subdirectories</p>
                  )}
                </div>
              </div>
            )}
          </div>

          {/* Agent Count */}
          <div>
            <label className="block text-sm font-medium text-gray-300 mb-2">Agent Configuration</label>
            <div className="grid grid-cols-3 gap-2">
              {agentOptions.map(opt => (
                <button
                  key={opt.value}
                  onClick={() => setAgentsCount(opt.value)}
                  className={`p-3 rounded-lg border text-left transition-all
                    ${agentsCount === opt.value
                      ? 'border-blue-500 bg-blue-500/10 text-white'
                      : 'border-gray-700 bg-gray-800 text-gray-400 hover:border-gray-600'}`}
                >
                  <div className="text-sm font-medium">{opt.label}</div>
                  <div className="text-xs mt-0.5 text-gray-500">{opt.desc}</div>
                </button>
              ))}
            </div>
          </div>

          {/* Description */}
          <div>
            <label className="block text-sm font-medium text-gray-300 mb-1.5">
              Description <span className="text-gray-600 font-normal">(optional)</span>
            </label>
            <textarea
              value={description}
              onChange={e => setDescription(e.target.value)}
              placeholder="Brief description of the project..."
              rows={2}
              className="w-full bg-gray-800 border border-gray-700 text-gray-200 text-base rounded-lg px-3 py-2.5
                         focus:border-blue-500 focus:ring-1 focus:ring-blue-500 focus:outline-none
                         placeholder-gray-500 resize-none"
            />
          </div>

          {/* Error */}
          {error && (
            <div className="bg-red-500/10 border border-red-500/30 rounded-lg px-3 py-2 text-sm text-red-400">
              {error}
            </div>
          )}

          {/* Actions */}
          <div className="flex gap-3 pt-2">
            <button
              onClick={() => navigate('/')}
              className="px-4 py-2.5 text-sm text-gray-400 hover:text-white transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={handleCreate}
              disabled={creating || !name.trim() || !directory.trim()}
              className="flex-1 px-4 py-2.5 bg-blue-600 hover:bg-blue-500 disabled:bg-gray-700 disabled:text-gray-500
                         text-white text-sm font-medium rounded-lg transition-colors"
            >
              {creating ? 'Creating...' : 'Create Project'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

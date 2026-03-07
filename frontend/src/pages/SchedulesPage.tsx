import { useEffect, useState } from 'react';
import { getSchedules, createSchedule, deleteSchedule, getProjects } from '../api';
import type { Schedule, Project } from '../types';

export default function SchedulesPage() {
  const [schedules, setSchedules] = useState<Schedule[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  // New schedule form
  const [showForm, setShowForm] = useState(false);
  const [formProject, setFormProject] = useState('');
  const [formTime, setFormTime] = useState('09:00');
  const [formTask, setFormTask] = useState('');
  const [formRepeat, setFormRepeat] = useState('daily');
  const [creating, setCreating] = useState(false);

  const load = async () => {
    try {
      const [s, p] = await Promise.all([getSchedules(), getProjects()]);
      setSchedules(s);
      setProjects(p);
      if (!formProject && p.length > 0) setFormProject(p[0].project_id);
    } catch {
      setError('Could not load schedules.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const handleCreate = async () => {
    if (!formProject || !formTask.trim()) return;
    setCreating(true);
    try {
      await createSchedule({
        project_id: formProject,
        schedule_time: formTime,
        task_description: formTask.trim(),
        repeat: formRepeat,
      });
      setFormTask('');
      setShowForm(false);
      await load();
    } catch {
      setError('Failed to create schedule.');
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (id: number) => {
    try {
      await deleteSchedule(id);
      setSchedules(prev => prev.filter(s => s.id !== id));
    } catch {
      setError('Failed to delete schedule.');
    }
  };

  const projectName = (pid: string) =>
    projects.find(p => p.project_id === pid)?.project_name || pid;

  if (error) {
    return (
      <div className="p-8">
        <h1 className="text-2xl font-bold text-white mb-4">Schedules</h1>
        <div className="bg-red-500/10 border border-red-500/30 rounded-lg px-4 py-3 text-sm text-red-400">
          {error}
          <button onClick={() => { setError(''); load(); }} className="ml-3 underline">Retry</button>
        </div>
      </div>
    );
  }

  return (
    <div className="p-8 max-w-3xl">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-white mb-1">Schedules</h1>
          <p className="text-gray-400 text-sm">Automated tasks that run at specified times.</p>
        </div>
        <button
          onClick={() => setShowForm(!showForm)}
          className="flex items-center gap-1.5 px-4 py-2 text-sm font-medium rounded-lg
                     bg-blue-600 hover:bg-blue-500 text-white transition-colors"
        >
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none">
            <path d="M8 3v10M3 8h10" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/>
          </svg>
          New Schedule
        </button>
      </div>

      {/* Create form */}
      {showForm && (
        <div className="bg-gray-900 border border-gray-800 rounded-xl p-4 mb-6 space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-gray-500 mb-1">Project</label>
              <select
                value={formProject}
                onChange={(e) => setFormProject(e.target.value)}
                className="w-full bg-gray-800 border border-gray-700/50 text-white text-sm
                           px-2.5 py-1.5 rounded-lg focus:border-blue-500 focus:outline-none"
              >
                {projects.map(p => (
                  <option key={p.project_id} value={p.project_id}>{p.project_name}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">Time (HH:MM)</label>
              <input
                type="time"
                value={formTime}
                onChange={(e) => setFormTime(e.target.value)}
                className="w-full bg-gray-800 border border-gray-700/50 text-white text-sm
                           px-2.5 py-1.5 rounded-lg focus:border-blue-500 focus:outline-none"
              />
            </div>
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">Task description</label>
            <input
              type="text"
              value={formTask}
              onChange={(e) => setFormTask(e.target.value)}
              placeholder="e.g. Run all tests and report failures"
              className="w-full bg-gray-800 border border-gray-700/50 text-white text-sm
                         px-2.5 py-1.5 rounded-lg focus:border-blue-500 focus:outline-none"
            />
          </div>
          <div className="flex items-center gap-3">
            <div>
              <label className="block text-xs text-gray-500 mb-1">Repeat</label>
              <select
                value={formRepeat}
                onChange={(e) => setFormRepeat(e.target.value)}
                className="bg-gray-800 border border-gray-700/50 text-white text-sm
                           px-2.5 py-1.5 rounded-lg focus:border-blue-500 focus:outline-none"
              >
                <option value="once">Once</option>
                <option value="daily">Daily</option>
                <option value="hourly">Hourly</option>
              </select>
            </div>
            <div className="ml-auto flex gap-2 self-end">
              <button
                onClick={() => setShowForm(false)}
                className="px-3 py-1.5 text-sm text-gray-400 hover:text-white transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleCreate}
                disabled={!formTask.trim() || creating}
                className={`px-4 py-1.5 text-sm font-medium rounded-lg transition-colors
                  ${formTask.trim() && !creating
                    ? 'bg-blue-600 hover:bg-blue-500 text-white'
                    : 'bg-gray-800 text-gray-600 cursor-not-allowed'}`}
              >
                {creating ? 'Creating...' : 'Create'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Schedules list */}
      {loading ? (
        <div className="text-gray-500 animate-pulse">Loading...</div>
      ) : schedules.length === 0 ? (
        <div className="text-center py-16">
          <svg width="48" height="48" viewBox="0 0 24 24" fill="none" className="mx-auto mb-3 text-gray-700">
            <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="1.5"/>
            <path d="M12 7v5l3 3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
          </svg>
          <p className="text-gray-500 text-sm">No schedules yet</p>
          <p className="text-gray-600 text-xs mt-1">Create one to automate recurring tasks.</p>
        </div>
      ) : (
        <div className="space-y-2">
          {schedules.map(s => (
            <div
              key={s.id}
              className="bg-gray-900 border border-gray-800 rounded-xl px-4 py-3 flex items-center gap-4"
            >
              {/* Time badge */}
              <div className="flex-shrink-0 bg-gray-800 rounded-lg px-3 py-1.5 text-center min-w-[64px]">
                <div className="text-sm font-mono text-white">{s.schedule_time}</div>
                <div className="text-[10px] text-gray-500 capitalize">{s.repeat}</div>
              </div>

              {/* Details */}
              <div className="min-w-0 flex-1">
                <div className="text-sm text-gray-200 truncate">{s.task_description}</div>
                <div className="text-xs text-gray-500 mt-0.5">
                  {projectName(s.project_id)}
                  {s.last_run && (
                    <span className="ml-2">
                      Last run: {new Date(s.last_run * 1000).toLocaleString()}
                    </span>
                  )}
                </div>
              </div>

              {/* Status */}
              <span className={`flex-shrink-0 text-xs px-2 py-0.5 rounded-full
                ${s.enabled
                  ? 'bg-green-500/10 text-green-400 border border-green-500/20'
                  : 'bg-gray-800 text-gray-500 border border-gray-700'}`}
              >
                {s.enabled ? 'Active' : 'Disabled'}
              </span>

              {/* Delete */}
              <button
                onClick={() => handleDelete(s.id)}
                className="flex-shrink-0 text-gray-600 hover:text-red-400 transition-colors p-1"
                title="Delete schedule"
              >
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none">
                  <path d="M3 4h10M5.5 4V3a1 1 0 011-1h3a1 1 0 011 1v1M6 7v4M10 7v4M4 4l.8 8.5a1 1 0 001 .9h4.4a1 1 0 001-.9L12 4" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"/>
                </svg>
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

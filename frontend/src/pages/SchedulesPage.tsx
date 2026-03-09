import { useEffect, useState } from 'react';
import { getSchedules, createSchedule, deleteSchedule, getProjects } from '../api';
import { SchedulesSkeleton } from '../components/Skeleton';
import ErrorState from '../components/ErrorState';
import { useToast } from '../components/Toast';
import type { Schedule, Project } from '../types';

export default function SchedulesPage() {
  const [schedules, setSchedules] = useState<Schedule[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const toast = useToast();

  // New schedule form
  const [showForm, setShowForm] = useState(false);
  const [formProject, setFormProject] = useState('');
  const [formTime, setFormTime] = useState('09:00');
  const [formTask, setFormTask] = useState('');
  const [formRepeat, setFormRepeat] = useState('daily');
  const [creating, setCreating] = useState(false);

  const load = async () => {
    setLoading(true);
    setError('');
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
      toast.success('Schedule created');
      await load();
    } catch {
      toast.error('Failed to create schedule');
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (id: number) => {
    try {
      await deleteSchedule(id);
      setSchedules(prev => prev.filter(s => s.id !== id));
      toast.success('Schedule deleted');
    } catch {
      toast.error('Failed to delete schedule');
    }
  };

  const projectName = (pid: string) =>
    projects.find(p => p.project_id === pid)?.project_name || pid;

  if (loading && schedules.length === 0) {
    return <SchedulesSkeleton />;
  }

  if (error && schedules.length === 0) {
    return (
      <div className="min-h-screen flex items-center justify-center" style={{ background: 'var(--bg-void)' }}>
        <ErrorState variant="connection" onRetry={load} />
      </div>
    );
  }

  return (
    <div className="min-h-screen safe-area-top page-enter" style={{ background: 'var(--bg-void)' }}>
      {/* Header */}
      <header className="relative overflow-hidden" style={{ borderBottom: '1px solid var(--border-dim)' }}>
        <div className="absolute inset-0" style={{
          background: 'radial-gradient(ellipse at 60% 50%, rgba(245,166,35,0.06) 0%, transparent 50%)',
        }} />
        <div className="relative max-w-3xl mx-auto px-4 sm:px-6 py-6">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-xl flex items-center justify-center text-xl"
                style={{ background: 'rgba(245,166,35,0.1)', boxShadow: '0 0 20px rgba(245,166,35,0.1)' }}>
                🕐
              </div>
              <div>
                <h1 className="text-2xl font-bold" style={{ color: 'var(--text-primary)', fontFamily: 'var(--font-display)' }}>
                  Schedules
                </h1>
                <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
                  Automated tasks that run at specified times
                </p>
              </div>
            </div>
            <button
              onClick={() => setShowForm(!showForm)}
              className="flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium rounded-xl transition-all active:scale-95 text-white"
              style={{
                background: showForm ? 'var(--bg-elevated)' : 'linear-gradient(135deg, var(--accent-blue), #4f6ef5)',
                color: showForm ? 'var(--text-muted)' : 'white',
                boxShadow: showForm ? 'none' : '0 4px 20px var(--glow-blue)',
                border: showForm ? '1px solid var(--border-subtle)' : 'none',
              }}
            >
              {showForm ? 'Cancel' : '+ New Schedule'}
            </button>
          </div>
        </div>
      </header>

      <div className="max-w-3xl mx-auto px-4 sm:px-6 py-6">
        {/* Create form */}
        {showForm && (
          <div
            className="rounded-2xl p-5 mb-6 space-y-4"
            style={{
              background: 'var(--bg-card)',
              border: '1px solid var(--border-active)',
              boxShadow: '0 0 30px var(--glow-blue)',
              animation: 'slideUp 0.3s ease-out',
            }}
          >
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div>
                <label htmlFor="schedule-project" className="block text-xs mb-1.5" style={{ color: 'var(--text-muted)' }}>Project</label>
                <select
                  id="schedule-project"
                  value={formProject}
                  onChange={(e) => setFormProject(e.target.value)}
                  className="w-full text-sm px-3 py-2 rounded-xl focus:outline-none"
                  style={{
                    background: 'var(--bg-elevated)',
                    border: '1px solid var(--border-subtle)',
                    color: 'var(--text-primary)',
                  }}
                >
                  {projects.map(p => (
                    <option key={p.project_id} value={p.project_id}>{p.project_name}</option>
                  ))}
                </select>
              </div>
              <div>
                <label htmlFor="schedule-time" className="block text-xs mb-1.5" style={{ color: 'var(--text-muted)' }}>Time</label>
                <input
                  id="schedule-time"
                  type="time"
                  value={formTime}
                  onChange={(e) => setFormTime(e.target.value)}
                  className="w-full text-sm px-3 py-2 rounded-xl focus:outline-none"
                  style={{
                    background: 'var(--bg-elevated)',
                    border: '1px solid var(--border-subtle)',
                    color: 'var(--text-primary)',
                    fontFamily: 'var(--font-mono)',
                  }}
                />
              </div>
            </div>
            <div>
              <label htmlFor="schedule-task" className="block text-xs mb-1.5" style={{ color: 'var(--text-muted)' }}>Task Description</label>
              <input
                id="schedule-task"
                type="text"
                value={formTask}
                onChange={(e) => setFormTask(e.target.value)}
                placeholder="e.g. Run all tests and report failures"
                className="w-full text-sm px-3 py-2.5 rounded-xl focus:outline-none"
                style={{
                  background: 'var(--bg-elevated)',
                  border: '1px solid var(--border-subtle)',
                  color: 'var(--text-primary)',
                }}
              />
            </div>
            <div className="flex items-end gap-4">
              <div>
                <label htmlFor="schedule-repeat" className="block text-xs mb-1.5" style={{ color: 'var(--text-muted)' }}>Repeat</label>
                <select
                  id="schedule-repeat"
                  value={formRepeat}
                  onChange={(e) => setFormRepeat(e.target.value)}
                  className="text-sm px-3 py-2 rounded-xl focus:outline-none"
                  style={{
                    background: 'var(--bg-elevated)',
                    border: '1px solid var(--border-subtle)',
                    color: 'var(--text-primary)',
                  }}
                >
                  <option value="once">Once</option>
                  <option value="daily">Daily</option>
                  <option value="hourly">Hourly</option>
                </select>
              </div>
              <button
                onClick={handleCreate}
                disabled={!formTask.trim() || creating}
                className="ml-auto px-5 py-2 text-sm font-medium rounded-xl transition-all active:scale-95"
                style={{
                  background: formTask.trim() && !creating ? 'var(--accent-blue)' : 'var(--bg-elevated)',
                  color: formTask.trim() && !creating ? 'white' : 'var(--text-muted)',
                  boxShadow: formTask.trim() && !creating ? '0 2px 12px var(--glow-blue)' : 'none',
                }}
              >
                {creating ? 'Creating...' : 'Create Schedule'}
              </button>
            </div>
          </div>
        )}

        {/* Schedules list */}
        {schedules.length === 0 ? (
          <ErrorState
            variant="empty"
            icon="🕐"
            title="No Schedules Yet"
            message="Create a schedule to automate recurring tasks for your projects."
          />
        ) : (
          <div className="space-y-3">
            {schedules.map((s, i) => (
              <div
                key={s.id}
                className="rounded-2xl px-5 py-4 flex items-center gap-4 card-hover"
                style={{
                  background: 'var(--bg-card)',
                  border: `1px solid ${s.enabled ? 'rgba(61,214,140,0.12)' : 'var(--border-dim)'}`,
                  animation: `slideUp 0.3s ease-out ${i * 50}ms backwards`,
                }}
              >
                {/* Time badge */}
                <div className="flex-shrink-0 rounded-xl px-3 py-2 text-center min-w-[68px]"
                  style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border-dim)' }}>
                  <div className="text-sm font-medium" style={{ color: 'var(--text-primary)', fontFamily: 'var(--font-mono)' }}>
                    {s.schedule_time}
                  </div>
                  <div className="text-[10px] capitalize" style={{ color: 'var(--text-muted)' }}>{s.repeat}</div>
                </div>

                {/* Details */}
                <div className="min-w-0 flex-1">
                  <div className="text-sm truncate" style={{ color: 'var(--text-primary)' }}>{s.task_description}</div>
                  <div className="text-xs mt-0.5" style={{ color: 'var(--text-muted)' }}>
                    {projectName(s.project_id)}
                    {s.last_run && (
                      <span className="ml-2">
                        Last: {new Date(s.last_run * 1000).toLocaleString()}
                      </span>
                    )}
                  </div>
                </div>

                {/* Status */}
                <span
                  className="flex-shrink-0 text-[11px] font-medium px-2.5 py-1 rounded-full"
                  style={{
                    background: s.enabled ? 'rgba(61,214,140,0.08)' : 'var(--bg-elevated)',
                    color: s.enabled ? 'var(--accent-green)' : 'var(--text-muted)',
                    border: `1px solid ${s.enabled ? 'rgba(61,214,140,0.15)' : 'var(--border-dim)'}`,
                  }}
                >
                  {s.enabled ? 'Active' : 'Disabled'}
                </span>

                {/* Delete */}
                <button
                  onClick={() => handleDelete(s.id)}
                  className="flex-shrink-0 p-2 rounded-lg transition-all"
                  style={{ color: 'var(--text-muted)' }}
                  aria-label={`Delete schedule: ${s.task_description}`}
                  onMouseEnter={e => {
                    e.currentTarget.style.color = 'var(--accent-red)';
                    e.currentTarget.style.background = 'var(--glow-red)';
                  }}
                  onMouseLeave={e => {
                    e.currentTarget.style.color = 'var(--text-muted)';
                    e.currentTarget.style.background = 'transparent';
                  }}
                  title="Delete schedule"
                >
                  <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                    <path d="M3 4h10M5.5 4V3a1 1 0 011-1h3a1 1 0 011 1v1M6 7v4M10 7v4M4 4l.8 8.5a1 1 0 001 .9h4.4a1 1 0 001-.9L12 4" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"/>
                  </svg>
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

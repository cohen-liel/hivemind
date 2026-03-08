import type { Project, ProjectMessage, FileChanges, TaskHistoryItem, Stats, FileTreeEntry, FileContent, Settings, BrowseDirsResponse, Schedule } from './types';

const BASE = '/api';

async function fetchJSON<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${url}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || `HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export async function getProjects(): Promise<Project[]> {
  const data = await fetchJSON<{ projects: Project[] }>('/projects');
  return data.projects;
}

export async function getProject(id: string): Promise<Project> {
  return fetchJSON<Project>(`/projects/${id}`);
}

export async function createProject(data: {
  name: string;
  directory: string;
  agents_count: number;
  description?: string;
}): Promise<{ ok: boolean; project_id: string }> {
  return fetchJSON('/projects', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function deleteProject(id: string): Promise<void> {
  await fetchJSON(`/projects/${id}`, { method: 'DELETE' });
}

export async function startProject(id: string): Promise<void> {
  await fetchJSON(`/projects/${id}/start`, { method: 'POST' });
}

export async function getMessages(id: string, limit = 50, offset = 0): Promise<{ messages: ProjectMessage[]; total: number }> {
  return fetchJSON(`/projects/${id}/messages?limit=${limit}&offset=${offset}`);
}

export async function getFiles(id: string): Promise<FileChanges> {
  return fetchJSON<FileChanges>(`/projects/${id}/files`);
}

export async function getTasks(id: string): Promise<TaskHistoryItem[]> {
  const data = await fetchJSON<{ tasks: TaskHistoryItem[] }>(`/projects/${id}/tasks`);
  return data.tasks;
}

export async function sendMessage(id: string, message: string): Promise<void> {
  await fetchJSON(`/projects/${id}/message`, {
    method: 'POST',
    body: JSON.stringify({ message }),
  });
}

export async function talkToAgent(id: string, agent: string, message: string): Promise<void> {
  await fetchJSON(`/projects/${id}/talk/${agent}`, {
    method: 'POST',
    body: JSON.stringify({ message }),
  });
}

export async function pauseProject(id: string): Promise<void> {
  await fetchJSON(`/projects/${id}/pause`, { method: 'POST' });
}

export async function resumeProject(id: string): Promise<void> {
  await fetchJSON(`/projects/${id}/resume`, { method: 'POST' });
}

export async function stopProject(id: string): Promise<void> {
  await fetchJSON(`/projects/${id}/stop`, { method: 'POST' });
}

export async function clearHistory(id: string): Promise<void> {
  await fetchJSON(`/projects/${id}/clear-history`, { method: 'POST' });
}

export async function getStats(): Promise<Stats> {
  return fetchJSON<Stats>('/stats');
}

export async function getSettings(): Promise<Settings> {
  return fetchJSON<Settings>('/settings');
}

export async function updateSettings(settings: Record<string, number>): Promise<void> {
  await fetchJSON('/settings', {
    method: 'PUT',
    body: JSON.stringify(settings),
  });
}

export async function persistSettings(settings: Record<string, number>): Promise<void> {
  await fetchJSON('/settings/persist', {
    method: 'POST',
    body: JSON.stringify(settings),
  });
}

export async function browseDirs(path: string = '~'): Promise<BrowseDirsResponse> {
  return fetchJSON<BrowseDirsResponse>(`/browse-dirs?path=${encodeURIComponent(path)}`);
}

export async function getFileTree(id: string): Promise<FileTreeEntry[]> {
  const data = await fetchJSON<{ tree: FileTreeEntry[] }>(`/projects/${id}/tree`);
  return data.tree;
}

export async function readFile(id: string, path: string): Promise<FileContent> {
  return fetchJSON<FileContent>(`/projects/${id}/file?path=${encodeURIComponent(path)}`);
}

export interface LiveState {
  status: string;
  agent_states: Record<string, {
    state?: string;
    task?: string;
    current_tool?: string;
    cost?: number;
    turns?: number;
    duration?: number;
  }>;
  current_agent?: string;
  current_tool?: string;
  loop_progress?: {
    loop: number;
    turn: number;
    max_turns: number;
    cost: number;
    max_budget: number;
    max_loops: number;
  } | null;
  shared_context_count: number;
  shared_context_preview: string[];
  pending_messages: number;
  pending_approval?: string | null;
  background_tasks: number;
  turn_count: number;
  total_cost_usd: number;
}

export async function getLiveState(id: string): Promise<LiveState> {
  return fetchJSON<LiveState>(`/projects/${id}/live`);
}

export async function getSchedules(): Promise<Schedule[]> {
  const data = await fetchJSON<{ schedules: Schedule[] }>('/schedules');
  return data.schedules;
}

export async function createSchedule(data: {
  project_id: string;
  schedule_time: string;
  task_description: string;
  repeat?: string;
}): Promise<{ ok: boolean; schedule_id: number }> {
  return fetchJSON('/schedules', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function deleteSchedule(id: number): Promise<void> {
  await fetchJSON(`/schedules/${id}`, { method: 'DELETE' });
}

// --- Agent Performance & Cost Analytics ---

export interface AgentStats {
  agent_role: string;
  total_runs: number;
  success_rate: number;
  avg_duration: number;
  avg_cost: number;
  total_cost: number;
  last_run: number;
}

export interface CostBreakdown {
  by_agent: { agent_role: string; cost: number; runs: number }[];
  by_day: { day: string; cost: number; runs: number }[];
  total_cost: number;
  total_runs: number;
}

export interface ResumableTask {
  resumable: boolean;
  task?: {
    last_message: string;
    current_loop: number;
    turn_count: number;
    total_cost_usd: number;
    status: string;
  };
}

export async function getAgentStats(projectId?: string): Promise<AgentStats[]> {
  const url = projectId ? `/agent-stats?project_id=${projectId}` : '/agent-stats';
  const data = await fetchJSON<{ stats: AgentStats[] }>(url);
  return data.stats;
}

export async function getAgentRecentPerformance(agentRole: string, limit = 10): Promise<unknown[]> {
  const data = await fetchJSON<{ entries: unknown[] }>(`/agent-stats/${agentRole}/recent?limit=${limit}`);
  return data.entries;
}

export async function getCostBreakdown(projectId?: string, days = 30): Promise<CostBreakdown> {
  const params = new URLSearchParams();
  if (projectId) params.set('project_id', projectId);
  params.set('days', String(days));
  return fetchJSON<CostBreakdown>(`/cost-breakdown?${params}`);
}

export async function getCostSummary(): Promise<{ projects: unknown[] }> {
  return fetchJSON('/cost-summary');
}

export async function getResumableTask(projectId: string): Promise<ResumableTask> {
  return fetchJSON<ResumableTask>(`/projects/${projectId}/resumable`);
}

export async function resumeInterruptedTask(projectId: string): Promise<{ ok: boolean; message: string }> {
  return fetchJSON(`/projects/${projectId}/resume-interrupted`, { method: 'POST' });
}

export async function discardInterruptedTask(projectId: string): Promise<void> {
  await fetchJSON(`/projects/${projectId}/discard-interrupted`, { method: 'POST' });
}

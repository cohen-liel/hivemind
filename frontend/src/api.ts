import type {
  Project, ProjectMessage, FileChanges, TaskHistoryItem, Stats,
  FileTreeEntry, FileContent, Settings, BrowseDirsResponse, Schedule,
  LiveState, AgentPerformanceEntry, ActivityEvent,
} from './types';

const BASE = '/api';

/** LocalStorage key shared with WebSocketContext */
const AUTH_TOKEN_KEY = 'hivemind-auth-token';

/**
 * Read the API key — meta tag (server-injected) takes priority over localStorage.
 * This ensures a stale localStorage key never blocks auth after a server restart.
 */
function getApiKey(): string {
  // Meta tag is injected by the server on every page load — always authoritative
  const meta = document.querySelector<HTMLMetaElement>('meta[name="hivemind-auth-token"]');
  if (meta?.content) {
    // Keep localStorage in sync so WebSocket context also has the latest key
    try { localStorage.setItem(AUTH_TOKEN_KEY, meta.content); } catch { /* ignore */ }
    return meta.content;
  }

  // Fallback: localStorage (manual login or offline)
  try {
    const stored = localStorage.getItem(AUTH_TOKEN_KEY);
    if (stored) return stored;
  } catch { /* localStorage unavailable */ }

  return '';
}

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly isNetworkError: boolean = false,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

async function fetchJSON<T>(url: string, init?: RequestInit): Promise<T> {
  const apiKey = getApiKey();
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(apiKey ? { 'X-API-Key': apiKey } : {}),
    ...(init?.headers as Record<string, string> ?? {}),
  };
  let res: Response;
  try {
    res = await fetch(`${BASE}${url}`, {
      ...init,
      headers,
    });
  } catch (err) {
    // Network error (offline, DNS failure, CORS, etc.)
    throw new ApiError(
      err instanceof Error ? err.message : 'Network error',
      0,
      true,
    );
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    // On 401, redirect to login instead of letting callers retry forever
    if (res.status === 401) {
      window.dispatchEvent(new CustomEvent('hivemind-auth-expired'));
    }
    throw new ApiError(
      body.error || body.detail || `HTTP ${res.status}`,
      res.status,
    );
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

export async function updateProject(id: string, data: { name?: string; description?: string }): Promise<void> {
  await fetchJSON(`/projects/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
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

export async function sendMessage(id: string, message: string, mode?: string): Promise<void> {
  await fetchJSON(`/projects/${id}/message`, {
    method: 'POST',
    body: JSON.stringify({ message, ...(mode ? { mode } : {}) }),
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

export async function getLiveState(id: string): Promise<LiveState> {
  return fetchJSON<LiveState>(`/projects/${id}/live`);
}

export interface SessionSummary {
  project_id: string;
  status: string;
  summary_text: string | null;
  turn_count: number;
  total_cost_usd: number;
}

export async function getSessionSummary(id: string): Promise<SessionSummary> {
  return fetchJSON<SessionSummary>(`/projects/${id}/summary`);
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

// --- Agent Performance Analytics ---

export interface AgentStats {
  agent_role: string;
  total_runs: number;
  success_rate: number;
  avg_duration: number;
  last_run: number;
}

export async function getAgentStats(projectId?: string): Promise<AgentStats[]> {
  const url = projectId ? `/agent-stats?project_id=${projectId}` : '/agent-stats';
  const data = await fetchJSON<{ stats: AgentStats[] }>(url);
  return data.stats;
}

export async function getAgentRecentPerformance(agentRole: string, limit = 10): Promise<AgentPerformanceEntry[]> {
  const data = await fetchJSON<{ entries: AgentPerformanceEntry[] }>(`/agent-stats/${agentRole}/recent?limit=${limit}`);
  return data.entries;
}

// --- Activity Events (persisted to DB, used for state recovery on refresh) ---
// ActivityEvent type is defined in types.ts as a discriminated union (TS-04).
// Re-export here for backward-compatible imports.
export type { ActivityEvent } from './types';

export async function getActivity(
  projectId: string,
  since = 0,
  limit = 500,
): Promise<{ events: ActivityEvent[]; latest_sequence: number; source: string }> {
  return fetchJSON(`/projects/${projectId}/activity?since=${since}&limit=${limit}`);
}

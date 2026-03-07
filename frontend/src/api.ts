import type { Project, ProjectMessage, FileChanges, TaskHistoryItem, Stats, FileTreeEntry, FileContent, Settings, BrowseDirsResponse } from './types';

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

export async function getStats(): Promise<Stats> {
  return fetchJSON<Stats>('/stats');
}

export async function getSettings(): Promise<Settings> {
  return fetchJSON<Settings>('/settings');
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

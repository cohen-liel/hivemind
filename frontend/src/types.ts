export interface ProjectMessage {
  agent_name: string;
  role: string;
  content: string;
  timestamp: number;
  cost_usd: number;
}

export interface Project {
  project_id: string;
  project_name: string;
  project_dir: string;
  status: 'running' | 'paused' | 'idle' | 'stopped';
  is_running: boolean;
  is_paused: boolean;
  turn_count: number;
  total_cost_usd: number;
  agents: string[];
  multi_agent: boolean;
  last_message: ProjectMessage | null;
  user_id?: number;
  description?: string;
  created_at?: number;
  updated_at?: number;
  message_count?: number;
  conversation_log?: ProjectMessage[];
}

export interface FileChanges {
  stat: string;
  status: string;
  diff: string;
  error?: string;
}

export interface TaskHistoryItem {
  id: number;
  project_id: string;
  user_id: number;
  task_description: string;
  status: string;
  cost_usd: number;
  turns_used: number;
  started_at: number;
  completed_at: number | null;
  summary: string;
}

export interface Stats {
  total_cost_usd: number;
  total_projects: number;
  active_projects: number;
  running: number;
  paused: number;
}

export interface WSEvent {
  type: 'agent_update' | 'agent_result' | 'agent_final' | 'project_status'
    | 'tool_use' | 'agent_started' | 'agent_finished' | 'delegation' | 'loop_progress';
  project_id: string;
  project_name?: string;
  text?: string;
  status?: string;
  timestamp: number;
  // tool_use fields
  agent?: string;
  tool_name?: string;
  description?: string;
  input?: Record<string, unknown>;
  // agent_started/finished fields
  task?: string;
  cost?: number;
  turns?: number;
  duration?: number;
  is_error?: boolean;
  // delegation fields
  from_agent?: string;
  to_agent?: string;
  // loop_progress fields
  loop?: number;
  max_loops?: number;
  turn?: number;
  max_turns?: number;
  max_budget?: number;
}

export type ActivityType = 'tool_use' | 'agent_started' | 'agent_finished'
  | 'delegation' | 'agent_text' | 'user_message' | 'loop_progress';

export interface ActivityEntry {
  id: string;
  type: ActivityType;
  timestamp: number;
  agent?: string;
  // tool_use
  tool_name?: string;
  tool_description?: string;
  // agent_started/finished
  task?: string;
  cost?: number;
  turns?: number;
  duration?: number;
  is_error?: boolean;
  // delegation
  from_agent?: string;
  to_agent?: string;
  // text content
  content?: string;
  // loop_progress
  loop?: number;
  max_loops?: number;
  turn?: number;
  max_turns?: number;
  max_budget?: number;
}

export interface AgentState {
  name: string;
  state: 'idle' | 'working' | 'done' | 'error';
  task?: string;
  current_tool?: string;
  cost: number;
  turns: number;
  duration: number;
  // Delegation tracking
  delegated_from?: string;
  delegated_at?: number;
  // Last result preview
  last_result?: string;
}

export interface LoopProgress {
  loop: number;
  max_loops: number;
  turn: number;
  max_turns: number;
  cost: number;
  max_budget: number;
}

export interface FileTreeEntry {
  name: string;
  type: 'file' | 'dir';
  path: string;
  children?: FileTreeEntry[];
}

export interface FileContent {
  content?: string;
  path?: string;
  size?: number;
  error?: string;
}

export interface Settings {
  max_turns_per_cycle: number;
  max_budget_usd: number;
  agent_timeout_seconds: number;
  sdk_max_turns_per_query: number;
  sdk_max_budget_per_query: number;
  projects_base_dir: string;
  max_user_message_length: number;
  session_expiry_hours: number;
  max_orchestrator_loops: number;
}

export interface DirEntry {
  name: string;
  path: string;
  is_dir: boolean;
}

export interface BrowseDirsResponse {
  current: string;
  parent: string | null;
  entries: DirEntry[];
  error?: string;
}

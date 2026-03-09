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
  // Live agent states (survives browser refresh)
  agent_states?: Record<string, {
    state?: string;
    task?: string;
    current_tool?: string;
    cost?: number;
    turns?: number;
    duration?: number;
  }>;
  current_agent?: string;
  current_tool?: string;
  pending_messages?: number;
  pending_approval?: string;
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
    | 'tool_use' | 'agent_started' | 'agent_finished' | 'delegation' | 'loop_progress'
    | 'approval_request' | 'replay_batch' | 'live_state_sync' | 'history_cleared'
    | 'task_complete' | 'task_error' | 'task_graph' | 'self_healing';
  project_id: string;
  project_name?: string;
  text?: string;
  status?: string;
  timestamp: number;
  // Sequence tracking for cross-device sync
  sequence_id?: number;
  // tool_use fields
  agent?: string;
  tool_name?: string;
  description?: string;
  input?: Record<string, unknown>;
  // agent_started/finished fields
  task?: string;
  task_id?: string;       // DAG task ID (e.g. "task_001") for live plan tracking
  task_status?: string;   // DAG task status ("completed", "failed", etc.)
  is_remediation?: boolean;
  cost?: number;
  turns?: number;
  duration?: number;
  is_error?: boolean;
  failure_reason?: string;
  // delegation fields
  from_agent?: string;
  to_agent?: string;
  // loop_progress fields
  loop?: number;
  max_loops?: number;
  turn?: number;
  max_turns?: number;
  max_budget?: number;
  // task_graph fields (DAG visualization)
  graph?: {
    vision?: string;
    tasks?: Array<{
      id: string;
      role: string;
      goal: string;
      depends_on: string[];
      required_artifacts?: string[];
      is_remediation?: boolean;
    }>;
  };
  // self_healing fields
  failed_task?: string;
  failure_category?: string;
  remediation_task?: string;
  remediation_role?: string;
  // agent_update extended fields
  progress?: string;
  artifacts_count?: number;
  summary?: string;
  // live_state_sync fields
  agent_states?: Record<string, {
    state?: string;
    task?: string;
    current_tool?: string;
    cost?: number;
    turns?: number;
    duration?: number;
  }>;
  loop_progress?: LoopProgress;
  dag_graph?: WSEvent['graph'];
  dag_task_statuses?: Record<string, string>;
}

export type ActivityType = 'tool_use' | 'agent_started' | 'agent_finished'
  | 'delegation' | 'agent_text' | 'user_message' | 'loop_progress' | 'error';

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
  failure_reason?: string;
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
  // Timing for elapsed display & stale detection
  started_at?: number;
  last_update_at?: number;
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

export interface Schedule {
  id: number;
  project_id: string;
  project_name?: string;
  schedule_time: string;
  task_description: string;
  repeat: string;
  enabled: number;
  last_run: number | null;
  created_at: number;
}

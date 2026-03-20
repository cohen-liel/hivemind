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
  // Project health & progress
  diagnostics?: {
    health_score?: 'healthy' | 'degraded' | 'critical';
    warnings_count?: number;
    last_stuckness?: number | null;
    seconds_since_progress?: number | null;
  };
  dag_progress?: {
    total: number;
    completed: number;
    failed: number;
    running: number;
    percent: number;
  };
  dag_vision?: string;
}

export interface FileChanges {
  stat: string;
  status: string;
  diff: string;
  error?: string;
  /** Absolute path of the project whose git is shown */
  project_dir?: string;
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
    | 'task_complete' | 'task_error' | 'task_graph' | 'self_healing'
    | 'dag_task_update' | 'execution_error' | 'turn_progress' | 'pre_task_question';
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
  task_name?: string;     // Human-readable task name/goal from backend
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
  // execution_error fields
  error_message?: string;
  error_type?: string;
  completed_tasks?: number;
  total_tasks?: number;
  // turn_progress fields
  turns_used?: number;
  remaining?: number;
  // pre_task_question fields
  question?: string;
}

export type ActivityType = 'tool_use' | 'agent_started' | 'agent_finished'
  | 'delegation' | 'agent_text' | 'agent_result' | 'user_message' | 'loop_progress' | 'error';

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
  state: 'idle' | 'working' | 'waiting' | 'done' | 'error';
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
  is_git?: boolean;
}

export interface BrowseDirsResponse {
  current: string;
  parent: string | null;
  entries: DirEntry[];
  error?: string;
  home?: string;
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

// ============================================================================
// LiveState — real-time project status snapshot from /api/projects/:id/live
// (TS-03: moved from api.ts to types.ts for consistency)
// ============================================================================

/** Real-time project status snapshot returned by the /live endpoint. */
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
  /** DAG task graph for plan visualization — present when a DAG session is active */
  dag_graph?: {
    vision?: string;
    tasks?: Array<{
      id: string;
      role: string;
      goal: string;
      depends_on: string[];
      required_artifacts?: string[];
      is_remediation?: boolean;
    }>;
  } | null;
  /** Per-task status map for the active DAG (task_id -> status string) */
  dag_task_statuses?: Record<string, string>;
}

// ============================================================================
// Agent Performance & Cost Analytics (TS-02)
// ============================================================================

/** A single recent performance entry for an agent, returned by /api/agent-stats/:role/recent. */
export interface AgentPerformanceEntry {
  /** The agent's role identifier (e.g. "frontend_developer") */
  agent_role: string;
  /** Outcome status of the run (e.g. "completed", "error") */
  status: string;
  /** Wall-clock duration of the agent run in seconds */
  duration_seconds: number;
  /** Cost of the run in USD */
  cost_usd: number;
  /** Number of LLM turns consumed during the run */
  turns_used: number;
  /** Human-readable description of the task performed */
  task_description: string;
  /** Error message if the run failed, empty string otherwise */
  error_message: string;
  /** Orchestration round number this run belonged to */
  round_number: number;
  /** Unix timestamp (seconds) when the run was recorded */
  created_at: number;
}

// ============================================================================
// Settings — utility type for numeric-only keys
// ============================================================================

/** Keys of Settings that hold numeric values (excludes string-only fields like projects_base_dir). */
export type NumericSettingsKey = {
  [K in keyof Settings]: Settings[K] extends number ? K : never;
}[keyof Settings];

// ============================================================================
// ActivityEvent — discriminated union for persisted activity events (TS-04)
// ============================================================================

/** Base fields shared by all persisted activity events from /api/projects/:id/activity. */
interface ActivityEventBase {
  project_id: string;
  agent?: string;
  timestamp: number;
  sequence_id?: number;
}

/** A tool_use activity event — an agent invoked a tool. */
export interface ToolUseActivityEvent extends ActivityEventBase {
  type: 'tool_use';
  tool_name?: string;
  description?: string;
}

/** An agent_started activity event — an agent began working on a task. */
export interface AgentStartedActivityEvent extends ActivityEventBase {
  type: 'agent_started';
  task?: string;
}

/** An agent_finished activity event — an agent completed (or failed) its task. */
export interface AgentFinishedActivityEvent extends ActivityEventBase {
  type: 'agent_finished';
  cost?: number;
  turns?: number;
  duration?: number;
  is_error?: boolean;
}

/** A delegation activity event — one agent delegated work to another. */
export interface DelegationActivityEvent extends ActivityEventBase {
  type: 'delegation';
  from_agent?: string;
  to_agent?: string;
  task?: string;
}

/** A loop_progress activity event — orchestrator loop progress update. */
export interface LoopProgressActivityEvent extends ActivityEventBase {
  type: 'loop_progress';
  loop?: number;
  max_loops?: number;
  turn?: number;
  max_turns?: number;
  max_budget?: number;
  cost?: number;
}

/** A task_error activity event — a task failed with an error. */
export interface TaskErrorActivityEvent extends ActivityEventBase {
  type: 'task_error';
  text?: string;
  summary?: string;
}

/** A catch-all for other event types (agent_update, agent_result, etc.) that are
 *  persisted but not rendered directly in the activity feed. */
export interface OtherActivityEvent extends ActivityEventBase {
  type: string;
  text?: string;
  summary?: string;
  tool_name?: string;
  description?: string;
  task?: string;
  task_id?: string;
  cost?: number;
  turns?: number;
  duration?: number;
  is_error?: boolean;
  from_agent?: string;
  to_agent?: string;
  status?: string;
  loop?: number;
  max_loops?: number;
  turn?: number;
  max_turns?: number;
  max_budget?: number;
}

/**
 * Discriminated union of all persisted activity event types.
 * Each variant carries only the fields relevant to its event type.
 * Use `evt.type` to narrow to the correct variant.
 */
export type ActivityEvent =
  | ToolUseActivityEvent
  | AgentStartedActivityEvent
  | AgentFinishedActivityEvent
  | DelegationActivityEvent
  | LoopProgressActivityEvent
  | TaskErrorActivityEvent
  | OtherActivityEvent;

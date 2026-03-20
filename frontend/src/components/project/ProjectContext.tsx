/**
 * ProjectContext — Eliminates prop drilling from ProjectView → Layout components.
 *
 * Provides all project state and action callbacks via React Context so that
 * DesktopLayout, MobileLayout, and any deeply nested children can consume
 * exactly what they need without props being threaded through intermediaries.
 *
 * STATE-01 fix: replaces 20+ props passed through layout components.
 */

import { createContext, useContext } from 'react';
import type {
  Project,
  ActivityEntry,
  AgentState,
  LoopProgress,
  FileChanges,
  WSEvent,
} from '../../types';
import type {
  SdkCall,
  DesktopTab,
  MobileView,
  HealingEvent,
  LiveAgentEntry,
} from '../../reducers/projectReducer';
import type { AgentMetric } from '../../hooks/useAgentMetrics';

// ============================================================================
// Context Value Type
// ============================================================================

export interface ProjectContextValue {
  // ── Core data ──
  project: Project;
  projectId: string;
  connected: boolean;

  // ── Agent state ──
  orchestratorState: AgentState | null;
  subAgentStates: AgentState[];
  agentStateList: AgentState[];
  agentStates: Record<string, AgentState>;
  loopProgress: LoopProgress | null;

  // ── Activity & content ──
  activities: ActivityEntry[];
  files: FileChanges | null;
  sdkCalls: SdkCall[];
  liveAgentStream: Record<string, LiveAgentEntry>;
  agentMetrics: AgentMetric[];

  // ── Time & display ──
  now: number;
  lastTicker: string;

  // ── DAG ──
  dagGraph: WSEvent['graph'] | null;
  dagTaskStatus: Record<string, 'pending' | 'working' | 'completed' | 'failed' | 'cancelled' | 'skipped'>;
  dagTaskFailureReasons: Record<string, string>;
  healingEvents: HealingEvent[];

  // ── UI view state ──
  desktopTab: DesktopTab;
  selectedAgent: string | null;
  mobileView: MobileView;

  // ── Messaging ──
  hasMoreMessages: boolean;
  message: string;
  sending: boolean;

  // ── Pre-task question ──
  pendingQuestion: string | null;
  onClearQuestion: () => void;

  // ── Callbacks ──
  onSetDesktopTab: (tab: DesktopTab) => void;
  onSelectAgent: (agent: string | null) => void;
  onSetMobileView: (view: MobileView) => void;
  onLoadMore: () => Promise<void>;
  onPause: () => Promise<void>;
  onResume: () => Promise<void>;
  onStop: () => Promise<void>;
  onSend: (msg: string, mode?: string) => Promise<void>;
  onMobileSend: (msg: string) => void;
  onShowClearConfirm: () => void;
  onMessageChange: (msg: string) => void;
}

// ============================================================================
// Context
// ============================================================================

const ProjectContext = createContext<ProjectContextValue | null>(null);
ProjectContext.displayName = 'ProjectContext';

// ============================================================================
// Hook
// ============================================================================

/**
 * Access the ProjectContext value. Must be used within a ProjectContext.Provider.
 * Throws if used outside the provider — fail-fast prevents silent bugs.
 */
export function useProjectContext(): ProjectContextValue {
  const ctx = useContext(ProjectContext);
  if (ctx === null) {
    throw new Error(
      'useProjectContext must be used within a <ProjectContext.Provider>. ' +
      'Wrap your component tree with the ProjectContext provider in ProjectView.',
    );
  }
  return ctx;
}

export default ProjectContext;

/**
 * useSmartHeartbeat — Emits heartbeat activity entries for working agents.
 *
 * Fires every 45s per agent with real tool info from agent_states.
 * Includes stale warnings after 90s of no updates.
 * Phase-aware: suppresses stale warnings during recognized orchestrator
 * startup phases (context loading, architect review, PM planning).
 *
 * M-22 fix: hook owns its own 5-second interval so ProjectView's
 * per-second `now` state is NOT a dependency here. This breaks the
 * cascade where every 1s tick re-ran all heartbeat logic unnecessarily.
 */

import { useEffect, useRef } from 'react';
import type { Dispatch } from 'react';
import type { AgentState } from '../types';
import type { ProjectAction } from '../reducers/projectReducer';
import { nextId } from '../utils/activityHelpers';

/**
 * Keywords that identify orchestrator startup phases where long silences
 * are expected (architect review, PM planning, context loading).
 * Matches against orchestrator.task or orchestrator.current_tool.
 */
const STARTUP_PHASE_KEYWORDS: readonly string[] = [
  'loading project context',
  'reading memory',
  'loading context',
  'architect',
  'reviewing codebase',
  'analysing architecture',
  'pm creating',
  'planning',
  'creating task graph',
  'pm agent',
  'critic',
  'reviewing plan',
  'plan check',
  'evaluating',
  'preparing workspace',
  'loading lessons',
  'cross-project',
  'file tree',
  'manifest',
];

/**
 * Returns true if the orchestrator is in a recognized startup phase
 * where long silences are expected and stall warnings should be suppressed.
 */
export function isOrchestratorInStartupPhase(
  agentStates: Record<string, AgentState>,
): boolean {
  const orch = agentStates['orchestrator'];
  if (!orch || orch.state !== 'working') return false;

  const taskText = (orch.task || orch.current_tool || '').toLowerCase();
  if (!taskText) return false;

  return STARTUP_PHASE_KEYWORDS.some(kw => taskText.includes(kw));
}

/** Stale threshold during startup phases (5 min) */
const STARTUP_STALE_THRESHOLD_MS = 300_000;
/** Normal stale threshold (90s) */
const NORMAL_STALE_THRESHOLD_MS = 90_000;

export function useSmartHeartbeat(
  agentStates: Record<string, AgentState>,
  dispatch: Dispatch<ProjectAction>,
): void {
  // Stable ref so the interval callback always sees the latest agentStates
  // without needing to re-create the interval on every render.
  const agentStatesRef = useRef(agentStates);
  agentStatesRef.current = agentStates;

  const dispatchRef = useRef(dispatch);
  dispatchRef.current = dispatch;

  const lastHeartbeatRef = useRef<Record<string, number>>({});

  useEffect(() => {
    const tick = () => {
      const now = Date.now();
      const currentAgentStates = agentStatesRef.current;
      const currentDispatch = dispatchRef.current;

      const inStartupPhase = isOrchestratorInStartupPhase(currentAgentStates);

      const workingAgents = Object.entries(currentAgentStates).filter(
        ([, a]) => a.state === 'working',
      );

      for (const [agentName, agentState] of workingAgents) {
        const startedAt = agentState.started_at ?? now;
        const runningMs = now - startedAt;
        if (runningMs < 45_000) continue;

        const lastHb = lastHeartbeatRef.current[agentName];
        if (lastHb === undefined || now - lastHb >= 45_000) {
          lastHeartbeatRef.current[agentName] = now;
          const totalMin = Math.floor(runningMs / 60_000);
          const remSec = Math.floor((runningMs % 60_000) / 1_000);
          const timeStr =
            totalMin > 0
              ? remSec > 0
                ? `${totalMin}m ${remSec}s`
                : `${totalMin}m`
              : `${Math.floor(runningMs / 1000)}s`;

          const currentAction = (
            agentState.current_tool ||
            agentState.task ||
            ''
          ).slice(0, 100);
          const lastUpdateAt = agentState.last_update_at;

          // Use a much higher threshold during startup phases to avoid
          // false "stale" warnings while orchestrator loads context / plans.
          const staleThreshold = inStartupPhase
            ? STARTUP_STALE_THRESHOLD_MS
            : NORMAL_STALE_THRESHOLD_MS;

          const isStale = lastUpdateAt
            ? now - lastUpdateAt > staleThreshold
            : runningMs > staleThreshold;

          let heartbeatMessage: string;
          if (inStartupPhase && agentName === 'orchestrator') {
            // During startup: show phase-appropriate status, never stale warnings
            const phaseLabel = agentState.task || agentState.current_tool || 'initializing';
            heartbeatMessage = `🔄 ${agentName}: ${phaseLabel.slice(0, 80)} (${timeStr})`;
          } else if (isStale && !currentAction) {
            heartbeatMessage = `⏳ ${agentName}: thinking... (${timeStr})`;
          } else if (isStale) {
            heartbeatMessage = `⏳ ${agentName}: ${currentAction} (${timeStr})`;
          } else if (currentAction) {
            heartbeatMessage = `⚡ ${agentName}: ${currentAction} (${timeStr})`;
          } else {
            heartbeatMessage = `⏱️ ${agentName}: working... (${timeStr})`;
          }

          currentDispatch({
            type: 'ADD_ACTIVITY',
            activity: {
              id: nextId(),
              type: 'agent_text',
              timestamp: now / 1000,
              agent: agentName,
              content: heartbeatMessage,
            },
          });
        }
      }

      // Remove stopped agents from heartbeat tracking
      for (const agentName of Object.keys(lastHeartbeatRef.current)) {
        if (currentAgentStates[agentName]?.state !== 'working') {
          delete lastHeartbeatRef.current[agentName];
        }
      }
    };

    // Check every 5 seconds (fine-grained enough, avoids 1s cascade re-renders)
    const intervalId = setInterval(tick, 5_000);
    return () => clearInterval(intervalId);
  }, []); // empty deps — stable via refs
}

/**
 * useSmartHeartbeat — Emits heartbeat activity entries for working agents.
 *
 * Fires every 45s per agent with real tool info from agent_states.
 * Includes stale warnings after 90s of no updates.
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

      const workingAgents = Object.entries(currentAgentStates).filter(
        ([, a]) => a.state === 'working' || a.state === 'waiting',
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
          const isStale = lastUpdateAt
            ? now - lastUpdateAt > 90_000
            : runningMs > 90_000;

          let heartbeatMessage: string;
          if (isStale && !currentAction) {
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
        if (currentAgentStates[agentName]?.state !== 'working' && currentAgentStates[agentName]?.state !== 'waiting') {
          delete lastHeartbeatRef.current[agentName];
        }
      }
    };

    // Check every 5 seconds (fine-grained enough, avoids 1s cascade re-renders)
    const intervalId = setInterval(tick, 5_000);
    return () => clearInterval(intervalId);
  }, []); // empty deps — stable via refs
}

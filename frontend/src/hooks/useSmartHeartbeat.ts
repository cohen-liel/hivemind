/**
 * useSmartHeartbeat — Emits heartbeat activity entries for working agents.
 *
 * Fires every 45s per agent with real tool info from agent_states.
 * Includes stale warnings after 30s of no updates.
 */

import { useEffect, useRef } from 'react';
import type { Dispatch } from 'react';
import type { AgentState } from '../types';
import type { ProjectAction } from '../reducers/projectReducer';
import { nextId } from '../utils/activityHelpers';

export function useSmartHeartbeat(
  now: number,
  agentStates: Record<string, AgentState>,
  dispatch: Dispatch<ProjectAction>,
): void {
  const lastHeartbeatRef = useRef<Record<string, number>>({});

  useEffect(() => {
    const workingAgents = Object.entries(agentStates).filter(
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
        const isStale = lastUpdateAt
          ? now - lastUpdateAt > 30_000
          : runningMs > 30_000;

        let heartbeatMessage: string;
        if (isStale && !currentAction) {
          heartbeatMessage = `⏳ ${agentName}: waiting for Claude response... (${timeStr})`;
        } else if (isStale) {
          heartbeatMessage = `⏳ ${agentName}: ${currentAction} (${timeStr}, no new activity for 30s+)`;
        } else if (currentAction) {
          heartbeatMessage = `⚡ ${agentName}: ${currentAction} (${timeStr})`;
        } else {
          heartbeatMessage = `⏱️ ${agentName}: working... (${timeStr})`;
        }

        dispatch({
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
      if (agentStates[agentName]?.state !== 'working') {
        delete lastHeartbeatRef.current[agentName];
      }
    }
  }, [now, agentStates, dispatch]);
}

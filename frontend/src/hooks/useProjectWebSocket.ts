/**
 * useProjectWebSocket — WebSocket event dispatcher for ProjectView.
 *
 * Maps incoming WSEvent types to typed ProjectAction dispatches.
 * Also triggers side effects like toast notifications and data reloads.
 */

import { useCallback } from 'react';
import type { Dispatch } from 'react';
import type { WSEvent } from '../types';
import type { ProjectAction } from '../reducers/projectReducer';
import { nextId } from '../utils/activityHelpers';

interface UseProjectWebSocketDeps {
  projectId: string | undefined;
  dispatch: Dispatch<ProjectAction>;
  loadProject: () => Promise<void>;
  loadFiles: () => Promise<void>;
  toast: {
    error: (title: string, message: string) => void;
  };
}

export function useProjectWebSocket({
  projectId,
  dispatch,
  loadProject,
  loadFiles,
  toast,
}: UseProjectWebSocketDeps): (event: WSEvent) => void {
  return useCallback(
    (event: WSEvent): void => {
      if (event.project_id !== projectId) return;

      switch (event.type) {
        case 'agent_update':
          dispatch({ type: 'WS_AGENT_UPDATE', event });
          break;
        case 'tool_use':
          dispatch({ type: 'WS_TOOL_USE', event });
          break;
        case 'agent_started':
          dispatch({ type: 'WS_AGENT_STARTED', event });
          break;
        case 'agent_finished':
          dispatch({ type: 'WS_AGENT_FINISHED', event });
          break;
        case 'delegation':
          dispatch({ type: 'WS_DELEGATION', event });
          break;
        case 'loop_progress':
          dispatch({ type: 'WS_LOOP_PROGRESS', event });
          break;
        case 'agent_result':
          dispatch({ type: 'WS_AGENT_RESULT', event });
          break;
        case 'agent_final':
          dispatch({ type: 'WS_AGENT_FINAL', event });
          loadProject().catch(() => {});
          loadFiles().catch(() => {});
          if (
            document.hidden &&
            'Notification' in window &&
            Notification.permission === 'granted'
          ) {
            new Notification('Task Complete', {
              body: event.text?.slice(0, 100) || 'Agent finished working',
              icon: '/favicon.ico',
            });
          }
          break;
        case 'project_status':
          dispatch({ type: 'WS_PROJECT_STATUS', event });
          loadProject().catch(() => {});
          if (event.status === 'running' && projectId) {
            try {
              localStorage.removeItem(`hivemind_dag_${projectId}`);
            } catch {
              /* ignore */
            }
          }
          break;
        case 'task_graph':
          dispatch({ type: 'WS_TASK_GRAPH', event });
          break;
        case 'task_error':
          dispatch({
            type: 'ADD_ACTIVITY',
            activity: {
              id: nextId(),
              type: 'error',
              timestamp: event.timestamp ?? Date.now() / 1000,
              agent: event.agent || 'system',
              content: `Task failed${event.agent ? ` (${event.agent})` : ''}: ${event.text || event.summary || 'Unknown error'}`,
            },
          });
          toast.error(
            `${event.agent || 'Task'} failed`,
            (event.text || event.summary || 'An error occurred').slice(0, 120),
          );
          break;
        case 'dag_task_update':
          dispatch({ type: 'WS_DAG_TASK_UPDATE', event });
          break;
        case 'task_progress':
          dispatch({ type: 'WS_TASK_PROGRESS', event });
          break;
        case 'dag_progress':
          dispatch({ type: 'WS_DAG_PROGRESS', event });
          break;
        case 'plan_delta':
          try {
            dispatch({ type: 'WS_PLAN_DELTA', event });
          } catch (err) {
            console.warn('[useProjectWebSocket] Malformed plan_delta event, skipping:', err, event);
          }
          break;
        case 'task_complete':
          dispatch({
            type: 'ADD_ACTIVITY',
            activity: {
              id: nextId(),
              type: 'agent_result',
              timestamp: event.timestamp ?? Date.now() / 1000,
              agent: event.agent || 'system',
              content: `DAG task complete: ${event.task_id ?? event.task_name ?? 'unknown'}`,
            },
          });
          break;
        case 'execution_error':
          dispatch({ type: 'WS_EXECUTION_ERROR', event });
          toast.error(
            'Execution Failed',
            (event.error_message || 'DAG execution crashed').slice(0, 200),
          );
          break;
        case 'self_healing':
          dispatch({ type: 'WS_SELF_HEALING', event });
          break;
        case 'approval_request':
          dispatch({ type: 'WS_APPROVAL_REQUEST', event });
          break;
        case 'pre_task_question':
          dispatch({ type: 'WS_PRE_TASK_QUESTION', event });
          break;
        case 'history_cleared':
          dispatch({ type: 'WS_HISTORY_CLEARED' });
          if (projectId) {
            try {
              localStorage.removeItem(`hivemind_dag_${projectId}`);
            } catch {
              /* ignore */
            }
          }
          loadProject().catch(() => {});
          break;
        case 'live_state_sync':
          dispatch({ type: 'WS_LIVE_STATE_SYNC', event });
          break;
        case 'turn_progress': {
          // Update the live agent stream progress field with turn consumption.
          // The event carries turns_used, max_turns, remaining.
          if (event.agent) {
            const turnsUsed = event.turns_used;
            const maxTurns = event.max_turns;
            const remaining = event.remaining;
            if (turnsUsed !== undefined && maxTurns !== undefined) {
              dispatch({
                type: 'WS_TURN_PROGRESS',
                agent: event.agent,
                turnsUsed,
                maxTurns,
                remaining: remaining ?? Math.max(0, maxTurns - turnsUsed),
              });
            }
          }
          break;
        }
        default:
          break;
      }
    },
    [projectId, dispatch, loadProject, loadFiles, toast],
  );
}

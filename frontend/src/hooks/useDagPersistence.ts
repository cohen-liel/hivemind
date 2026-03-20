/**
 * useDagPersistence — Hydrates and persists DAG graph state to localStorage.
 *
 * On mount, restores any saved DAG graph within a 24-hour window.
 * Also restores cumulative plan state (including skipped tasks) from the
 * persistent plan storage managed by the projectReducer.
 * On changes, persists the current DAG graph and task statuses.
 */

import { useEffect } from 'react';
import type { Dispatch } from 'react';
import type { WSEvent } from '../types';
import type { ProjectAction, DagTaskStatusValue } from '../reducers/projectReducer';
import { restorePlanState } from '../reducers/projectReducer';

const AGE_LIMIT_MS = 24 * 60 * 60 * 1000;

export function useDagPersistence(
  projectId: string | undefined,
  dagGraph: WSEvent['graph'] | null,
  dagTaskStatus: Record<string, DagTaskStatusValue>,
  dispatch: Dispatch<ProjectAction>,
): void {
  // Hydrate from localStorage on mount
  useEffect(() => {
    if (!projectId) return;

    // First try the cumulative plan storage (new system — includes skipped tasks)
    const planState = restorePlanState(projectId);
    if (planState?.dagGraph) {
      dispatch({
        type: 'HYDRATE_DAG',
        graph: planState.dagGraph,
        statuses: planState.dagTaskStatus ?? {},
      });
      return;
    }

    // Fall back to legacy DAG storage
    try {
      const saved = localStorage.getItem(`hivemind_dag_${projectId}`);
      if (saved) {
        const parsed: {
          graph: WSEvent['graph'];
          statuses?: Record<string, DagTaskStatusValue>;
          savedAt: number;
        } = JSON.parse(saved);
        if (Date.now() - parsed.savedAt < AGE_LIMIT_MS && parsed.graph) {
          dispatch({
            type: 'HYDRATE_DAG',
            graph: parsed.graph,
            statuses: parsed.statuses ?? {},
          });
        }
      }
    } catch {
      /* corrupted storage — ignore */
    }
  }, [projectId, dispatch]);

  // Persist to localStorage on changes (legacy key — kept for backward compat)
  useEffect(() => {
    if (!projectId || !dagGraph) return;
    const key = `hivemind_dag_${projectId}`;
    const value = JSON.stringify({
      graph: dagGraph,
      statuses: dagTaskStatus,
      savedAt: Date.now(),
    });
    try {
      localStorage.setItem(key, value);
    } catch {
      // localStorage may be unavailable (private browsing, quota exceeded).
      // DAG persistence is non-critical — fail silently.
    }
  }, [projectId, dagGraph, dagTaskStatus]);
}

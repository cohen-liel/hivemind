/**
 * useDagPersistence — Hydrates and persists DAG graph state to localStorage.
 *
 * On mount, restores any saved DAG graph within a 24-hour window.
 * On changes, persists the current DAG graph and task statuses.
 */

import { useEffect } from 'react';
import type { Dispatch } from 'react';
import type { WSEvent } from '../types';
import type { ProjectAction } from '../reducers/projectReducer';

const AGE_LIMIT_MS = 24 * 60 * 60 * 1000;

export function useDagPersistence(
  projectId: string | undefined,
  dagGraph: WSEvent['graph'] | null,
  dagTaskStatus: Record<string, 'pending' | 'working' | 'completed' | 'failed'>,
  dispatch: Dispatch<ProjectAction>,
): void {
  // Hydrate from localStorage on mount
  useEffect(() => {
    if (!projectId) return;
    try {
      const saved = localStorage.getItem(`nexus_dag_${projectId}`);
      if (saved) {
        const parsed: {
          graph: WSEvent['graph'];
          statuses?: Record<string, 'pending' | 'working' | 'completed' | 'failed'>;
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

  // Persist to localStorage on changes
  useEffect(() => {
    if (!projectId || !dagGraph) return;
    const key = `nexus_dag_${projectId}`;
    const value = JSON.stringify({
      graph: dagGraph,
      statuses: dagTaskStatus,
      savedAt: Date.now(),
    });
    try {
      localStorage.setItem(key, value);
    } catch (e) {
      if (e instanceof DOMException && e.name === 'QuotaExceededError') {
        console.warn('[DagPersistence] localStorage quota exceeded, DAG state not persisted');
      } else {
        throw e;
      }
    }
  }, [projectId, dagGraph, dagTaskStatus]);
}

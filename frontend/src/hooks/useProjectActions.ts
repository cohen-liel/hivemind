/**
 * useProjectActions — Action handlers for ProjectView.
 *
 * Encapsulates all user-initiated actions (send, pause, resume, stop,
 * clear history, resume/discard interrupted tasks) with proper error
 * handling and toast notifications.
 */

import { useCallback } from 'react';
import type { Dispatch } from 'react';
import {
  sendMessage,
  pauseProject,
  resumeProject,
  stopProject,
  clearHistory,
  resumeInterruptedTask,
  discardInterruptedTask,
} from '../api';
import type { ProjectAction } from '../reducers/projectReducer';
import { nextId } from '../utils/activityHelpers';

interface Toast {
  success: (msg: string) => void;
  error: (title: string, msg: string) => void;
  info: (msg: string) => void;
  warning: (msg: string) => void;
}

export interface ProjectActions {
  handleSend: (msg: string) => Promise<void>;
  handlePause: () => Promise<void>;
  handleResume: () => Promise<void>;
  handleStop: () => Promise<void>;
  handleClearHistory: () => Promise<void>;
  handleResumeTask: () => Promise<void>;
  handleDiscardTask: () => Promise<void>;
  handleMobileSend: (msg: string) => void;
}

export function useProjectActions(
  projectId: string | undefined,
  dispatch: Dispatch<ProjectAction>,
  toast: Toast,
  loadProject: () => Promise<void>,
): ProjectActions {
  const handleSend = useCallback(
    async (msg: string): Promise<void> => {
      if (!projectId) return;
      dispatch({
        type: 'ADD_ACTIVITY',
        activity: {
          id: nextId(),
          type: 'user_message',
          timestamp: Date.now() / 1000,
          agent: 'user',
          content: msg,
        },
      });
      try {
        await sendMessage(projectId, msg);
        toast.success('Message sent');
      } catch (err: unknown) {
        const errMsg = err instanceof Error ? err.message : String(err);
        toast.error('Send failed', errMsg);
        dispatch({
          type: 'ADD_ACTIVITY',
          activity: {
            id: nextId(),
            type: 'error',
            timestamp: Date.now() / 1000,
            agent: 'system',
            content: `Failed to send: ${errMsg}`,
          },
        });
      }
      loadProject().catch(() => {});
    },
    [projectId, dispatch, toast, loadProject],
  );

  const handlePause = useCallback(async (): Promise<void> => {
    if (!projectId) return;
    try {
      await pauseProject(projectId);
      toast.info('Project paused');
      loadProject().catch(() => {});
    } catch (err: unknown) {
      toast.error('Pause failed', err instanceof Error ? err.message : String(err));
    }
  }, [projectId, toast, loadProject]);

  const handleResume = useCallback(async (): Promise<void> => {
    if (!projectId) return;
    try {
      await resumeProject(projectId);
      toast.success('Project resumed');
      loadProject().catch(() => {});
    } catch (err: unknown) {
      toast.error('Resume failed', err instanceof Error ? err.message : String(err));
    }
  }, [projectId, toast, loadProject]);

  const handleStop = useCallback(async (): Promise<void> => {
    if (!projectId) return;
    try {
      await stopProject(projectId);
      toast.warning('Project stopped');
      loadProject().catch(() => {});
    } catch (err: unknown) {
      toast.error('Stop failed', err instanceof Error ? err.message : String(err));
    }
  }, [projectId, toast, loadProject]);

  const handleClearHistory = useCallback(async (): Promise<void> => {
    if (!projectId) return;
    dispatch({ type: 'SET_SHOW_CLEAR_CONFIRM', show: false });
    try {
      await clearHistory(projectId);
      dispatch({ type: 'CLEAR_ALL_STATE' });
      // Also clear persisted DAG graph from localStorage
      // (otherwise it rehydrates stale data on next page load)
      try {
        localStorage.removeItem(`nexus_dag_${projectId}`);
      } catch { /* localStorage unavailable — ignore */ }
      toast.success('History cleared');
      loadProject().catch(() => {});
    } catch (e: unknown) {
      toast.error('Clear failed', e instanceof Error ? e.message : String(e));
    }
  }, [projectId, dispatch, toast, loadProject]);

  const handleResumeTask = useCallback(async (): Promise<void> => {
    if (!projectId) return;
    try {
      await resumeInterruptedTask(projectId);
      dispatch({ type: 'SET_RESUMABLE_TASK', task: null });
      toast.success('Task resumed');
      loadProject().catch(() => {});
    } catch (e: unknown) {
      toast.error(
        'Failed to resume task',
        e instanceof Error ? e.message : 'Unknown error',
      );
    }
  }, [projectId, dispatch, toast, loadProject]);

  const handleDiscardTask = useCallback(async (): Promise<void> => {
    if (!projectId) return;
    try {
      await discardInterruptedTask(projectId);
      dispatch({ type: 'SET_RESUMABLE_TASK', task: null });
      toast.info('Task discarded');
    } catch (e: unknown) {
      toast.error(
        'Failed to discard task',
        e instanceof Error ? e.message : 'Unknown error',
      );
    }
  }, [projectId, dispatch, toast]);

  const handleMobileSend = useCallback(
    (msg: string): void => {
      dispatch({ type: 'SET_SENDING', sending: true });
      handleSend(msg).finally(() =>
        dispatch({ type: 'SET_SENDING', sending: false }),
      );
    },
    [handleSend, dispatch],
  );

  return {
    handleSend,
    handlePause,
    handleResume,
    handleStop,
    handleClearHistory,
    handleResumeTask,
    handleDiscardTask,
    handleMobileSend,
  };
}

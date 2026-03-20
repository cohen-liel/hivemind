import { useState, useEffect, useCallback } from 'react';
import {
  getCircles,
  getCircle,
  getCircleMembers,
  getCircleProjects,
  createCircle,
  deleteCircle,
  addCircleMember,
  removeCircleMember,
} from '../api';
import type { Circle, CircleMember, Project } from '../types';

interface UseCirclesReturn {
  circles: Circle[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  create: (data: { name: string; description?: string }) => Promise<Circle | null>;
  remove: (id: string) => Promise<boolean>;
}

export function useCircles(): UseCirclesReturn {
  const [circles, setCircles] = useState<Circle[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setError(null);
      const data = await getCircles();
      setCircles(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load circles');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const create = useCallback(async (data: { name: string; description?: string }): Promise<Circle | null> => {
    try {
      const circle = await createCircle(data);
      await refresh();
      return circle;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create circle');
      return null;
    }
  }, [refresh]);

  const remove = useCallback(async (id: string): Promise<boolean> => {
    try {
      await deleteCircle(id);
      setCircles(prev => prev.filter(c => c.id !== id));
      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete circle');
      return false;
    }
  }, []);

  return { circles, loading, error, refresh, create, remove };
}

interface UseCircleDetailReturn {
  circle: Circle | null;
  members: CircleMember[];
  projects: Project[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  addMember: (userId: string, role?: string) => Promise<boolean>;
  removeMember: (userId: string) => Promise<boolean>;
}

export function useCircleDetail(circleId: string | null): UseCircleDetailReturn {
  const [circle, setCircle] = useState<Circle | null>(null);
  const [members, setMembers] = useState<CircleMember[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!circleId) return;
    try {
      setError(null);
      setLoading(true);
      const [c, m, p] = await Promise.all([
        getCircle(circleId),
        getCircleMembers(circleId),
        getCircleProjects(circleId),
      ]);
      setCircle(c);
      setMembers(m);
      setProjects(p);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load circle');
    } finally {
      setLoading(false);
    }
  }, [circleId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const addMember = useCallback(async (userId: string, role = 'member'): Promise<boolean> => {
    if (!circleId) return false;
    try {
      await addCircleMember(circleId, userId, role);
      await refresh();
      return true;
    } catch {
      return false;
    }
  }, [circleId, refresh]);

  const removeMember = useCallback(async (userId: string): Promise<boolean> => {
    if (!circleId) return false;
    try {
      await removeCircleMember(circleId, userId);
      setMembers(prev => prev.filter(m => m.user_id !== userId));
      return true;
    } catch {
      return false;
    }
  }, [circleId]);

  return { circle, members, projects, loading, error, refresh, addMember, removeMember };
}

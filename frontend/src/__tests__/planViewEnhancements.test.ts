/**
 * Tests for plan view enhancement helpers: buildArtifactEdges, buildUpstreamContexts,
 * estimateTaskEtas, detectRemediationChains.
 *
 * Covers: planViewHelpers.ts (task_006)
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import {
  buildArtifactEdges,
  buildUpstreamContexts,
  estimateTaskEtas,
  detectRemediationChains,
  dagToPlanSteps,
} from '../components/planViewHelpers';
import type {
  PlanStep,
  ArtifactEdge,
  UpstreamContext,
  TaskEta,
  RemediationChain,
  DagGraph,
  DagTask,
} from '../components/planViewHelpers';

// ── Helpers ──────────────────────────────────────────────────────────────────

function makeStep(overrides: Partial<PlanStep> = {}): PlanStep {
  return {
    index: 1,
    text: 'Default task goal text for testing',
    status: 'pending',
    agent: 'backend_developer',
    taskId: 'task_001',
    dependsOn: [],
    messageRound: 1,
    ...overrides,
  };
}

function makeDagGraph(tasks: DagTask[], vision?: string): DagGraph {
  return { vision: vision ?? 'Test vision', tasks };
}


// ── Tests: buildArtifactEdges ───────────────────────────────────────────────

describe('buildArtifactEdges (task_006)', () => {
  it('test_artifact_edges_when_dependency_done_should_be_received', () => {
    const steps: PlanStep[] = [
      makeStep({ taskId: 'task_001', status: 'done', dependsOn: [] }),
      makeStep({ taskId: 'task_002', status: 'pending', dependsOn: ['task_001'] }),
    ];
    const edges = buildArtifactEdges(steps);
    expect(edges).toHaveLength(1);
    expect(edges[0]).toEqual({
      fromTaskId: 'task_001',
      toTaskId: 'task_002',
      status: 'received',
    });
  });

  it('test_artifact_edges_when_dependency_in_progress_should_be_partial', () => {
    const steps: PlanStep[] = [
      makeStep({ taskId: 'task_001', status: 'in_progress', dependsOn: [] }),
      makeStep({ taskId: 'task_002', status: 'pending', dependsOn: ['task_001'] }),
    ];
    const edges = buildArtifactEdges(steps);
    expect(edges[0].status).toBe('partial');
  });

  it('test_artifact_edges_when_dependency_pending_should_be_missing', () => {
    const steps: PlanStep[] = [
      makeStep({ taskId: 'task_001', status: 'pending', dependsOn: [] }),
      makeStep({ taskId: 'task_002', status: 'pending', dependsOn: ['task_001'] }),
    ];
    const edges = buildArtifactEdges(steps);
    expect(edges[0].status).toBe('missing');
  });

  it('test_artifact_edges_when_dependency_error_should_be_missing', () => {
    const steps: PlanStep[] = [
      makeStep({ taskId: 'task_001', status: 'error', dependsOn: [] }),
      makeStep({ taskId: 'task_002', status: 'pending', dependsOn: ['task_001'] }),
    ];
    const edges = buildArtifactEdges(steps);
    expect(edges[0].status).toBe('missing');
  });

  it('test_artifact_edges_when_no_dependencies_should_return_empty', () => {
    const steps: PlanStep[] = [
      makeStep({ taskId: 'task_001', dependsOn: [] }),
      makeStep({ taskId: 'task_002', dependsOn: [] }),
    ];
    const edges = buildArtifactEdges(steps);
    expect(edges).toHaveLength(0);
  });

  it('test_artifact_edges_when_multiple_deps_should_create_all_edges', () => {
    const steps: PlanStep[] = [
      makeStep({ taskId: 'task_001', status: 'done', dependsOn: [] }),
      makeStep({ taskId: 'task_002', status: 'in_progress', dependsOn: [] }),
      makeStep({ taskId: 'task_003', dependsOn: ['task_001', 'task_002'] }),
    ];
    const edges = buildArtifactEdges(steps);
    expect(edges).toHaveLength(2);
    expect(edges.find(e => e.fromTaskId === 'task_001')?.status).toBe('received');
    expect(edges.find(e => e.fromTaskId === 'task_002')?.status).toBe('partial');
  });

  it('test_artifact_edges_when_no_taskId_should_skip', () => {
    const steps: PlanStep[] = [
      makeStep({ taskId: undefined, dependsOn: ['task_001'] }),
    ];
    const edges = buildArtifactEdges(steps);
    expect(edges).toHaveLength(0);
  });
});


// ── Tests: buildUpstreamContexts ────────────────────────────────────────────

describe('buildUpstreamContexts (task_006)', () => {
  it('test_upstream_when_has_dependencies_should_build_context', () => {
    const steps: PlanStep[] = [
      makeStep({ taskId: 'task_001', text: 'Build API endpoints', agent: 'backend_developer', status: 'done' }),
      makeStep({ taskId: 'task_002', text: 'Build frontend', agent: 'frontend_developer', dependsOn: ['task_001'] }),
    ];
    const contexts = buildUpstreamContexts(steps);
    expect(contexts.size).toBe(1);
    const ctx = contexts.get('task_002');
    expect(ctx).toBeDefined();
    expect(ctx!.upstreamTasks).toHaveLength(1);
    expect(ctx!.upstreamTasks[0].role).toBe('backend_developer');
    expect(ctx!.upstreamTasks[0].goalSummary).toBe('Build API endpoints');
  });

  it('test_upstream_when_no_dependencies_should_not_include', () => {
    const steps: PlanStep[] = [
      makeStep({ taskId: 'task_001', dependsOn: [] }),
    ];
    const contexts = buildUpstreamContexts(steps);
    expect(contexts.size).toBe(0);
  });

  it('test_upstream_should_truncate_long_goals_to_60_chars', () => {
    const longGoal = 'A'.repeat(100);
    const steps: PlanStep[] = [
      makeStep({ taskId: 'task_001', text: longGoal, dependsOn: [] }),
      makeStep({ taskId: 'task_002', dependsOn: ['task_001'] }),
    ];
    const contexts = buildUpstreamContexts(steps);
    const ctx = contexts.get('task_002');
    expect(ctx!.upstreamTasks[0].goalSummary.length).toBeLessThanOrEqual(60);
    expect(ctx!.upstreamTasks[0].goalSummary).toContain('…');
  });

  it('test_upstream_when_dep_not_found_should_skip', () => {
    const steps: PlanStep[] = [
      makeStep({ taskId: 'task_002', dependsOn: ['task_missing'] }),
    ];
    const contexts = buildUpstreamContexts(steps);
    // Should have the key but no upstream tasks (missing dep)
    const ctx = contexts.get('task_002');
    expect(ctx).toBeUndefined(); // No upstream found
  });

  it('test_upstream_when_no_agent_should_use_unknown', () => {
    const steps: PlanStep[] = [
      makeStep({ taskId: 'task_001', agent: undefined, dependsOn: [] }),
      makeStep({ taskId: 'task_002', dependsOn: ['task_001'] }),
    ];
    const contexts = buildUpstreamContexts(steps);
    const ctx = contexts.get('task_002');
    expect(ctx!.upstreamTasks[0].role).toBe('unknown');
  });
});


// ── Tests: estimateTaskEtas ─────────────────────────────────────────────────

describe('estimateTaskEtas (task_006)', () => {
  it('test_eta_when_in_progress_with_start_time_should_calculate_remaining', () => {
    const now = Date.now() / 1000;
    const steps: PlanStep[] = [
      makeStep({ taskId: 'task_001', status: 'in_progress', agent: 'pm' }),
    ];
    const etas = estimateTaskEtas(steps, { task_001: now - 20 });
    const eta = etas.get('task_001');
    expect(eta).toBeDefined();
    // PM avg is 45s, elapsed 20s, remaining ~25s
    expect(eta!.etaSeconds).toBeGreaterThanOrEqual(20);
    expect(eta!.etaSeconds).toBeLessThanOrEqual(30);
  });

  it('test_eta_when_in_progress_without_start_time_should_use_full_avg', () => {
    const steps: PlanStep[] = [
      makeStep({ taskId: 'task_001', status: 'in_progress', agent: 'backend_developer' }),
    ];
    const etas = estimateTaskEtas(steps);
    const eta = etas.get('task_001');
    expect(eta).toBeDefined();
    expect(eta!.etaSeconds).toBe(120); // backend_developer avg
  });

  it('test_eta_when_pending_should_include_dependency_eta', () => {
    const steps: PlanStep[] = [
      makeStep({ taskId: 'task_001', status: 'in_progress', agent: 'pm', dependsOn: [] }),
      makeStep({ taskId: 'task_002', status: 'pending', agent: 'backend_developer', dependsOn: ['task_001'] }),
    ];
    const etas = estimateTaskEtas(steps);
    const task2Eta = etas.get('task_002');
    expect(task2Eta).toBeDefined();
    // Should be task_001 eta + backend_developer avg (120s)
    expect(task2Eta!.etaSeconds).toBeGreaterThan(120);
  });

  it('test_eta_when_done_should_not_include', () => {
    const steps: PlanStep[] = [
      makeStep({ taskId: 'task_001', status: 'done' }),
    ];
    const etas = estimateTaskEtas(steps);
    expect(etas.has('task_001')).toBe(false);
  });

  it('test_eta_when_skipped_should_not_include', () => {
    const steps: PlanStep[] = [
      makeStep({ taskId: 'task_001', status: 'skipped' }),
    ];
    const etas = estimateTaskEtas(steps);
    expect(etas.has('task_001')).toBe(false);
  });

  it('test_eta_when_unknown_role_should_use_default_90s', () => {
    const steps: PlanStep[] = [
      makeStep({ taskId: 'task_001', status: 'in_progress', agent: 'unknown_role' }),
    ];
    const etas = estimateTaskEtas(steps);
    expect(etas.get('task_001')!.etaSeconds).toBe(90);
  });

  it('test_eta_display_should_format_correctly', () => {
    const steps: PlanStep[] = [
      makeStep({ taskId: 'task_001', status: 'in_progress', agent: 'pm' }),
    ];
    const etas = estimateTaskEtas(steps);
    const eta = etas.get('task_001');
    expect(eta!.etaDisplay).toMatch(/^[~<]/); // Starts with ~ or <
  });

  it('test_eta_when_elapsed_exceeds_avg_should_clamp_to_zero', () => {
    const now = Date.now() / 1000;
    const steps: PlanStep[] = [
      makeStep({ taskId: 'task_001', status: 'in_progress', agent: 'pm' }),
    ];
    // PM avg is 45s, but elapsed is 100s
    const etas = estimateTaskEtas(steps, { task_001: now - 100 });
    const eta = etas.get('task_001');
    expect(eta!.etaSeconds).toBe(0);
    expect(eta!.etaDisplay).toBe('<1m');
  });
});


// ── Tests: detectRemediationChains ──────────────────────────────────────────

describe('detectRemediationChains (task_006)', () => {
  it('test_detect_when_no_remediations_should_return_empty', () => {
    const steps: PlanStep[] = [
      makeStep({ taskId: 'task_001' }),
      makeStep({ taskId: 'task_002' }),
    ];
    const chains = detectRemediationChains(steps);
    expect(chains).toHaveLength(0);
  });

  it('test_detect_when_single_remediation_should_link_to_original', () => {
    const steps: PlanStep[] = [
      makeStep({ taskId: 'task_001', status: 'error' }),
      makeStep({ taskId: 'task_fix', isRemediation: true, dependsOn: ['task_001'], status: 'in_progress' }),
    ];
    const chains = detectRemediationChains(steps);
    expect(chains).toHaveLength(1);
    expect(chains[0].originalTaskId).toBe('task_001');
    expect(chains[0].remediationTaskIds).toContain('task_fix');
    expect(chains[0].isHealed).toBe(false);
  });

  it('test_detect_when_remediation_done_should_mark_healed', () => {
    const steps: PlanStep[] = [
      makeStep({ taskId: 'task_001', status: 'error' }),
      makeStep({ taskId: 'task_fix', isRemediation: true, dependsOn: ['task_001'], status: 'done' }),
    ];
    const chains = detectRemediationChains(steps);
    expect(chains[0].isHealed).toBe(true);
  });

  it('test_detect_when_chained_remediations_should_track_chain', () => {
    const steps: PlanStep[] = [
      makeStep({ taskId: 'task_001', status: 'error' }),
      makeStep({ taskId: 'task_fix1', isRemediation: true, dependsOn: ['task_001'], status: 'error' }),
      makeStep({ taskId: 'task_fix2', isRemediation: true, dependsOn: ['task_fix1'], status: 'done' }),
    ];
    const chains = detectRemediationChains(steps);
    // Implementation creates separate chains per remediation dep
    expect(chains.length).toBeGreaterThanOrEqual(1);
    const allRemTaskIds = chains.flatMap(c => c.remediationTaskIds);
    expect(allRemTaskIds).toContain('task_fix1');
    expect(allRemTaskIds).toContain('task_fix2');
    expect(chains.some(c => c.isHealed)).toBe(true);
  });

  it('test_detect_when_multiple_originals_should_create_separate_chains', () => {
    const steps: PlanStep[] = [
      makeStep({ taskId: 'task_001', status: 'error' }),
      makeStep({ taskId: 'task_fix_1', isRemediation: true, dependsOn: ['task_001'], status: 'done' }),
      makeStep({ taskId: 'task_002', status: 'error' }),
      makeStep({ taskId: 'task_fix_2', isRemediation: true, dependsOn: ['task_002'], status: 'in_progress' }),
    ];
    const chains = detectRemediationChains(steps);
    expect(chains).toHaveLength(2);
  });

  it('test_detect_when_remediation_has_no_deps_should_skip', () => {
    const steps: PlanStep[] = [
      makeStep({ taskId: 'task_fix', isRemediation: true, dependsOn: undefined }),
    ];
    const chains = detectRemediationChains(steps);
    expect(chains).toHaveLength(0);
  });
});


// ── Tests: dagToPlanSteps ───────────────────────────────────────────────────

describe('dagToPlanSteps (task_006)', () => {
  it('test_dag_to_steps_when_valid_graph_should_convert', () => {
    const tasks: DagTask[] = [
      { id: 'task_001', role: 'pm', goal: 'Plan the project', depends_on: [] },
      { id: 'task_002', role: 'backend_developer', goal: 'Build API', depends_on: ['task_001'] },
    ];
    const graph = makeDagGraph(tasks);
    const taskStatus = { task_001: 'completed' as const, task_002: 'working' as const };
    const steps = dagToPlanSteps(graph, taskStatus, {});
    expect(steps).toHaveLength(2);
    expect(steps[0].taskId).toBe('task_001');
    expect(steps[0].status).toBe('done');
    expect(steps[1].taskId).toBe('task_002');
    expect(steps[1].status).toBe('in_progress');
  });

  it('test_dag_to_steps_when_empty_graph_should_return_empty', () => {
    const graph = makeDagGraph([]);
    const steps = dagToPlanSteps(graph, {}, {});
    expect(steps).toHaveLength(0);
  });

  it('test_dag_to_steps_when_no_tasks_field_should_return_empty', () => {
    const graph: DagGraph = { vision: 'Test' };
    const steps = dagToPlanSteps(graph, {}, {});
    expect(steps).toHaveLength(0);
  });

  it('test_dag_to_steps_when_failed_status_should_map_to_error', () => {
    const tasks: DagTask[] = [
      { id: 'task_001', role: 'backend_developer', goal: 'Build API' },
    ];
    const graph = makeDagGraph(tasks);
    const steps = dagToPlanSteps(graph, { task_001: 'failed' as const }, {});
    expect(steps[0].status).toBe('error');
  });

  it('test_dag_to_steps_when_remediation_should_mark_flag', () => {
    const tasks: DagTask[] = [
      { id: 'task_001', role: 'backend_developer', goal: 'Fix the bug', is_remediation: true },
    ];
    const graph = makeDagGraph(tasks);
    const steps = dagToPlanSteps(graph, {}, {});
    expect(steps[0].isRemediation).toBe(true);
  });

  it('test_dag_to_steps_should_preserve_dependency_links', () => {
    const tasks: DagTask[] = [
      { id: 'task_001', role: 'pm', goal: 'Plan the project' },
      { id: 'task_002', role: 'dev', goal: 'Build feature', depends_on: ['task_001'] },
    ];
    const graph = makeDagGraph(tasks);
    const steps = dagToPlanSteps(graph, {}, {});
    expect(steps[1].dependsOn).toEqual(['task_001']);
  });
});

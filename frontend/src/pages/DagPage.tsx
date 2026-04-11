/**
 * DagPage.tsx — DAG visualization page for a Hivemind project.
 *
 * Features:
 *  - Live polling (3 s interval, default on) with a pulsing green dot indicator
 *  - Replay mode: scrub through historical snapshots from /dag/history
 *  - BFS layout algorithm: roots at top, depth levels spaced vertically
 *  - Custom dagNode type backed by DagNode.tsx
 *  - Empty, loading, and error states
 */

import { useEffect, useCallback, useState, useRef } from 'react';
import { useParams, Link } from 'react-router-dom';
import ReactFlow, {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  MarkerType,
} from 'reactflow';
import type { Node, Edge } from 'reactflow';
import 'reactflow/dist/style.css';

import DagNode from '../components/DagNode';
import type { DagNodeData } from '../components/DagNode';

// ── Node type registration ─────────────────────────────────────────────────

const nodeTypes = { dagNode: DagNode };

// ── API response shapes ────────────────────────────────────────────────────

interface APINode {
  id: string;
  role: string;
  goal: string;
  status: string;
  depends_on: string[];
}

interface APIEdge {
  source: string;
  target: string;
}

interface DAGResponse {
  project_id: string;
  nodes: APINode[];
  edges: APIEdge[];
}

interface DAGSnapshot {
  timestamp: number;
  event_type: string;
  round_num: number;
  nodes: APINode[];
  edges: APIEdge[];
}

interface DAGHistoryResponse {
  project_id: string;
  snapshots: DAGSnapshot[];
}

// ── Status colour palette ──────────────────────────────────────────────────

const STATUS_COLORS: Record<string, string> = {
  pending:     '#4b5563',
  running:     '#3b82f6',
  completed:   '#22c55e',
  failed:      '#ef4444',
  retrying:    '#f59e0b',
  blocked:     '#6b7280',
  remediation: '#a855f7',
};

function statusColor(status: string): string {
  return STATUS_COLORS[status] ?? STATUS_COLORS['pending']!;
}

// ── Layout algorithm ───────────────────────────────────────────────────────

/**
 * BFS from roots → assign depth levels → spread nodes horizontally centred.
 * Returns ReactFlow Node<DagNodeData> objects ready for rendering.
 */
function computeLayout(apiNodes: APINode[], _edges: APIEdge[]): Node<DagNodeData>[] {
  if (apiNodes.length === 0) return [];

  const allIds = new Set(apiNodes.map(n => n.id));

  // Build children adjacency (dep → children that depend on it)
  const childrenMap = new Map<string, string[]>();
  for (const n of apiNodes) {
    for (const dep of n.depends_on) {
      if (!childrenMap.has(dep)) childrenMap.set(dep, []);
      childrenMap.get(dep)!.push(n.id);
    }
  }

  // Root nodes: no known dependencies (or all deps are outside the set)
  const roots = apiNodes.filter(
    n => n.depends_on.length === 0 || n.depends_on.every(d => !allIds.has(d)),
  );

  // BFS to assign levels (max-depth from roots)
  const levelMap = new Map<string, number>();
  for (const r of roots) levelMap.set(r.id, 0);

  const queue: string[] = roots.map(r => r.id);
  const visited = new Set<string>(roots.map(r => r.id));
  let head = 0;

  while (head < queue.length) {
    const current = queue[head++]!;
    const currentLevel = levelMap.get(current) ?? 0;
    for (const child of childrenMap.get(current) ?? []) {
      const existing = levelMap.get(child) ?? -1;
      const proposed = currentLevel + 1;
      if (proposed > existing) levelMap.set(child, proposed);
      if (!visited.has(child)) {
        visited.add(child);
        queue.push(child);
      }
    }
  }

  // Disconnected nodes → level 0
  for (const n of apiNodes) {
    if (!levelMap.has(n.id)) levelMap.set(n.id, 0);
  }

  // Group by level
  const byLevel = new Map<number, APINode[]>();
  for (const n of apiNodes) {
    const lvl = levelMap.get(n.id) ?? 0;
    if (!byLevel.has(lvl)) byLevel.set(lvl, []);
    byLevel.get(lvl)!.push(n);
  }

  // Position: y = level * 150 (as per spec), x spread centred at 0
  const NODE_W = 280;
  const NODE_H = 150;
  const result: Node<DagNodeData>[] = [];

  for (const [level, levelNodes] of byLevel.entries()) {
    const totalWidth = levelNodes.length * NODE_W;
    const startX = -totalWidth / 2;
    levelNodes.forEach((n, colIndex) => {
      result.push({
        id: n.id,
        type: 'dagNode',
        position: {
          x: startX + colIndex * NODE_W,
          y: level * NODE_H,
        },
        data: {
          id: n.id,
          role: n.role,
          goal: n.goal,
          status: n.status,
          depends_on: n.depends_on,
        },
      });
    });
  }

  return result;
}

// ── Edge builder ───────────────────────────────────────────────────────────

function buildEdges(apiEdges: APIEdge[], apiNodes: APINode[]): Edge[] {
  const statusById = new Map<string, string>(apiNodes.map(n => [n.id, n.status]));

  return apiEdges.map((e, i): Edge => {
    const srcStatus = statusById.get(e.source) ?? 'pending';
    const isRunning = srcStatus === 'running';
    const color = statusColor(srcStatus);
    return {
      id: `e-${e.source}-${e.target}-${i}`,
      source: e.source,
      target: e.target,
      type: 'smoothstep',
      animated: isRunning,
      style: { stroke: color, strokeWidth: 1.5 },
      markerEnd: {
        type: MarkerType.ArrowClosed,
        color,
        width: 16,
        height: 16,
      },
    };
  });
}

// ── MiniMap node colour ────────────────────────────────────────────────────

function miniMapNodeColor(node: Node<DagNodeData>): string {
  return statusColor(node.data?.status ?? 'pending');
}

// ── Styles ─────────────────────────────────────────────────────────────────

const S = {
  page: {
    height: '100vh',
    background: 'var(--bg-void, #0a0a0f)',
    display: 'flex',
    flexDirection: 'column' as const,
    fontFamily: 'var(--font-sans, system-ui, sans-serif)',
    overflow: 'hidden',
  },
  header: {
    flexShrink: 0,
    padding: '10px 16px',
    display: 'flex',
    alignItems: 'center',
    gap: 10,
    background: 'var(--bg-panel, #111118)',
    borderBottom: '1px solid var(--border-dim, #27272a)',
    backdropFilter: 'blur(12px)',
  },
  backLink: {
    display: 'inline-flex',
    alignItems: 'center',
    justifyContent: 'center',
    width: 28,
    height: 28,
    borderRadius: 8,
    color: 'var(--text-muted, #6b7280)',
    textDecoration: 'none' as const,
    flexShrink: 0,
    transition: 'background 0.15s ease',
  },
  titleGroup: {
    display: 'flex',
    flexDirection: 'column' as const,
    gap: 0,
    flexShrink: 0,
  },
  title: {
    fontSize: 14,
    fontWeight: 700,
    color: 'var(--text-primary, #f4f4f5)',
    margin: 0,
    lineHeight: 1.2,
  },
  projectId: {
    fontSize: 10,
    color: 'var(--text-muted, #6b7280)',
    fontFamily: 'var(--font-mono, monospace)',
    letterSpacing: '0.04em',
    lineHeight: 1.2,
  },
  spacer: { flex: 1 },
  liveDotWrapper: {
    display: 'inline-flex',
    alignItems: 'center',
    gap: 5,
    fontSize: 10,
    fontWeight: 600,
    letterSpacing: '0.06em',
    textTransform: 'uppercase' as const,
    color: '#22c55e',
    flexShrink: 0,
  },
  btn: {
    fontSize: 12,
    fontWeight: 500,
    padding: '4px 12px',
    borderRadius: 8,
    border: '1px solid var(--border-dim, #27272a)',
    background: 'var(--bg-elevated, #1c1c27)',
    color: 'var(--text-secondary, #9ca3af)',
    cursor: 'pointer',
    flexShrink: 0,
    transition: 'all 0.15s ease',
  },
  btnActive: {
    color: 'var(--accent-blue, #3b82f6)',
    borderColor: 'rgba(59,130,246,0.4)',
    background: 'rgba(59,130,246,0.08)',
  },
  btnReplay: {
    color: '#f59e0b',
    borderColor: 'rgba(245,158,11,0.4)',
    background: 'rgba(245,158,11,0.08)',
  },
  replayBar: {
    flexShrink: 0,
    display: 'flex',
    alignItems: 'center',
    gap: 10,
    padding: '8px 16px',
    background: 'var(--bg-panel, #111118)',
    borderBottom: '1px solid var(--border-dim, #27272a)',
  },
  replayLabel: {
    fontSize: 11,
    color: 'var(--text-muted, #6b7280)',
    fontFamily: 'var(--font-mono, monospace)',
    whiteSpace: 'nowrap' as const,
    flexShrink: 0,
  },
  replayEventLabel: {
    fontSize: 11,
    color: 'var(--accent-blue, #3b82f6)',
    fontFamily: 'var(--font-mono, monospace)',
    whiteSpace: 'nowrap' as const,
    flexShrink: 0,
  },
  rangeInput: { flex: 1, accentColor: 'var(--accent-blue, #3b82f6)', cursor: 'pointer', minWidth: 80 },
  navBtn: {
    fontSize: 11,
    fontWeight: 500,
    padding: '3px 10px',
    borderRadius: 6,
    border: '1px solid var(--border-dim, #27272a)',
    background: 'var(--bg-elevated, #1c1c27)',
    color: 'var(--text-secondary, #9ca3af)',
    cursor: 'pointer',
    flexShrink: 0,
  },
  canvas: { flex: 1, minHeight: 0 },
  center: {
    display: 'flex',
    flexDirection: 'column' as const,
    alignItems: 'center',
    justifyContent: 'center',
    flex: 1,
    gap: 12,
    color: 'var(--text-muted, #6b7280)',
    fontSize: 14,
  },
  retryBtn: {
    fontSize: 12,
    fontWeight: 500,
    padding: '6px 14px',
    borderRadius: 8,
    border: '1px solid rgba(239,68,68,0.3)',
    background: 'rgba(239,68,68,0.08)',
    color: '#ef4444',
    cursor: 'pointer',
  },
  emptyIcon: {
    width: 48,
    height: 48,
    borderRadius: 14,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    background: 'var(--bg-elevated, #1c1c27)',
    border: '1px solid var(--border-dim, #27272a)',
    marginBottom: 8,
  },
} as const;

// ── Component ──────────────────────────────────────────────────────────────

export default function DagPage(): JSX.Element {
  const { projectId } = useParams<{ projectId: string }>();

  // ReactFlow state
  const [rfNodes, setRfNodes, onNodesChange] = useNodesState<DagNodeData>([]);
  const [rfEdges, setRfEdges, onEdgesChange] = useEdgesState([]);

  // UI state
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<'live' | 'replay'>('live');
  const [snapshots, setSnapshots] = useState<DAGSnapshot[]>([]);
  const [snapshotIndex, setSnapshotIndex] = useState<number>(0);

  // Stable ref so the interval callback always sees current mode
  const modeRef = useRef<'live' | 'replay'>('live');
  useEffect(() => { modeRef.current = mode; }, [mode]);

  // ── Fetch live DAG ──────────────────────────────────────────────────────

  const fetchDagData = useCallback(async (): Promise<void> => {
    if (!projectId) return;
    try {
      const res = await fetch(`/api/projects/${encodeURIComponent(projectId)}/dag`);
      if (!res.ok) throw new Error(`Server responded ${res.status}`);
      const data = await res.json() as DAGResponse;
      setRfNodes(computeLayout(data.nodes, data.edges));
      setRfEdges(buildEdges(data.edges, data.nodes));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch DAG');
    } finally {
      setLoading(false);
    }
  }, [projectId, setRfNodes, setRfEdges]);

  // ── Fetch history ───────────────────────────────────────────────────────

  const fetchHistoryData = useCallback(async (): Promise<void> => {
    if (!projectId) return;
    try {
      const res = await fetch(`/api/projects/${encodeURIComponent(projectId)}/dag/history`);
      if (!res.ok) throw new Error(`Server responded ${res.status}`);
      const data = await res.json() as DAGHistoryResponse;
      setSnapshots(data.snapshots);
      if (data.snapshots.length > 0) {
        // Default to the latest snapshot
        setSnapshotIndex(data.snapshots.length - 1);
      }
    } catch (err) {
      console.error('Failed to fetch DAG history:', err);
    }
  }, [projectId]);

  // ── Initial load ────────────────────────────────────────────────────────

  useEffect(() => {
    void fetchDagData();
  }, [fetchDagData]);

  // ── Live polling — only when in live mode ──────────────────────────────

  useEffect(() => {
    if (mode !== 'live') return;
    const id = setInterval(() => {
      if (modeRef.current === 'live') void fetchDagData();
    }, 3000);
    return () => clearInterval(id);
  }, [mode, fetchDagData]);

  // ── Apply snapshot when replay index or snapshots change ───────────────

  useEffect(() => {
    if (mode !== 'replay' || snapshots.length === 0) return;
    const snap = snapshots[snapshotIndex];
    if (!snap) return;
    setRfNodes(computeLayout(snap.nodes, snap.edges));
    setRfEdges(buildEdges(snap.edges, snap.nodes));
  }, [mode, snapshotIndex, snapshots, setRfNodes, setRfEdges]);

  // ── Enter/exit replay mode ──────────────────────────────────────────────

  const handleModeToggle = useCallback((): void => {
    if (mode === 'live') {
      setMode('replay');
      void fetchHistoryData();
    } else {
      setMode('live');
      void fetchDagData();
    }
  }, [mode, fetchHistoryData, fetchDagData]);

  // ── Helpers ─────────────────────────────────────────────────────────────

  function formatTime(ts: number): string {
    return new Date(ts * 1000).toLocaleTimeString([], {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
  }

  const currentSnap: DAGSnapshot | undefined = snapshots[snapshotIndex];
  const hasNodes = rfNodes.length > 0;

  // ── Render ──────────────────────────────────────────────────────────────

  return (
    <div style={S.page}>
      {/* ── Keyframes ── */}
      <style>{`
        @keyframes dagSpin { to { transform: rotate(360deg); } }
        @keyframes livePulse {
          0%, 100% { opacity: 1; transform: scale(1); }
          50% { opacity: 0.4; transform: scale(0.8); }
        }
      `}</style>

      {/* ── Header ── */}
      <header style={S.header}>
        <Link
          to={`/project/${projectId ?? ''}`}
          style={S.backLink}
          aria-label="Back to project"
          onMouseEnter={e => { (e.currentTarget as HTMLAnchorElement).style.background = 'var(--bg-elevated, #1c1c27)'; }}
          onMouseLeave={e => { (e.currentTarget as HTMLAnchorElement).style.background = 'transparent'; }}
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" aria-hidden="true">
            <path d="M15 18l-6-6 6-6"/>
          </svg>
        </Link>

        {/* DAG icon */}
        <svg
          width="14"
          height="14"
          viewBox="0 0 16 16"
          fill="none"
          stroke="var(--accent-blue, #3b82f6)"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
          style={{ flexShrink: 0 }}
        >
          <circle cx="3" cy="8" r="2"/>
          <circle cx="13" cy="3" r="2"/>
          <circle cx="13" cy="13" r="2"/>
          <path d="M5 8h3l2-3M5 8h3l2 3"/>
        </svg>

        <div style={S.titleGroup}>
          <h1 style={S.title}>DAG Visualization</h1>
          {projectId !== undefined && (
            <span style={S.projectId}>{projectId}</span>
          )}
        </div>

        <div style={S.spacer} />

        {/* Pulsing live dot indicator */}
        {mode === 'live' && (
          <span style={S.liveDotWrapper} aria-label="Live updates active">
            <span
              aria-hidden="true"
              style={{
                width: 7,
                height: 7,
                borderRadius: '50%',
                background: '#22c55e',
                display: 'inline-block',
                animation: 'livePulse 1.5s ease-in-out infinite',
              }}
            />
            Live
          </span>
        )}

        {/* Live / Replay toggle button */}
        <button
          style={{
            ...S.btn,
            ...(mode === 'replay' ? S.btnReplay : S.btnActive),
          }}
          onClick={handleModeToggle}
          aria-pressed={mode === 'replay'}
          title={mode === 'live' ? 'Switch to replay mode' : 'Switch to live mode'}
        >
          {mode === 'live' ? 'Replay' : 'Exit Replay'}
        </button>
      </header>

      {/* ── Replay timeline ── */}
      {mode === 'replay' && snapshots.length > 0 && (
        <div style={S.replayBar} role="group" aria-label="Snapshot timeline">
          <button
            style={S.navBtn}
            onClick={() => setSnapshotIndex(i => Math.max(0, i - 1))}
            disabled={snapshotIndex === 0}
            aria-label="Previous snapshot"
          >
            ‹ Prev
          </button>

          <input
            type="range"
            style={S.rangeInput}
            min={0}
            max={snapshots.length - 1}
            value={snapshotIndex}
            onChange={e => setSnapshotIndex(Number(e.target.value))}
            aria-label="Snapshot position"
          />

          <button
            style={S.navBtn}
            onClick={() => setSnapshotIndex(i => Math.min(snapshots.length - 1, i + 1))}
            disabled={snapshotIndex === snapshots.length - 1}
            aria-label="Next snapshot"
          >
            Next ›
          </button>

          {currentSnap !== undefined && (
            <>
              <span style={S.replayEventLabel}>
                {currentSnap.event_type}
              </span>
              <span style={S.replayLabel}>
                round {currentSnap.round_num} · {formatTime(currentSnap.timestamp)}
              </span>
            </>
          )}

          <span style={{ ...S.replayLabel, borderLeft: '1px solid var(--border-dim, #27272a)', paddingLeft: 10 }}>
            {snapshotIndex + 1} / {snapshots.length}
          </span>
        </div>
      )}

      {mode === 'replay' && snapshots.length === 0 && (
        <div style={{ ...S.replayBar, justifyContent: 'center' }}>
          <span style={S.replayLabel}>Loading history…</span>
        </div>
      )}

      {/* ── Loading ── */}
      {loading && (
        <div style={S.center} role="status" aria-live="polite">
          <div style={{
            width: 24,
            height: 24,
            border: '2px solid var(--border-dim, #27272a)',
            borderTopColor: '#3b82f6',
            borderRadius: '50%',
            animation: 'dagSpin 0.8s linear infinite',
          }} aria-hidden="true" />
          <span>Loading DAG…</span>
        </div>
      )}

      {/* ── Error ── */}
      {!loading && error !== null && (
        <div style={S.center} role="alert">
          <svg width="20" height="20" viewBox="0 0 16 16" fill="none" stroke="#ef4444" strokeWidth="1.5" strokeLinecap="round" aria-hidden="true">
            <circle cx="8" cy="8" r="6"/>
            <path d="M8 5v3M8 10.5v.5"/>
          </svg>
          <span style={{ color: '#ef4444' }}>Error: {error}</span>
          <button
            style={S.retryBtn}
            onClick={() => { setLoading(true); void fetchDagData(); }}
          >
            Retry
          </button>
        </div>
      )}

      {/* ── Empty state ── */}
      {!loading && error === null && !hasNodes && (
        <div style={S.center} aria-label="No DAG data">
          <div style={S.emptyIcon} aria-hidden="true">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="var(--text-muted, #6b7280)" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="5" cy="12" r="2.5"/>
              <circle cx="19" cy="5" r="2.5"/>
              <circle cx="19" cy="19" r="2.5"/>
              <path d="M7.5 12h4.5l4-5M7.5 12h4.5l4 5"/>
            </svg>
          </div>
          <span style={{ color: 'var(--text-primary, #f4f4f5)', fontWeight: 600 }}>No DAG data available</span>
          <span style={{ color: 'var(--text-muted, #6b7280)', fontSize: 12 }}>
            This project has no task graph yet. Start a task to generate one.
          </span>
        </div>
      )}

      {/* ── ReactFlow canvas ── */}
      {!loading && error === null && hasNodes && (
        <div style={S.canvas}>
          <ReactFlow
            nodes={rfNodes}
            edges={rfEdges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            nodeTypes={nodeTypes}
            fitView
            fitViewOptions={{ padding: 0.2 }}
            minZoom={0.15}
            maxZoom={2}
            attributionPosition="bottom-left"
          >
            <Background
              variant={BackgroundVariant.Dots}
              color="var(--border-dim, #27272a)"
              gap={20}
              size={1}
            />
            <Controls
              style={{
                background: 'var(--bg-panel, #111118)',
                border: '1px solid var(--border-dim, #27272a)',
                borderRadius: 8,
              }}
            />
            <MiniMap
              nodeColor={miniMapNodeColor}
              maskColor="rgba(10,10,15,0.7)"
              style={{
                background: 'var(--bg-panel, #111118)',
                border: '1px solid var(--border-dim, #27272a)',
                borderRadius: 8,
              }}
            />
          </ReactFlow>
        </div>
      )}
    </div>
  );
}

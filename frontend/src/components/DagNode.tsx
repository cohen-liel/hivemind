/**
 * DagNode.tsx — Custom React Flow node for DAG task visualization.
 *
 * Renders a task node styled by its execution status:
 *   pending     → gray border + muted text
 *   running     → blue border + animated pulse ring
 *   completed   → green border + checkmark
 *   failed      → red border + X mark
 *   retrying    → amber border + spinner
 *   blocked     → dim border + lock icon
 *   remediation → purple border
 */

import { memo } from 'react';
import { Handle, Position } from 'reactflow';
import type { NodeProps } from 'reactflow';

// ── Types ──────────────────────────────────────────────────────────────────

export interface DagNodeData {
  id: string;
  role: string;
  goal: string;
  status: string;
  depends_on: string[];
}

// ── Status config ──────────────────────────────────────────────────────────

interface StatusStyle {
  border: string;
  headerBg: string;
  dotColor: string;
  textColor: string;
  pulse: boolean;
  icon: React.ReactNode;
  label: string;
}

function getStatusStyle(status: string): StatusStyle {
  switch (status) {
    case 'running':
      return {
        border: '1.5px solid #3b82f6',
        headerBg: 'rgba(59,130,246,0.10)',
        dotColor: '#3b82f6',
        textColor: '#93c5fd',
        pulse: true,
        label: 'Running',
        icon: (
          <svg width="11" height="11" viewBox="0 0 16 16" fill="none" aria-hidden="true">
            <circle cx="8" cy="8" r="6" stroke="#3b82f6" strokeWidth="1.5"/>
            <path d="M6 5.5l5 2.5-5 2.5V5.5z" fill="#3b82f6"/>
          </svg>
        ),
      };
    case 'completed':
      return {
        border: '1.5px solid #22c55e',
        headerBg: 'rgba(34,197,94,0.08)',
        dotColor: '#22c55e',
        textColor: '#86efac',
        pulse: false,
        label: 'Done',
        icon: (
          <svg width="11" height="11" viewBox="0 0 16 16" fill="none" aria-hidden="true">
            <circle cx="8" cy="8" r="6" stroke="#22c55e" strokeWidth="1.5"/>
            <path d="M5 8l2 2 4-4" stroke="#22c55e" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
          </svg>
        ),
      };
    case 'failed':
      return {
        border: '1.5px solid #ef4444',
        headerBg: 'rgba(239,68,68,0.08)',
        dotColor: '#ef4444',
        textColor: '#fca5a5',
        pulse: false,
        label: 'Failed',
        icon: (
          <svg width="11" height="11" viewBox="0 0 16 16" fill="none" aria-hidden="true">
            <circle cx="8" cy="8" r="6" stroke="#ef4444" strokeWidth="1.5"/>
            <path d="M5.5 5.5l5 5M10.5 5.5l-5 5" stroke="#ef4444" strokeWidth="1.5" strokeLinecap="round"/>
          </svg>
        ),
      };
    case 'retrying':
      return {
        border: '1.5px solid #f59e0b',
        headerBg: 'rgba(245,158,11,0.08)',
        dotColor: '#f59e0b',
        textColor: '#fcd34d',
        pulse: true,
        label: 'Retrying',
        icon: (
          <svg width="11" height="11" viewBox="0 0 16 16" fill="none" aria-hidden="true">
            <path d="M13 8A5 5 0 112 8" stroke="#f59e0b" strokeWidth="1.5" strokeLinecap="round"/>
            <path d="M13 4v4h-4" stroke="#f59e0b" strokeWidth="1.5" strokeLinecap="round"/>
          </svg>
        ),
      };
    case 'blocked':
      return {
        border: '1.5px solid #6b7280',
        headerBg: 'rgba(107,114,128,0.08)',
        dotColor: '#6b7280',
        textColor: '#9ca3af',
        pulse: false,
        label: 'Blocked',
        icon: (
          <svg width="11" height="11" viewBox="0 0 16 16" fill="none" aria-hidden="true">
            <rect x="4" y="7" width="8" height="6" rx="1" stroke="#6b7280" strokeWidth="1.3"/>
            <path d="M5.5 7V5a2.5 2.5 0 015 0v2" stroke="#6b7280" strokeWidth="1.3" strokeLinecap="round"/>
          </svg>
        ),
      };
    case 'remediation':
      return {
        border: '1.5px solid #a855f7',
        headerBg: 'rgba(168,85,247,0.08)',
        dotColor: '#a855f7',
        textColor: '#d8b4fe',
        pulse: true,
        label: 'Healing',
        icon: (
          <svg width="11" height="11" viewBox="0 0 16 16" fill="none" aria-hidden="true">
            <circle cx="8" cy="8" r="6" stroke="#a855f7" strokeWidth="1.5"/>
            <path d="M8 5v3l2 2" stroke="#a855f7" strokeWidth="1.5" strokeLinecap="round"/>
          </svg>
        ),
      };
    default: // pending
      return {
        border: '1.5px solid #374151',
        headerBg: 'rgba(55,65,81,0.06)',
        dotColor: '#4b5563',
        textColor: '#6b7280',
        pulse: false,
        label: 'Pending',
        icon: (
          <svg width="11" height="11" viewBox="0 0 16 16" fill="none" aria-hidden="true">
            <circle cx="8" cy="8" r="6" stroke="#4b5563" strokeWidth="1.3" strokeDasharray="2 2"/>
          </svg>
        ),
      };
  }
}

// ── Role abbreviations for compact display ─────────────────────────────────

function abbreviateRole(role: string): string {
  const map: Record<string, string> = {
    backend_developer: 'BE',
    frontend_developer: 'FE',
    fullstack_developer: 'FS',
    devops_engineer: 'OPS',
    test_engineer: 'QA',
    documentation_writer: 'DOC',
    security_auditor: 'SEC',
    architect: 'ARC',
    pm: 'PM',
    project_manager: 'PM',
    reviewer: 'REV',
    database_engineer: 'DB',
  };
  return map[role] ?? role.substring(0, 3).toUpperCase();
}

// ── Component ──────────────────────────────────────────────────────────────

const DagNode = memo(function DagNode({ data }: NodeProps<DagNodeData>): JSX.Element {
  const style = getStatusStyle(data.status);
  const abbr = abbreviateRole(data.role);

  return (
    <>
      {/* Target handle (dependency input) */}
      <Handle
        type="target"
        position={Position.Top}
        style={{
          background: style.dotColor,
          border: `2px solid ${style.dotColor}`,
          width: 8,
          height: 8,
          top: -5,
        }}
      />

      {/* Node body */}
      <div
        role="group"
        aria-label={`Task ${data.id}: ${data.role} — ${data.status}`}
        style={{
          minWidth: 160,
          maxWidth: 220,
          border: style.border,
          borderRadius: 10,
          background: 'var(--bg-panel, #111118)',
          boxShadow:
            data.status === 'running'
              ? `0 0 16px ${style.dotColor}40`
              : data.status === 'failed'
              ? `0 0 12px ${style.dotColor}30`
              : '0 2px 8px rgba(0,0,0,0.35)',
          overflow: 'hidden',
          fontFamily: 'var(--font-sans, system-ui, sans-serif)',
          position: 'relative',
        }}
      >
        {/* Animated ring for running tasks */}
        {style.pulse && data.status === 'running' && (
          <div
            aria-hidden="true"
            style={{
              position: 'absolute',
              inset: -3,
              borderRadius: 13,
              border: `1.5px solid ${style.dotColor}`,
              animation: 'dagNodePulse 1.8s ease-in-out infinite',
              pointerEvents: 'none',
            }}
          />
        )}

        {/* Header strip */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 6,
            padding: '6px 10px',
            background: style.headerBg,
            borderBottom: `1px solid ${style.border.replace('1.5px solid ', '')}30`,
          }}
        >
          {/* Role avatar */}
          <div
            aria-hidden="true"
            style={{
              width: 22,
              height: 22,
              borderRadius: 6,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              background: `${style.dotColor}20`,
              color: style.dotColor,
              fontSize: 9,
              fontWeight: 700,
              fontFamily: 'var(--font-mono, monospace)',
              flexShrink: 0,
              letterSpacing: '0.03em',
            }}
          >
            {abbr}
          </div>

          {/* Role name */}
          <span
            style={{
              color: style.textColor,
              fontSize: 10,
              fontWeight: 600,
              fontFamily: 'var(--font-mono, monospace)',
              letterSpacing: '0.04em',
              flex: 1,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {data.role.replace(/_/g, ' ')}
          </span>

          {/* Status icon */}
          <span style={{ flexShrink: 0 }}>{style.icon}</span>
        </div>

        {/* Goal text */}
        <div
          style={{
            padding: '8px 10px',
            fontSize: 11,
            lineHeight: 1.45,
            color: 'var(--text-secondary, #9ca3af)',
            display: '-webkit-box',
            WebkitLineClamp: 3,
            WebkitBoxOrient: 'vertical',
            overflow: 'hidden',
          }}
          title={data.goal}
        >
          {data.goal || '—'}
        </div>

        {/* Footer: task ID + status badge */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '4px 10px 6px',
            borderTop: '1px solid rgba(255,255,255,0.04)',
          }}
        >
          <code
            style={{
              color: 'var(--text-muted, #6b7280)',
              fontSize: 9,
              fontFamily: 'var(--font-mono, monospace)',
              letterSpacing: '0.05em',
            }}
          >
            {data.id}
          </code>
          <span
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 3,
              fontSize: 9,
              fontWeight: 600,
              letterSpacing: '0.06em',
              color: style.dotColor,
              textTransform: 'uppercase',
            }}
          >
            <span
              aria-hidden="true"
              style={{
                width: 5,
                height: 5,
                borderRadius: '50%',
                background: style.dotColor,
                display: 'inline-block',
                animation: style.pulse ? 'dagDot 1.2s ease-in-out infinite' : 'none',
              }}
            />
            {style.label}
          </span>
        </div>
      </div>

      {/* Source handle (dependency output) */}
      <Handle
        type="source"
        position={Position.Bottom}
        style={{
          background: style.dotColor,
          border: `2px solid ${style.dotColor}`,
          width: 8,
          height: 8,
          bottom: -5,
        }}
      />

      {/* Keyframes — injected once per node instance, browser deduplicates */}
      <style>{`
        @keyframes dagNodePulse {
          0%, 100% { opacity: 0.6; transform: scale(1); }
          50% { opacity: 0.15; transform: scale(1.04); }
        }
        @keyframes dagDot {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.3; }
        }
      `}</style>
    </>
  );
});

export default DagNode;

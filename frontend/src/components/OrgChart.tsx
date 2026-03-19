import { useState } from 'react';

/**
 * OrgChart — Visual organizational hierarchy for a Hivemind project.
 *
 * Displays the corporate structure:
 *   CEO (Orchestrator)
 *   ├── CTO (PM)
 *   │   ├── VP Engineering → Frontend, Backend, Database
 *   │   ├── VP Quality → Tester, Security, Reviewer
 *   │   └── VP Research → Researcher, UX
 *   └── VP Operations → DevOps
 */

interface OrgNode {
  title: string;
  executive_title: string;
  agent_role: string | null;
  responsibilities: string[];
  reports_to: string | null;
  direct_reports: string[];
  decision_authority: string[];
  budget_pct: number;
}

interface OrgChartProps {
  orgChart?: Record<string, OrgNode>;
  activeAgents?: Set<string>;
}

const EXEC_ICONS: Record<string, string> = {
  ceo: '\u{1f451}',           // 👑
  cto: '\u{1f9e0}',           // 🧠
  vp_engineering: '\u{2699}\u{fe0f}',  // ⚙️
  vp_quality: '\u{1f6e1}\u{fe0f}',    // 🛡️
  vp_research: '\u{1f50d}',   // 🔍
  vp_operations: '\u{1f680}', // 🚀
};

const AGENT_ICONS: Record<string, string> = {
  orchestrator: '\u{1f3af}',
  pm: '\u{1f9e0}',
  memory: '\u{1f4da}',
  frontend_developer: '\u{1f3a8}',
  backend_developer: '\u{26a1}',
  database_expert: '\u{1f5c4}\u{fe0f}',
  devops: '\u{1f680}',
  security_auditor: '\u{1f510}',
  test_engineer: '\u{1f9ea}',
  reviewer: '\u{1f50d}',
  researcher: '\u{1f50e}',
  ux_critic: '\u{1f3ad}',
};

// Default org chart if none provided
const DEFAULT_ORG: Record<string, OrgNode> = {
  ceo: {
    title: 'Chief Executive Officer',
    executive_title: 'ceo',
    agent_role: 'orchestrator',
    responsibilities: ['Overall project vision', 'Final approval', 'Resource allocation'],
    reports_to: null,
    direct_reports: ['cto', 'vp_operations'],
    decision_authority: ['project_scope', 'release_approval'],
    budget_pct: 5,
  },
  cto: {
    title: 'Chief Technology Officer',
    executive_title: 'cto',
    agent_role: 'pm',
    responsibilities: ['Technical architecture', 'Task decomposition', 'Sprint planning'],
    reports_to: 'ceo',
    direct_reports: ['vp_engineering', 'vp_quality', 'vp_research'],
    decision_authority: ['architecture', 'technology_choice'],
    budget_pct: 10,
  },
  vp_engineering: {
    title: 'VP of Engineering',
    executive_title: 'vp_engineering',
    agent_role: 'memory',
    responsibilities: ['Oversee code-writing agents', 'Code quality standards'],
    reports_to: 'cto',
    direct_reports: ['frontend_developer', 'backend_developer', 'database_expert'],
    decision_authority: ['code_standards', 'merge_approval'],
    budget_pct: 35,
  },
  vp_quality: {
    title: 'VP of Quality',
    executive_title: 'vp_quality',
    agent_role: null,
    responsibilities: ['Quality gates', 'Test coverage', 'Security audits'],
    reports_to: 'cto',
    direct_reports: ['test_engineer', 'security_auditor', 'reviewer'],
    decision_authority: ['quality_gate', 'review_approval'],
    budget_pct: 25,
  },
  vp_research: {
    title: 'VP of Research',
    executive_title: 'vp_research',
    agent_role: null,
    responsibilities: ['Research', 'UX standards', 'Documentation'],
    reports_to: 'cto',
    direct_reports: ['researcher', 'ux_critic'],
    decision_authority: ['research_scope', 'ux_standards'],
    budget_pct: 10,
  },
  vp_operations: {
    title: 'VP of Operations',
    executive_title: 'vp_operations',
    agent_role: null,
    responsibilities: ['Deployment', 'CI/CD', 'Infrastructure'],
    reports_to: 'ceo',
    direct_reports: ['devops'],
    decision_authority: ['deployment_approval'],
    budget_pct: 15,
  },
};

function ExecCard({
  node,
  isActive,
  isExpanded,
  onToggle,
}: {
  node: OrgNode;
  isActive: boolean;
  isExpanded: boolean;
  onToggle: () => void;
}) {
  const icon = EXEC_ICONS[node.executive_title] || '\u{1f4bc}';
  const hasReports = node.direct_reports.length > 0;

  return (
    <button
      onClick={onToggle}
      className="w-full text-left rounded-xl p-3 transition-all duration-200 border"
      style={{
        background: isActive ? 'var(--bg-elevated)' : 'var(--bg-card)',
        borderColor: isActive ? 'var(--border-active)' : 'var(--border-dim)',
        boxShadow: isActive ? '0 0 15px -3px rgba(99, 140, 255, 0.2)' : 'none',
      }}
    >
      <div className="flex items-center gap-2">
        <span className="text-lg">{icon}</span>
        <div className="flex-1 min-w-0">
          <div className="text-xs font-bold truncate" style={{ color: 'var(--text-primary)' }}>
            {node.title}
          </div>
          {node.agent_role && (
            <div className="text-[10px] font-mono" style={{ color: 'var(--accent-blue)' }}>
              {AGENT_ICONS[node.agent_role] || ''} {node.agent_role}
            </div>
          )}
        </div>
        {hasReports && (
          <span
            className="text-[10px] transition-transform duration-200"
            style={{
              color: 'var(--text-muted)',
              transform: isExpanded ? 'rotate(180deg)' : 'rotate(0deg)',
            }}
          >
            ▼
          </span>
        )}
      </div>

      {/* Budget bar */}
      <div className="mt-2 flex items-center gap-2">
        <div className="flex-1 h-1 rounded-full overflow-hidden" style={{ background: 'var(--border-dim)' }}>
          <div
            className="h-full rounded-full"
            style={{
              width: `${node.budget_pct}%`,
              background: 'linear-gradient(90deg, var(--accent-blue), var(--accent-purple))',
            }}
          />
        </div>
        <span className="text-[9px] font-mono" style={{ color: 'var(--text-muted)' }}>
          {node.budget_pct}%
        </span>
      </div>
    </button>
  );
}

function AgentLeaf({ role, isActive }: { role: string; isActive: boolean }) {
  const icon = AGENT_ICONS[role] || '\u{1f527}';
  const label = role.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());

  return (
    <div
      className="flex items-center gap-2 rounded-lg px-3 py-1.5 transition-all duration-200"
      style={{
        background: isActive ? 'var(--glow-green)' : 'var(--bg-card)',
        border: `1px solid ${isActive ? 'rgba(61, 214, 140, 0.3)' : 'var(--border-dim)'}`,
      }}
    >
      {isActive && (
        <span className="w-1.5 h-1.5 rounded-full animate-pulse" style={{ background: 'var(--accent-green)' }} />
      )}
      <span className="text-sm">{icon}</span>
      <span className="text-[10px] font-medium" style={{ color: isActive ? 'var(--accent-green)' : 'var(--text-secondary)' }}>
        {label}
      </span>
    </div>
  );
}

export default function OrgChart({ orgChart, activeAgents = new Set() }: OrgChartProps) {
  const chart = orgChart || DEFAULT_ORG;
  const [expanded, setExpanded] = useState<Set<string>>(new Set(['ceo', 'cto']));

  const toggle = (key: string) => {
    setExpanded(prev => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const isAgentActive = (role: string) => activeAgents.has(role);

  const renderNode = (key: string, depth: number = 0) => {
    const node = chart[key];
    if (!node) return null;

    const isExp = expanded.has(key);
    const isActive = node.agent_role ? isAgentActive(node.agent_role) : false;

    return (
      <div key={key} style={{ marginLeft: depth > 0 ? 16 : 0 }}>
        <ExecCard
          node={node}
          isActive={isActive}
          isExpanded={isExp}
          onToggle={() => toggle(key)}
        />

        {isExp && node.direct_reports.length > 0 && (
          <div className="mt-1.5 space-y-1.5 relative">
            {/* Vertical connector line */}
            <div
              className="absolute left-4 top-0 bottom-0 w-px"
              style={{ background: 'var(--border-dim)' }}
            />

            {node.direct_reports.map(report => {
              if (report in chart) {
                return renderNode(report, depth + 1);
              }
              // Leaf agent
              return (
                <div key={report} style={{ marginLeft: 16 }}>
                  <AgentLeaf role={report} isActive={isAgentActive(report)} />
                </div>
              );
            })}
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="space-y-2 animate-fade-in">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-bold" style={{ color: 'var(--text-primary)' }}>
          Organization Chart
        </h3>
        <button
          onClick={() => {
            if (expanded.size > 0) setExpanded(new Set());
            else setExpanded(new Set(Object.keys(chart)));
          }}
          className="text-[10px] font-mono px-2 py-0.5 rounded"
          style={{ color: 'var(--text-muted)', background: 'var(--bg-elevated)' }}
        >
          {expanded.size > 0 ? 'Collapse All' : 'Expand All'}
        </button>
      </div>

      {renderNode('ceo')}
    </div>
  );
}

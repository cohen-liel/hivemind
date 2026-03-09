import { useMemo } from 'react';
import { AGENT_ICONS, AGENT_LABELS, getAgentAccent } from '../constants';
import type { AgentState, LoopProgress, ActivityEntry } from '../types';

interface Props {
  agents: AgentState[];
  progress: LoopProgress | null;
  activities: ActivityEntry[];
  totalCost: number;
  status: string;
  messageDraft: string; // current typed message (for forecast)
}

// --- Agent accent lookup ---
function accent(name: string) { return getAgentAccent(name); }

// --- Extract artifacts from activities ---
interface Artifact {
  name: string;
  action: 'created' | 'modified' | 'read' | 'ran';
  agent: string;
  timestamp: number;
}

function extractArtifacts(activities: ActivityEntry[]): Artifact[] {
  const seen = new Map<string, Artifact>();
  for (const a of activities) {
    if (a.type !== 'tool_use' || !a.tool_name) continue;
    const desc = a.tool_description || a.tool_name;
    const agent = a.agent || 'agent';
    const tn = a.tool_name.toLowerCase();

    if (tn === 'write' || tn === 'edit') {
      const fileMatch = desc.match(/(?:to |file |path[:\s]+)([^\s,'"]+\.\w+)/i) || desc.match(/([^\s'"]+\.\w{1,6})$/);
      const name = fileMatch ? fileMatch[1].replace(/^.*[/\\]/, '') : desc.slice(0, 40);
      seen.set(name, { name, action: tn === 'write' ? 'created' : 'modified', agent, timestamp: a.timestamp });
    } else if (tn === 'bash') {
      const cmd = desc.slice(0, 50);
      seen.set(`cmd-${a.id}`, { name: cmd, action: 'ran', agent, timestamp: a.timestamp });
    }
  }
  return Array.from(seen.values()).slice(-12); // last 12
}

// --- Forecast engine ---
function estimateForecast(msg: string, agentCount: number): { cost: string; time: string; agents: number } | null {
  if (!msg || msg.length < 5) return null;
  const words = msg.split(/\s+/).length;
  const complexity = Math.min(words / 5, 10); // 1-10 scale
  const agents = Math.min(Math.ceil(complexity / 3), agentCount);
  const cost = (agents * 0.08 * (1 + complexity * 0.1)).toFixed(2);
  const minutes = Math.max(1, Math.round(agents * 0.5 + complexity * 0.3));
  return { cost: `$${cost}`, time: `~${minutes}m`, agents };
}

export default function ConductorMode({ agents, progress, activities, totalCost, status, messageDraft }: Props) {
  const artifacts = useMemo(() => extractArtifacts(activities), [activities]);
  const forecast = useMemo(() => estimateForecast(messageDraft, agents.length), [messageDraft, agents.length]);
  const orchestrator = agents.find(a => a.name === 'orchestrator');
  const workingAgents = agents.filter(a => a.state === 'working');
  const isActive = status === 'running' && workingAgents.length > 0;
  const orchPhase = orchestrator?.task || orchestrator?.current_tool;

  // Progress ring values
  const turnPct = progress ? Math.min((progress.turn / Math.max(progress.max_turns, 1)) * 100, 100) : 0;
  const circumference = 2 * Math.PI * 54; // radius=54
  const strokeOffset = circumference - (turnPct / 100) * circumference;

  // Cost for display
  const costDisplay = totalCost > 0 ? `$${totalCost.toFixed(2)}` : '$0.00';

  // Self-healing: find errors that were followed by a retry
  const healingAgents = useMemo(() => {
    const healing = new Set<string>();
    for (let i = 0; i < activities.length; i++) {
      const a = activities[i];
      if (a.type === 'agent_finished' && a.is_error && a.agent) {
        // Check if next few events include a restart for same agent
        for (let j = i + 1; j < Math.min(i + 5, activities.length); j++) {
          if (activities[j].type === 'agent_started' && activities[j].agent === a.agent) {
            healing.add(a.agent);
            break;
          }
        }
      }
    }
    return healing;
  }, [activities]);

  return (
    <div className="flex flex-col items-center justify-center h-full px-4 py-6 relative overflow-hidden select-none">

      {/* === STATUS RING + CONSTELLATION === */}
      <div className="relative w-40 h-40 mb-5 flex-shrink-0">
        {/* Background ring */}
        <svg className="absolute inset-0 w-full h-full" viewBox="0 0 120 120">
          <circle cx="60" cy="60" r="54" fill="none" stroke="var(--border-dim)" strokeWidth="3" />
          {/* Progress arc */}
          {isActive && (
            <circle cx="60" cy="60" r="54" fill="none"
              stroke="var(--accent-blue)" strokeWidth="3.5"
              strokeLinecap="round"
              strokeDasharray={circumference}
              strokeDashoffset={strokeOffset}
              transform="rotate(-90 60 60)"
              style={{ transition: 'stroke-dashoffset 1s ease-out' }}>
            </circle>
          )}
          {/* Glow ring when active */}
          {isActive && (
            <circle cx="60" cy="60" r="54" fill="none"
              stroke="var(--accent-blue)" strokeWidth="1" opacity="0.3"
              strokeDasharray={circumference}
              strokeDashoffset={strokeOffset}
              transform="rotate(-90 60 60)"
              filter="url(#glow)">
            </circle>
          )}
          <defs>
            <filter id="glow"><feGaussianBlur stdDeviation="3" result="blur"/><feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
          </defs>
        </svg>

        {/* Center content */}
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          {isActive ? (
            <>
              <div className="text-3xl mb-0.5">{AGENT_ICONS.orchestrator}</div>
              <div className="text-[10px] font-bold tracking-wider" style={{ color: 'var(--accent-blue)', fontFamily: 'var(--font-mono)' }}>
                {progress ? `${progress.turn}/${progress.max_turns}` : 'ACTIVE'}
              </div>
              {orchPhase ? (
                <div className="text-[8px] max-w-[90px] text-center truncate animate-pulse"
                  style={{ color: 'var(--accent-blue)', fontFamily: 'var(--font-mono)', opacity: 0.8 }}>
                  {orchPhase}
                </div>
              ) : (
                <div className="text-[9px]" style={{ color: 'var(--text-muted)' }}>{costDisplay}</div>
              )}
            </>
          ) : (
            <>
              <div className="text-3xl mb-1">⚡</div>
              <div className="text-[10px] font-bold tracking-wider" style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
                {status === 'paused' ? 'PAUSED' : 'READY'}
              </div>
            </>
          )}
        </div>

        {/* Agent constellation dots orbiting */}
        {agents.filter(a => a.name !== 'orchestrator').map((agent, i) => {
          const total = agents.filter(a => a.name !== 'orchestrator').length;
          const angle = (i / total) * 360 - 90; // start from top
          const rad = (angle * Math.PI) / 180;
          const orbitR = 68; // px from center
          const cx = 80 + Math.cos(rad) * orbitR; // 80 = center of 160px container
          const cy = 80 + Math.sin(rad) * orbitR;
          const a = accent(agent.name);
          const isWorking = agent.state === 'working';
          const isDone = agent.state === 'done';
          const isHealing = healingAgents.has(agent.name);

          return (
            <div key={agent.name} className="absolute flex flex-col items-center"
              style={{
                left: cx - 14, top: cy - 14,
                width: 28, height: 28,
              }}>
              <div className={`w-7 h-7 rounded-full flex items-center justify-center text-sm transition-all duration-500 ${isWorking ? 'animate-pulse' : ''}`}
                style={{
                  background: isWorking ? a.color + '20' : isDone ? 'rgba(61,214,140,0.12)' : 'var(--bg-elevated)',
                  border: `2px solid ${isWorking ? a.color : isDone ? 'var(--accent-green)' : isHealing ? 'var(--accent-amber)' : 'var(--border-subtle)'}`,
                  boxShadow: isWorking ? `0 0 10px ${a.glow}` : isHealing ? '0 0 8px rgba(245,166,35,0.3)' : 'none',
                  opacity: agent.state === 'idle' ? 0.4 : 1,
                }}>
                <span className="text-xs">{AGENT_ICONS[agent.name] || '🔧'}</span>
              </div>
              {/* Agent name label (only when active) */}
              {(isWorking || isHealing) && (
                <div className="absolute -bottom-3.5 text-[7px] font-bold tracking-wider whitespace-nowrap"
                  style={{ color: isHealing ? 'var(--accent-amber)' : a.color, fontFamily: 'var(--font-mono)' }}>
                  {isHealing ? 'FIXING' : (AGENT_LABELS[agent.name] || agent.name).toUpperCase()}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* === SELF-HEALING BUBBLE === */}
      {healingAgents.size > 0 && (
        <div className="flex items-center gap-2 px-4 py-2 rounded-full mb-3 animate-[fadeSlideIn_0.3s_ease-out]"
          style={{
            background: 'rgba(61,214,140,0.06)',
            border: '1px solid rgba(61,214,140,0.15)',
          }}>
          <div className="w-2 h-2 rounded-full animate-pulse" style={{ background: 'var(--accent-green)' }} />
          <span className="text-xs" style={{ color: 'var(--accent-green)' }}>
            🔧 Auto-resolving issue{healingAgents.size > 1 ? 's' : ''}...
          </span>
        </div>
      )}

      {/* === ARTIFACT ZONE === */}
      <div className="w-full max-w-sm flex-1 min-h-0 overflow-hidden">
        {isActive && artifacts.length > 0 ? (
          /* Live artifact stream */
          <div className="space-y-1.5 overflow-y-auto max-h-full px-1">
            <div className="text-[9px] font-bold tracking-widest mb-2 text-center"
              style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
              ARTIFACTS
            </div>
            {artifacts.map((art, i) => {
              const a = accent(art.agent);
              const isCmd = art.action === 'ran';
              return (
                <div key={`${art.name}-${i}`}
                  className="flex items-center gap-2.5 px-3 py-2 rounded-xl transition-all animate-[fadeSlideIn_0.2s_ease-out]"
                  style={{
                    background: 'var(--bg-card)',
                    border: '1px solid var(--border-dim)',
                    animationDelay: `${i * 40}ms`,
                    animationFillMode: 'backwards',
                  }}>
                  {/* Action icon */}
                  <div className="w-6 h-6 rounded-lg flex items-center justify-center text-[10px] flex-shrink-0"
                    style={{ background: a.color + '15', color: a.color }}>
                    {isCmd ? '▶' : art.action === 'created' ? '+' : '✎'}
                  </div>
                  {/* File/command name */}
                  <div className="flex-1 min-w-0">
                    <div className={`text-xs truncate ${isCmd ? '' : 'font-medium'}`}
                      style={{ color: 'var(--text-primary)', fontFamily: isCmd ? 'var(--font-mono)' : 'var(--font-display)' }}>
                      {art.name}
                    </div>
                    <div className="text-[9px]" style={{ color: 'var(--text-muted)' }}>
                      {art.action} by {AGENT_LABELS[art.agent] || art.agent}
                    </div>
                  </div>
                  {/* Pulse dot for recent */}
                  {i >= artifacts.length - 2 && (
                    <div className="w-1.5 h-1.5 rounded-full animate-pulse flex-shrink-0" style={{ background: a.color }} />
                  )}
                </div>
              );
            })}
          </div>
        ) : !isActive && artifacts.length > 0 ? (
          /* Summary card — last run results */
          <div className="rounded-2xl p-4 text-center animate-[fadeSlideIn_0.3s_ease-out]"
            style={{ background: 'var(--bg-card)', border: '1px solid var(--border-dim)' }}>
            <div className="text-[9px] font-bold tracking-widest mb-3"
              style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
              LAST RUN SUMMARY
            </div>
            <div className="grid grid-cols-3 gap-3 mb-3">
              <div>
                <div className="text-lg font-bold" style={{ color: 'var(--text-primary)' }}>
                  {artifacts.filter(a => a.action === 'created' || a.action === 'modified').length}
                </div>
                <div className="text-[9px]" style={{ color: 'var(--text-muted)' }}>Files</div>
              </div>
              <div>
                <div className="text-lg font-bold" style={{ color: 'var(--text-primary)' }}>
                  {agents.filter(a => a.state === 'done' && a.name !== 'orchestrator').length}
                </div>
                <div className="text-[9px]" style={{ color: 'var(--text-muted)' }}>Agents</div>
              </div>
              <div>
                <div className="text-lg font-bold" style={{ color: 'var(--accent-green)' }}>
                  {costDisplay}
                </div>
                <div className="text-[9px]" style={{ color: 'var(--text-muted)' }}>Cost</div>
              </div>
            </div>
            {/* File list */}
            <div className="space-y-1">
              {artifacts.filter(a => a.action !== 'ran').slice(-5).map((art, i) => (
                <div key={i} className="flex items-center gap-2 px-2 py-1 rounded-lg text-xs"
                  style={{ background: 'var(--bg-elevated)' }}>
                  <span style={{ color: art.action === 'created' ? 'var(--accent-green)' : 'var(--accent-amber)' }}>
                    {art.action === 'created' ? '+' : '✎'}
                  </span>
                  <span className="truncate" style={{ color: 'var(--text-secondary)', fontFamily: 'var(--font-mono)' }}>
                    {art.name}
                  </span>
                </div>
              ))}
            </div>
          </div>
        ) : (
          /* Empty — idle state with network graph */
          <div className="flex flex-col items-center justify-center h-full text-center">
            <svg viewBox="0 0 200 120" fill="none" className="w-48 h-auto mb-4 opacity-40">
              <line x1="100" y1="20" x2="40" y2="65" stroke="var(--border-subtle)" strokeWidth="1" strokeDasharray="4 3">
                <animate attributeName="stroke-dashoffset" from="14" to="0" dur="2s" repeatCount="indefinite"/>
              </line>
              <line x1="100" y1="20" x2="160" y2="65" stroke="var(--border-subtle)" strokeWidth="1" strokeDasharray="4 3">
                <animate attributeName="stroke-dashoffset" from="14" to="0" dur="2.5s" repeatCount="indefinite"/>
              </line>
              <line x1="40" y1="65" x2="80" y2="105" stroke="var(--border-subtle)" strokeWidth="1" strokeDasharray="4 3">
                <animate attributeName="stroke-dashoffset" from="14" to="0" dur="3s" repeatCount="indefinite"/>
              </line>
              <line x1="160" y1="65" x2="120" y2="105" stroke="var(--border-subtle)" strokeWidth="1" strokeDasharray="4 3">
                <animate attributeName="stroke-dashoffset" from="14" to="0" dur="2.2s" repeatCount="indefinite"/>
              </line>
              <circle cx="100" cy="20" r="10" fill="var(--bg-elevated)" stroke="var(--text-muted)" strokeWidth="1"/>
              <text x="100" y="24" textAnchor="middle" fontSize="10">🎯</text>
              <circle cx="40" cy="65" r="8" fill="var(--bg-elevated)" stroke="rgba(99,140,255,0.3)" strokeWidth="1"/>
              <text x="40" y="69" textAnchor="middle" fontSize="9">💻</text>
              <circle cx="160" cy="65" r="8" fill="var(--bg-elevated)" stroke="rgba(167,139,250,0.3)" strokeWidth="1"/>
              <text x="160" y="69" textAnchor="middle" fontSize="9">🔍</text>
              <circle cx="80" cy="105" r="7" fill="var(--bg-elevated)" stroke="rgba(245,166,35,0.3)" strokeWidth="1"/>
              <text x="80" y="109" textAnchor="middle" fontSize="8">🧪</text>
              <circle cx="120" cy="105" r="7" fill="var(--bg-elevated)" stroke="rgba(34,211,238,0.3)" strokeWidth="1"/>
              <text x="120" y="109" textAnchor="middle" fontSize="8">🚀</text>
            </svg>
            <p className="text-xs font-medium" style={{ color: 'var(--text-secondary)' }}>
              Type a task to start
            </p>
          </div>
        )}
      </div>

      {/* === FORECAST BAR ("What If" Engine) === */}
      {forecast && (
        <div className="w-full max-w-sm mt-3 flex-shrink-0 animate-[fadeSlideIn_0.2s_ease-out]">
          <div className="flex items-center justify-between px-4 py-2 rounded-xl"
            style={{
              background: 'rgba(99,140,255,0.04)',
              border: '1px solid rgba(99,140,255,0.1)',
            }}>
            <div className="flex items-center gap-1.5">
              <span className="text-[9px] font-bold tracking-wider" style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
                FORECAST
              </span>
            </div>
            <div className="flex items-center gap-3">
              <div className="flex items-center gap-1">
                <span className="text-[10px]" style={{ color: 'var(--text-muted)' }}>💰</span>
                <span className="text-xs font-bold" style={{ color: 'var(--accent-blue)', fontFamily: 'var(--font-mono)' }}>
                  {forecast.cost}
                </span>
              </div>
              <div className="flex items-center gap-1">
                <span className="text-[10px]" style={{ color: 'var(--text-muted)' }}>⏱</span>
                <span className="text-xs font-bold" style={{ color: 'var(--accent-cyan)', fontFamily: 'var(--font-mono)' }}>
                  {forecast.time}
                </span>
              </div>
              <div className="flex items-center gap-1">
                <span className="text-[10px]" style={{ color: 'var(--text-muted)' }}>👥</span>
                <span className="text-xs font-bold" style={{ color: 'var(--accent-purple)', fontFamily: 'var(--font-mono)' }}>
                  {forecast.agents}
                </span>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

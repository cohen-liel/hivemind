import React, { useEffect, useRef, useState, useMemo, useCallback, memo } from 'react';
import type { ActivityEntry } from '../types';
import { AGENT_ICONS, AGENT_LABELS, formatTime } from '../constants';

type ViewMode = 'detail' | 'summary';

interface Props {
  activities: ActivityEntry[];
  hasMore?: boolean;
  onLoadMore?: () => void;
}

function agentIcon(name?: string): string {
  if (!name) return '\u{1F916}';
  return AGENT_ICONS[name.toLowerCase()] || '\u{1F916}';
}

function agentLabel(name?: string): string {
  if (!name) return 'Agent';
  return AGENT_LABELS[name.toLowerCase()] || AGENT_LABELS[name] || name;
}

// --- Determine who "sent" each message ---
type Sender = 'user' | 'agent' | 'system';

function senderOf(entry: ActivityEntry): Sender {
  if (entry.type === 'user_message') return 'user';
  if (entry.type === 'error') return 'system';
  if (entry.type === 'delegation' || entry.type === 'loop_progress') return 'system';
  if (
    entry.type === 'agent_text' ||
    entry.type === 'tool_use' ||
    entry.type === 'agent_started' ||
    entry.type === 'agent_finished'
  )
    return 'agent';
  return 'system';
}

// --- Group consecutive messages from the same sender+agent ---
interface MessageGroup {
  sender: Sender;
  agent?: string;
  entries: ActivityEntry[];
  /** Stable key derived from first entry ID */
  key: string;
}

function groupBySender(activities: ActivityEntry[]): MessageGroup[] {
  const groups: MessageGroup[] = [];

  for (const entry of activities) {
    const sender = senderOf(entry);
    const agent = sender === 'agent' ? entry.agent || entry.from_agent : undefined;
    const last = groups[groups.length - 1];

    if (last && last.sender === sender && last.agent === agent) {
      last.entries.push(entry);
    } else {
      groups.push({ sender, agent, entries: [entry], key: entry.id });
    }
  }
  return groups;
}

// --- Lightweight markdown rendering (no external deps) ---
// Handles: fenced code blocks, inline code, **bold**, *italic*, and newlines.
function renderInlineMarkdown(text: string, keyPrefix: string): React.ReactNode[] {
  // Split on inline code and bold/italic markers
  const parts = text.split(/(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*)/g);
  return parts.map((part, i) => {
    if (part.startsWith('`') && part.endsWith('`') && part.length > 2) {
      return (
        <code
          key={`${keyPrefix}-ic-${i}`}
          className="px-1 py-0.5 rounded text-[0.8em]"
          style={{
            background: 'rgba(0,0,0,0.35)',
            color: 'var(--accent-cyan)',
            fontFamily: 'var(--font-mono)',
          }}
        >
          {part.slice(1, -1)}
        </code>
      );
    }
    if (part.startsWith('**') && part.endsWith('**') && part.length > 4) {
      return <strong key={`${keyPrefix}-b-${i}`} style={{ color: 'var(--text-primary)', fontWeight: 600 }}>{part.slice(2, -2)}</strong>;
    }
    if (part.startsWith('*') && part.endsWith('*') && part.length > 2) {
      return <em key={`${keyPrefix}-em-${i}`}>{part.slice(1, -1)}</em>;
    }
    // Render plain text with newlines preserved as <br>
    return part.split('\n').map((line, li, arr) => (
      <React.Fragment key={`${keyPrefix}-l-${i}-${li}`}>
        {line}
        {li < arr.length - 1 && <br />}
      </React.Fragment>
    ));
  });
}

// --- Render code blocks and inline markdown inside text ---
function renderContent(text: string): React.ReactNode[] {
  // Split on fenced code blocks first
  const parts = text.split(/(```[\s\S]*?```)/g);
  return parts.map((part, i) => {
    if (part.startsWith('```') && part.endsWith('```')) {
      const inner = part.slice(3, -3);
      const nlIdx = inner.indexOf('\n');
      const lang = nlIdx >= 0 ? inner.slice(0, nlIdx).trim() : '';
      const code = nlIdx >= 0 ? inner.slice(nlIdx + 1) : inner;
      return (
        <pre
          key={i}
          className="rounded-lg p-3 my-1.5 text-xs overflow-x-auto whitespace-pre"
          style={{
            background: 'rgba(0,0,0,0.4)',
            border: '1px solid var(--border-dim)',
            color: 'var(--text-primary)',
            fontFamily: 'var(--font-mono)',
            maxWidth: '100%',
            boxSizing: 'border-box',
            minWidth: 0,
          }}
        >
          {lang && (
            <div className="text-[10px] mb-1.5 uppercase tracking-wide"
              style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-display)' }}>
              {lang}
            </div>
          )}
          {code}
        </pre>
      );
    }
    // For non-code segments, apply inline markdown rendering
    return <React.Fragment key={i}>{renderInlineMarkdown(part, String(i))}</React.Fragment>;
  });
}

// ============================================================
// BUBBLE COMPONENTS (memoized for virtual scroll performance)
// ============================================================

const Avatar = memo(function Avatar({ icon, side }: { icon: string; side: 'left' | 'right' }): React.ReactElement {
  return (
    <div
      className={`w-8 h-8 rounded-full flex items-center justify-center text-sm flex-shrink-0 ${
        side === 'right' ? 'order-last' : ''
      }`}
      style={{ background: 'var(--bg-elevated)' }}
    >
      {icon}
    </div>
  );
});

function AvatarSpacer(): React.ReactElement {
  return <div className="w-8 flex-shrink-0" />;
}

const GroupTimestamp = memo(function GroupTimestamp({ ts, align }: { ts: number; align: 'left' | 'right' | 'center' }): React.ReactElement {
  const justify =
    align === 'right' ? 'justify-end pr-10' : align === 'left' ? 'justify-start pl-10' : 'justify-center';
  return (
    <div className={`flex ${justify} mt-0.5`}>
      <span className="text-[11px] select-none" style={{ color: 'var(--text-muted)' }}>{formatTime(ts)}</span>
    </div>
  );
});

// ---------- Agent text bubble ----------
const AgentTextBubble = memo(function AgentTextBubble({ entry, showAvatar }: { entry: ActivityEntry; showAvatar: boolean }): React.ReactElement {
  const [expanded, setExpanded] = useState(false);
  const content = entry.content || '';
  const isLong = content.length > 300;
  const shown = expanded ? content : content.slice(0, 300);

  return (
    <div className="flex items-end gap-2 animate-[fadeSlideIn_0.3s_ease-out_both]">
      {showAvatar ? <Avatar icon={agentIcon(entry.agent)} side="left" /> : <AvatarSpacer />}
      <div className="max-w-[70%] min-w-[60px] overflow-hidden">
        {showAvatar && entry.agent && (
          <div className="text-[11px] font-medium mb-0.5 ml-1" style={{ color: 'var(--text-muted)' }}>{agentIcon(entry.agent)} {agentLabel(entry.agent)}</div>
        )}
        <div className="rounded-2xl rounded-bl-md px-3.5 py-2.5 text-sm whitespace-pre-wrap break-words leading-relaxed"
          style={{
            background: 'var(--bg-card)',
            color: 'var(--text-primary)',
            border: '1px solid var(--border-dim)',
            boxShadow: '0 2px 8px rgba(0,0,0,0.15)',
          }}>
          {renderContent(shown)}
          {isLong && (
            <button
              onClick={() => setExpanded(!expanded)}
              className="block text-xs mt-1.5 font-medium transition-opacity hover:opacity-80 focus:outline-none focus-visible:ring-2"
              style={{ color: 'var(--accent-blue)' }}
              aria-label={expanded ? 'Show less content' : 'Show more content'}
            >
              {expanded ? 'Show less' : `Show more (${(content.length / 1024).toFixed(1)}KB)`}
            </button>
          )}
        </div>
      </div>
    </div>
  );
});

// ---------- User message bubble ----------
const UserMessageBubble = memo(function UserMessageBubble({ entry, showAvatar }: { entry: ActivityEntry; showAvatar: boolean }): React.ReactElement {
  return (
    <div className="flex items-end gap-2 justify-end animate-[fadeSlideIn_0.3s_ease-out_both]">
      <div className="max-w-[70%] min-w-[60px] overflow-hidden">
        <div className="rounded-2xl rounded-br-md px-3.5 py-2.5 text-sm whitespace-pre-wrap break-words leading-relaxed"
          style={{
            background: 'var(--accent-blue)',
            color: 'white',
            boxShadow: '0 2px 10px var(--glow-blue)',
          }}>
          {entry.content}
        </div>
      </div>
      {showAvatar ? <Avatar icon={'\u{1F464}'} side="right" /> : <AvatarSpacer />}
    </div>
  );
});

// ---------- Error translation ----------
function translateError(raw: string): { title: string; detail: string; actions: ('retry' | 'dismiss')[] } {
  const lower = raw.toLowerCase();
  if (lower.includes('timeout') || lower.includes('timed out')) {
    return { title: 'Agent Timed Out', detail: 'The agent took too long to respond. This often happens with complex tasks.', actions: ['retry', 'dismiss'] };
  }
  if (lower.includes('rate limit') || lower.includes('429') || lower.includes('too many')) {
    return { title: 'Rate Limited', detail: 'Too many requests. The system will automatically retry shortly.', actions: ['dismiss'] };
  }
  if (lower.includes('connection') || lower.includes('network') || lower.includes('fetch')) {
    return { title: 'Connection Lost', detail: 'Could not reach the server. Check your network connection.', actions: ['retry', 'dismiss'] };
  }
  if (lower.includes('budget') || lower.includes('cost') || lower.includes('limit exceeded')) {
    return { title: 'Budget Exceeded', detail: 'The session has reached its spending limit. Adjust in Settings.', actions: ['dismiss'] };
  }
  if (lower.includes('permission') || lower.includes('denied') || lower.includes('access')) {
    return { title: 'Permission Denied', detail: 'The agent doesn\'t have access to perform this action.', actions: ['retry', 'dismiss'] };
  }
  if (lower.includes('exit code') || lower.match(/exit\s*\d+/)) {
    const code = lower.match(/exit\s*(?:code\s*)?(\d+)/);
    const codeNum = code ? parseInt(code[1]) : 0;
    const codeMsg = codeNum === 1 ? 'General error' : codeNum === 127 ? 'Command not found' : codeNum === 137 ? 'Killed (out of memory)' : codeNum === 139 ? 'Segfault' : `Code ${codeNum}`;
    return { title: `Process Failed: ${codeMsg}`, detail: raw, actions: ['retry', 'dismiss'] };
  }
  if (lower.includes('failed to send')) {
    return { title: 'Send Failed', detail: 'The message could not be delivered to the agent.', actions: ['retry', 'dismiss'] };
  }
  return { title: 'Error', detail: raw, actions: ['retry', 'dismiss'] };
}

// ---------- Error bubble (Decision Card) ----------
function ErrorBubble({ entry, onRetry }: { entry: ActivityEntry; onRetry?: () => void }): React.ReactElement | null {
  const translated = translateError(entry.content || 'Unknown error');
  const [dismissed, setDismissed] = useState(false);
  if (dismissed) return null;

  return (
    <div className="flex justify-center animate-[fadeSlideIn_0.3s_ease-out_both] px-4">
      <div className="rounded-2xl w-full max-w-sm overflow-hidden"
        style={{
          background: 'var(--bg-card)',
          border: '1px solid rgba(245,71,91,0.2)',
          boxShadow: '0 4px 20px rgba(245,71,91,0.08)',
        }}>
        {/* Header stripe */}
        <div className="h-1 w-full" style={{ background: 'linear-gradient(90deg, var(--accent-red), var(--accent-amber))' }} />
        <div className="px-4 py-3">
          <div className="flex items-start gap-2.5">
            <div className="w-8 h-8 rounded-xl flex items-center justify-center text-sm flex-shrink-0"
              style={{ background: 'var(--glow-red)' }}>
              ⚠️
            </div>
            <div className="flex-1 min-w-0">
              <h4 className="text-sm font-semibold" style={{ color: 'var(--accent-red)' }}>
                {translated.title}
              </h4>
              <p className="text-xs mt-0.5 leading-relaxed" style={{ color: 'var(--text-muted)' }}>
                {translated.detail.length > 150 ? translated.detail.slice(0, 150) + '…' : translated.detail}
              </p>
            </div>
          </div>
          {/* Action buttons */}
          <div className="flex gap-2 mt-3 justify-end">
            {translated.actions.includes('dismiss') && (
              <button onClick={() => setDismissed(true)}
                className="px-3 py-1.5 text-xs font-medium rounded-lg transition-all active:scale-95 focus:outline-none focus-visible:ring-2"
                style={{ color: 'var(--text-muted)' }}
                aria-label="Dismiss error"
                onMouseEnter={e => { e.currentTarget.style.color = 'var(--text-primary)'; e.currentTarget.style.background = 'var(--bg-elevated)'; }}
                onMouseLeave={e => { e.currentTarget.style.color = 'var(--text-muted)'; e.currentTarget.style.background = 'transparent'; }}
              >
                Dismiss
              </button>
            )}
            {translated.actions.includes('retry') && onRetry && (
              <button onClick={onRetry}
                className="px-3 py-1.5 text-xs font-medium rounded-lg transition-all active:scale-95 focus:outline-none focus-visible:ring-2"
                style={{
                  background: 'var(--glow-red)',
                  color: 'var(--accent-red)',
                  border: '1px solid rgba(245,71,91,0.2)',
                }}
                aria-label="Retry action"
                onMouseEnter={e => { e.currentTarget.style.background = 'rgba(245,71,91,0.2)'; }}
                onMouseLeave={e => { e.currentTarget.style.background = 'var(--glow-red)'; }}
              >
                ↻ Retry
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

// ---------- Tool use bubble ----------
const ToolUseBubble = memo(function ToolUseBubble({ entry, showAvatar }: { entry: ActivityEntry; showAvatar: boolean }): React.ReactElement {
  return (
    <div className="flex items-end gap-2 animate-[fadeSlideIn_0.25s_ease-out_both]">
      {showAvatar ? <Avatar icon={agentIcon(entry.agent)} side="left" /> : <AvatarSpacer />}
      <div className="max-w-[70%] overflow-hidden">
        {showAvatar && entry.agent && (
          <div className="text-[11px] font-medium mb-0.5 ml-1" style={{ color: 'var(--text-muted)' }}>{agentIcon(entry.agent)} {agentLabel(entry.agent)}</div>
        )}
        <div className="rounded-2xl rounded-bl-md px-3 py-2 text-xs flex items-center gap-2"
          style={{
            background: 'var(--bg-card)',
            border: '1px solid var(--border-dim)',
            color: 'var(--text-secondary)',
            fontFamily: 'var(--font-mono)',
          }}>
          <span style={{ color: 'var(--text-muted)' }}>🔧</span>
          <span className="truncate">{entry.tool_description || entry.tool_name}</span>
        </div>
      </div>
    </div>
  );
});

// ---------- Tool group (collapsed) bubble ----------
function ToolGroupBubble({
  agent,
  entries,
  showAvatar,
}: {
  agent: string;
  entries: ActivityEntry[];
  showAvatar: boolean;
}): React.ReactElement {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="flex items-end gap-2 animate-[fadeSlideIn_0.25s_ease-out_both]">
      {showAvatar ? <Avatar icon={agentIcon(agent)} side="left" /> : <AvatarSpacer />}
      <div className="max-w-[70%] overflow-hidden">
        {showAvatar && (
          <div className="text-[11px] font-medium mb-0.5 ml-1" style={{ color: 'var(--text-muted)' }}>{agentIcon(agent)} {agentLabel(agent)}</div>
        )}
        <div className="rounded-2xl rounded-bl-md px-3 py-2 text-xs"
          style={{ background: 'var(--bg-card)', border: '1px solid var(--border-dim)' }}>
          <button
            onClick={() => setExpanded(!expanded)}
            className="flex items-center gap-1.5 w-full transition-colors focus:outline-none focus-visible:ring-2"
            style={{ color: 'var(--text-secondary)' }}
            aria-expanded={expanded}
            aria-label={`${entries.length} tool calls - click to ${expanded ? 'collapse' : 'expand'}`}
          >
            <span style={{ color: 'var(--text-muted)' }}>🔧</span>
            <span style={{ fontFamily: 'var(--font-mono)' }} className="truncate">
              {expanded
                ? `Collapse ${entries.length} tool calls`
                : `${entries.length} tool calls`}
            </span>
            <svg
              className={`w-3 h-3 ml-auto flex-shrink-0 transition-transform ${expanded ? 'rotate-180' : ''}`}
              fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2"
              style={{ color: 'var(--text-muted)' }}
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
            </svg>
          </button>
          {expanded && (
            <div className="mt-1.5 pt-1.5 space-y-0.5"
              style={{ borderTop: '1px solid var(--border-dim)', fontFamily: 'var(--font-mono)', color: 'var(--text-muted)' }}>
              {entries.map((e) => (
                <div key={e.id} className="truncate">
                  {e.tool_description || e.tool_name}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ---------- Agent started bubble ----------
const AgentStartedBubble = memo(function AgentStartedBubble({ entry, showAvatar }: { entry: ActivityEntry; showAvatar: boolean }): React.ReactElement {
  return (
    <div className="flex items-end gap-2 animate-[fadeSlideIn_0.3s_ease-out_both]">
      {showAvatar ? <Avatar icon={agentIcon(entry.agent)} side="left" /> : <AvatarSpacer />}
      <div className="max-w-[70%] overflow-hidden">
        {showAvatar && entry.agent && (
          <div className="text-[11px] font-medium mb-0.5 ml-1" style={{ color: 'var(--text-muted)' }}>{agentIcon(entry.agent)} {agentLabel(entry.agent)}</div>
        )}
        <div className="rounded-2xl rounded-bl-md px-3.5 py-2.5 text-sm"
          style={{ background: 'var(--bg-card)', border: '1px solid var(--border-dim)', color: 'var(--text-primary)' }}>
          <div className="flex items-center gap-2">
            <span className="text-xs" style={{ color: 'var(--accent-green)' }}>▶</span>
            <span>
              <span className="font-medium" style={{ color: 'var(--accent-green)' }}>Started</span>
              {entry.task && <span className="ml-1.5" style={{ color: 'var(--text-secondary)' }}>: {entry.task}</span>}
            </span>
          </div>
        </div>
      </div>
    </div>
  );
});

// ---------- Agent finished bubble ----------
const AgentFinishedBubble = memo(function AgentFinishedBubble({ entry, showAvatar }: { entry: ActivityEntry; showAvatar: boolean }): React.ReactElement {
  const isError = entry.is_error;
  const stats: string[] = [];
  if (entry.turns !== undefined) stats.push(`${entry.turns} turns`);
  if (entry.duration !== undefined) stats.push(`${entry.duration}s`);

  return (
    <div className="flex items-end gap-2 animate-[fadeSlideIn_0.3s_ease-out_both]">
      {showAvatar ? <Avatar icon={agentIcon(entry.agent)} side="left" /> : <AvatarSpacer />}
      <div className="max-w-[70%] overflow-hidden">
        {showAvatar && entry.agent && (
          <div className="text-[11px] font-medium mb-0.5 ml-1" style={{ color: 'var(--text-muted)' }}>{agentIcon(entry.agent)} {agentLabel(entry.agent)}</div>
        )}
        <div className="rounded-2xl rounded-bl-md px-3.5 py-2.5 text-sm"
          style={{
            background: isError ? 'rgba(245,71,91,0.06)' : 'var(--bg-card)',
            border: isError ? '1px solid rgba(245,71,91,0.2)' : '1px solid var(--border-dim)',
            color: 'var(--text-primary)',
          }}>
          <div className="flex items-center gap-2">
            <span className="text-xs" style={{ color: isError ? 'var(--accent-red)' : 'var(--accent-green)' }}>
              {isError ? '\u2718' : '\u2714'}
            </span>
            <span>
              <span className="font-medium" style={{ color: isError ? 'var(--accent-red)' : 'var(--accent-green)' }}>
                {isError ? 'Failed' : 'Finished'}
              </span>
              {stats.length > 0 && (
                <span className="text-xs ml-1.5" style={{ color: 'var(--text-muted)' }}>({stats.join(', ')})</span>
              )}
            </span>
          </div>
          {/* Show failure reason when agent failed */}
          {isError && entry.failure_reason && (
            <div className="mt-2 pt-2 text-xs leading-relaxed"
              style={{ borderTop: '1px solid rgba(245,71,91,0.15)', color: 'var(--text-secondary)' }}>
              <span className="font-semibold" style={{ color: 'var(--accent-red)' }}>Reason: </span>
              {entry.failure_reason.length > 200 ? entry.failure_reason.slice(0, 200) + '\u2026' : entry.failure_reason}
            </div>
          )}
        </div>
      </div>
    </div>
  );
});

// ---------- Delegation bubble (system/center) ----------
const DelegationBubble = memo(function DelegationBubble({ entry }: { entry: ActivityEntry }): React.ReactElement {
  return (
    <div className="flex justify-center animate-[fadeSlideIn_0.3s_ease-out_both]">
      <div className="rounded-2xl px-4 py-2 text-xs inline-flex items-center gap-2"
        style={{
          background: 'var(--glow-blue)',
          border: '1px solid rgba(99,140,255,0.15)',
          color: 'var(--accent-blue)',
        }}>
        <span className="font-medium">{entry.from_agent}</span>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
          style={{ color: 'var(--accent-blue)' }}>
          <path d="M5 12h14M12 5l7 7-7 7" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        <span className="font-medium">{entry.to_agent}</span>
        {entry.task && (
          <span className="ml-0.5 truncate max-w-[200px]" style={{ opacity: 0.7 }}>: {entry.task}</span>
        )}
      </div>
    </div>
  );
});

// ---------- Loop progress bubble (system/center) ----------
const LoopProgressBubble = memo(function LoopProgressBubble({ entry }: { entry: ActivityEntry }): React.ReactElement {
  const loop = entry.loop ?? 0;
  const maxLoops = entry.max_loops ?? 0;
  const turn = entry.turn ?? 0;
  const maxTurns = entry.max_turns ?? 0;

  const turnPct = maxTurns > 0 ? Math.min((turn / maxTurns) * 100, 100) : 0;

  return (
    <div className="flex justify-center animate-[fadeSlideIn_0.2s_ease-out_both]">
      <div className="rounded-full px-4 py-1.5 text-[11px] inline-flex items-center gap-3"
        style={{
          background: 'var(--bg-card)',
          border: '1px solid var(--border-dim)',
          color: 'var(--text-muted)',
          fontFamily: 'var(--font-mono)',
        }}>
        {maxLoops > 0 && (
          <span>Loop {loop}/{maxLoops}</span>
        )}
        {maxTurns > 0 && (
          <span style={{ color: 'var(--accent-blue)' }}>Turn {turn}/{maxTurns}</span>
        )}
        {turnPct > 0 && (
          <div className="w-12 h-1 rounded-full overflow-hidden" style={{ background: 'var(--border-dim)' }}>
            <div className="h-full rounded-full transition-all"
              style={{ width: `${turnPct}%`, background: 'var(--accent-blue)' }} />
          </div>
        )}
      </div>
    </div>
  );
});

// ============================================================
// MEMOIZED MESSAGE GROUP RENDERER
// ============================================================

/** Renders a single message group with all its bubbles */
const MessageGroupRenderer = memo(function MessageGroupRenderer({
  group,
  groupIndex,
}: {
  group: MessageGroup;
  groupIndex: number;
}): React.ReactElement {
  const items: React.ReactElement[] = [];
  let toolAccum: ActivityEntry[] = [];

  const flushTools = (): void => {
    if (toolAccum.length === 0) return;
    if (toolAccum.length === 1) {
      items.push(
        <ToolUseBubble
          key={toolAccum[0].id}
          entry={toolAccum[0]}
          showAvatar={items.length === 0}
        />
      );
    } else {
      items.push(
        <ToolGroupBubble
          key={`tg-${toolAccum[0].id}`}
          agent={group.agent || ''}
          entries={toolAccum}
          showAvatar={items.length === 0}
        />
      );
    }
    toolAccum = [];
  };

  for (const entry of group.entries) {
    if (group.sender === 'agent' && entry.type === 'tool_use') {
      toolAccum.push(entry);
      continue;
    }
    flushTools();

    const showAvatar = items.length === 0;

    switch (entry.type) {
      case 'agent_text':
        items.push(<AgentTextBubble key={entry.id} entry={entry} showAvatar={showAvatar} />);
        break;
      case 'user_message':
        items.push(<UserMessageBubble key={entry.id} entry={entry} showAvatar={showAvatar} />);
        break;
      case 'agent_started':
        items.push(<AgentStartedBubble key={entry.id} entry={entry} showAvatar={showAvatar} />);
        break;
      case 'agent_finished':
        items.push(<AgentFinishedBubble key={entry.id} entry={entry} showAvatar={showAvatar} />);
        break;
      case 'delegation':
        items.push(<DelegationBubble key={entry.id} entry={entry} />);
        break;
      case 'loop_progress':
        items.push(<LoopProgressBubble key={entry.id} entry={entry} />);
        break;
      case 'error':
        items.push(<ErrorBubble key={entry.id} entry={entry} />);
        break;
      default:
        break;
    }
  }
  flushTools();

  const lastTs = group.entries[group.entries.length - 1].timestamp;
  const tsAlign: 'left' | 'right' | 'center' =
    group.sender === 'user' ? 'right' : group.sender === 'agent' ? 'left' : 'center';

  return (
    <div className={`flex flex-col gap-1 ${groupIndex > 0 ? 'mt-4' : ''}`}>
      {items}
      <GroupTimestamp ts={lastTs} align={tsAlign} />
    </div>
  );
}, (prev, next) => {
  // Custom comparator: re-render only when group content actually changes
  if (prev.group.key !== next.group.key) return false;
  if (prev.group.entries.length !== next.group.entries.length) return false;
  if (prev.groupIndex !== next.groupIndex) return false;
  // Check last entry ID to detect appended entries within the group
  const prevLast = prev.group.entries[prev.group.entries.length - 1];
  const nextLast = next.group.entries[next.group.entries.length - 1];
  return prevLast.id === nextLast.id;
});

// ============================================================
// VIRTUAL SCROLL CONSTANTS
// ============================================================

const ESTIMATED_GROUP_HEIGHT = 100;
const OVERSCAN_COUNT = 8;

// ============================================================
// MAIN COMPONENT WITH VIRTUAL SCROLL
// ============================================================

export default function ActivityFeed({ activities, hasMore, onLoadMore }: Props): React.ReactElement {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [viewMode, setViewMode] = useState<ViewMode>('detail');

  // Virtual scroll state
  const [visibleRange, setVisibleRange] = useState<{ start: number; end: number }>({ start: 0, end: 50 });
  const heightCacheRef = useRef<Map<string, number>>(new Map());
  const isNearBottomRef = useRef<boolean>(true);
  const prevActivitiesLenRef = useRef<number>(0);
  const observerRef = useRef<ResizeObserver | null>(null);
  const elementMapRef = useRef<Map<string, HTMLElement>>(new Map());
  // Track whether user has scrolled up to show "new messages" indicator
  const [hasNewBelow, setHasNewBelow] = useState<boolean>(false);

  // Filter activities based on view mode.
  // "Compact" shows only user messages, agent text, and errors — hides
  // tool calls, delegation, loop counters, and agent start/finish noise.
  const filtered = useMemo((): ActivityEntry[] =>
    viewMode === 'summary'
      ? activities.filter(a =>
          a.type === 'user_message' ||
          a.type === 'agent_text' ||
          a.type === 'error'
        )
      : activities,
    [activities, viewMode]
  );

  // Compute message groups with stable keys
  const groups = useMemo((): MessageGroup[] => groupBySender(filtered), [filtered]);

  // Get height for a group from cache or use estimate
  const getGroupHeight = useCallback((groupKey: string): number => {
    return heightCacheRef.current.get(groupKey) ?? ESTIMATED_GROUP_HEIGHT;
  }, []);

  // Setup single ResizeObserver for all group elements
  useEffect(() => {
    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const el = entry.target as HTMLElement;
        const key = el.dataset.groupKey;
        if (key) {
          const height = entry.borderBoxSize?.[0]?.blockSize ?? el.getBoundingClientRect().height;
          heightCacheRef.current.set(key, height);
        }
      }
    });
    observerRef.current = observer;
    return () => observer.disconnect();
  }, []);

  // Ref callback to observe/unobserve group elements
  const groupRefCallback = useCallback((groupKey: string) => (el: HTMLElement | null): void => {
    const observer = observerRef.current;
    if (!observer) return;

    const prevEl = elementMapRef.current.get(groupKey);
    if (prevEl && prevEl !== el) {
      observer.unobserve(prevEl);
      elementMapRef.current.delete(groupKey);
    }

    if (el) {
      el.dataset.groupKey = groupKey;
      observer.observe(el);
      elementMapRef.current.set(groupKey, el);
    }
  }, []);

  // Calculate visible range from scroll position
  const calculateVisibleRange = useCallback((): void => {
    const el = scrollRef.current;
    if (!el || groups.length === 0) {
      setVisibleRange({ start: 0, end: Math.min(groups.length, 50) });
      return;
    }

    const scrollTop = el.scrollTop;
    const containerHeight = el.clientHeight;

    // Track if user is near bottom
    const nearBottom = scrollTop + containerHeight >= el.scrollHeight - 150;
    isNearBottomRef.current = nearBottom;
    if (nearBottom) {
      setHasNewBelow(false);
    }

    // Find first visible group (linear scan — fine for <2000 groups)
    let cumHeight = 0;
    let startIdx = 0;
    while (startIdx < groups.length) {
      const h = getGroupHeight(groups[startIdx].key);
      if (cumHeight + h > scrollTop) break;
      cumHeight += h;
      startIdx++;
    }

    // Find last visible group
    let endIdx = startIdx;
    let visibleHeight = cumHeight;
    while (endIdx < groups.length && visibleHeight < scrollTop + containerHeight) {
      visibleHeight += getGroupHeight(groups[endIdx].key);
      endIdx++;
    }

    // Apply overscan
    const start = Math.max(0, startIdx - OVERSCAN_COUNT);
    const end = Math.min(groups.length, endIdx + OVERSCAN_COUNT);

    setVisibleRange(prev => {
      if (prev.start === start && prev.end === end) return prev;
      return { start, end };
    });
  }, [groups, getGroupHeight]);

  // Attach scroll listener with rAF throttling
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;

    let ticking = false;
    const onScroll = (): void => {
      if (!ticking) {
        ticking = true;
        requestAnimationFrame(() => {
          calculateVisibleRange();
          ticking = false;
        });
      }
    };

    el.addEventListener('scroll', onScroll, { passive: true });

    // Recalculate on container resize
    const containerObserver = new ResizeObserver(() => calculateVisibleRange());
    containerObserver.observe(el);

    // Initial calculation
    calculateVisibleRange();

    return () => {
      el.removeEventListener('scroll', onScroll);
      containerObserver.disconnect();
    };
  }, [calculateVisibleRange]);

  // Recalculate when groups change
  useEffect(() => {
    calculateVisibleRange();
  }, [groups.length, calculateVisibleRange]);

  // Scroll to bottom on initial mount so the user always sees the latest messages
  const initialScrollDone = useRef(false);
  useEffect(() => {
    if (!initialScrollDone.current && groups.length > 0) {
      initialScrollDone.current = true;
      requestAnimationFrame(() => {
        const el = scrollRef.current;
        if (el) {
          el.scrollTop = el.scrollHeight;
          isNearBottomRef.current = true;
        }
      });
    }
  }, [groups.length]);

  // Auto-scroll to bottom when new events arrive (only if user was near bottom)
  useEffect(() => {
    if (activities.length > prevActivitiesLenRef.current) {
      if (isNearBottomRef.current) {
        // Double-rAF to account for virtual scroll re-rendering
        requestAnimationFrame(() => {
          const el = scrollRef.current;
          if (el) {
            el.scrollTop = el.scrollHeight;
            requestAnimationFrame(() => {
              el.scrollTop = el.scrollHeight;
            });
          }
        });
      } else {
        // User has scrolled up — show "new messages" indicator
        setHasNewBelow(true);
      }
    }
    prevActivitiesLenRef.current = activities.length;
  }, [activities.length]);

  // Scroll-to-bottom handler for the indicator button
  const scrollToBottom = useCallback((): void => {
    const el = scrollRef.current;
    if (el) {
      // Use instant scroll for "New messages" button — smooth scroll can feel
      // sluggish when there are many messages between current position and bottom.
      // Double-rAF ensures we stay at the bottom even after React re-renders
      // the virtual scroll groups (which may change total scrollHeight).
      el.scrollTop = el.scrollHeight;
      isNearBottomRef.current = true;
      setHasNewBelow(false);
      requestAnimationFrame(() => {
        el.scrollTop = el.scrollHeight;
        requestAnimationFrame(() => {
          el.scrollTop = el.scrollHeight;
        });
      });
    }
  }, []);

  // Calculate spacer heights for off-screen groups
  // Clamp visibleRange to current groups length (groups can shrink when
  // MAX_ACTIVITIES cap drops oldest entries during rapid streaming).
  const safeStart = Math.min(visibleRange.start, groups.length);
  const safeEnd = Math.min(visibleRange.end, groups.length);

  let offsetBefore = 0;
  for (let i = 0; i < safeStart; i++) {
    offsetBefore += getGroupHeight(groups[i].key);
  }
  let offsetAfter = 0;
  for (let i = safeEnd; i < groups.length; i++) {
    offsetAfter += getGroupHeight(groups[i].key);
  }

  // Empty state
  if (activities.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-sm px-4">
        <div className="w-14 h-14 rounded-2xl flex items-center justify-center mb-3"
          style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border-dim)' }}>
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="var(--accent-blue)" strokeWidth="1.5" strokeLinecap="round" style={{ opacity: 0.6 }}>
            <path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/>
            <line x1="9" y1="9" x2="15" y2="9" opacity="0.4"/>
            <line x1="9" y1="13" x2="12" y2="13" opacity="0.3"/>
          </svg>
        </div>
        <p className="text-sm font-semibold" style={{ color: 'var(--text-secondary)' }}>No messages yet</p>
        <p className="text-xs mt-1" style={{ color: 'var(--text-muted)' }}>Send a message to get started</p>
      </div>
    );
  }

  return (
    <div className="relative h-full">
      <div
        ref={scrollRef}
        id="activity-scroll-container"
        className="flex flex-col h-full overflow-y-auto overflow-x-hidden p-4"
        style={{ wordBreak: 'break-word', overflowWrap: 'anywhere' }}
        role="log"
        aria-label="Activity feed"
        aria-live="polite"
      >
        {/* View mode toggle + freshness badge */}
        <div className="flex items-center justify-between mb-2 sticky top-0 z-10">
          {/* Freshness: pulse when last activity < 30s ago */}
          {(() => {
            const lastTs = activities[activities.length - 1]?.timestamp;
            const isLive = lastTs && (Date.now() - lastTs) < 30000;
            return isLive ? (
              <div className="flex items-center gap-1">
                <span className="w-1.5 h-1.5 rounded-full animate-pulse" style={{ background: 'var(--accent-green)' }} />
                <span className="text-[9px] font-bold tracking-widest" style={{ color: 'var(--accent-green)', fontFamily: 'var(--font-mono)' }}>LIVE</span>
              </div>
            ) : <div />;
          })()}
          <div className="rounded-full p-0.5 flex gap-0.5"
            style={{ background: 'var(--bg-panel)', border: '1px solid var(--border-dim)', backdropFilter: 'blur(8px)' }}>
            <button
              onClick={() => setViewMode('summary')}
              className="px-2.5 py-1 rounded-full text-[10px] font-medium transition-colors focus:outline-none focus-visible:ring-2"
              style={{
                background: viewMode === 'summary' ? 'var(--bg-elevated)' : 'transparent',
                color: viewMode === 'summary' ? 'var(--text-primary)' : 'var(--text-muted)',
              }}
              aria-label="Compact view — text only"
              aria-pressed={viewMode === 'summary'}
              title="Show only messages (hide tool calls and system events)"
            >
              Compact
            </button>
            <button
              onClick={() => setViewMode('detail')}
              className="px-2.5 py-1 rounded-full text-[10px] font-medium transition-colors focus:outline-none focus-visible:ring-2"
              style={{
                background: viewMode === 'detail' ? 'var(--bg-elevated)' : 'transparent',
                color: viewMode === 'detail' ? 'var(--text-primary)' : 'var(--text-muted)',
              }}
              aria-label="Full detail view"
              aria-pressed={viewMode === 'detail'}
              title="Show all events including tool calls, delegations, and system events"
            >
              Full
            </button>
          </div>
        </div>

        {/* Load earlier messages */}
        {hasMore && onLoadMore && (
          <div className="flex justify-center mb-3">
            <button
              onClick={onLoadMore}
              className="px-3 py-1.5 text-xs rounded-lg transition-colors focus:outline-none focus-visible:ring-2"
              style={{
                color: 'var(--text-muted)',
                background: 'var(--bg-panel)',
                border: '1px solid var(--border-dim)',
              }}
              aria-label="Load earlier messages"
            >
              Load earlier messages
            </button>
          </div>
        )}

        {/* Virtual scroll: spacer before visible groups */}
        {offsetBefore > 0 && (
          <div style={{ height: offsetBefore, flexShrink: 0 }} aria-hidden="true" />
        )}

        {/* Render only visible groups */}
        {groups.slice(safeStart, safeEnd).map((group, i) => {
          const gi = safeStart + i;
          return (
            <div
              key={group.key}
              ref={groupRefCallback(group.key)}
              style={{ contentVisibility: 'auto', containIntrinsicSize: 'auto 100px' }}
            >
              <MessageGroupRenderer
                group={group}
                groupIndex={gi}
              />
            </div>
          );
        })}

        {/* Virtual scroll: spacer after visible groups */}
        {offsetAfter > 0 && (
          <div style={{ height: offsetAfter, flexShrink: 0 }} aria-hidden="true" />
        )}
      </div>

      {/* Scroll to bottom indicator — shown when user scrolled up and new messages arrived */}
      {hasNewBelow && (
        <button
          onClick={scrollToBottom}
          className="absolute bottom-4 right-4 flex items-center gap-1.5 px-3 py-2 rounded-full transition-all active:scale-90 z-20 focus:outline-none focus-visible:ring-2 animate-[fadeSlideIn_0.2s_ease-out]"
          style={{
            background: 'var(--accent-blue)',
            color: 'white',
            boxShadow: '0 4px 15px rgba(99,140,255,0.4)',
          }}
          aria-label="Scroll to latest messages"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
            <path d="M12 5v14M5 12l7 7 7-7" />
          </svg>
          <span className="text-xs font-medium">New messages</span>
        </button>
      )}
    </div>
  );
}

import { useEffect, useRef, useState } from 'react';
import type { ActivityEntry } from '../types';
import { AGENT_ICONS, formatTime } from '../constants';

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

// --- Determine who "sent" each message ---
type Sender = 'user' | 'agent' | 'system';

function senderOf(entry: ActivityEntry): Sender {
  if (entry.type === 'user_message') return 'user';
  if (entry.type === 'error') return 'system';
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
      groups.push({ sender, agent, entries: [entry] });
    }
  }
  return groups;
}

// --- Render code blocks inside text ---
function renderContent(text: string) {
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
    return <span key={i}>{part}</span>;
  });
}

// ============================================================
// BUBBLE COMPONENTS
// ============================================================

function Avatar({ icon, side }: { icon: string; side: 'left' | 'right' }) {
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
}

function AvatarSpacer() {
  return <div className="w-8 flex-shrink-0" />;
}

function GroupTimestamp({ ts, align }: { ts: number; align: 'left' | 'right' | 'center' }) {
  const justify =
    align === 'right' ? 'justify-end pr-10' : align === 'left' ? 'justify-start pl-10' : 'justify-center';
  return (
    <div className={`flex ${justify} mt-0.5`}>
      <span className="text-[11px] select-none" style={{ color: 'var(--text-muted)' }}>{formatTime(ts)}</span>
    </div>
  );
}

// ---------- Agent text bubble ----------
function AgentTextBubble({ entry, showAvatar }: { entry: ActivityEntry; showAvatar: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const content = entry.content || '';
  const isLong = content.length > 300;
  const shown = expanded ? content : content.slice(0, 300);

  return (
    <div className="flex items-end gap-2 animate-[fadeSlideIn_0.3s_ease-out_both]">
      {showAvatar ? <Avatar icon={agentIcon(entry.agent)} side="left" /> : <AvatarSpacer />}
      <div className="max-w-[70%] min-w-[60px] overflow-hidden">
        {showAvatar && entry.agent && (
          <div className="text-[11px] font-medium mb-0.5 ml-1" style={{ color: 'var(--text-muted)' }}>{entry.agent}</div>
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
              className="block text-xs mt-1.5 font-medium transition-opacity hover:opacity-80"
              style={{ color: 'var(--accent-blue)' }}
            >
              {expanded ? 'Show less' : `Show more (${(content.length / 1024).toFixed(1)}KB)`}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

// ---------- User message bubble ----------
function UserMessageBubble({ entry, showAvatar }: { entry: ActivityEntry; showAvatar: boolean }) {
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
}

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
function ErrorBubble({ entry, onRetry }: { entry: ActivityEntry; onRetry?: () => void }) {
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
                className="px-3 py-1.5 text-xs font-medium rounded-lg transition-all active:scale-95"
                style={{ color: 'var(--text-muted)' }}
                onMouseEnter={e => { e.currentTarget.style.color = 'var(--text-primary)'; e.currentTarget.style.background = 'var(--bg-elevated)'; }}
                onMouseLeave={e => { e.currentTarget.style.color = 'var(--text-muted)'; e.currentTarget.style.background = 'transparent'; }}
              >
                Dismiss
              </button>
            )}
            {translated.actions.includes('retry') && onRetry && (
              <button onClick={onRetry}
                className="px-3 py-1.5 text-xs font-medium rounded-lg transition-all active:scale-95"
                style={{
                  background: 'var(--glow-red)',
                  color: 'var(--accent-red)',
                  border: '1px solid rgba(245,71,91,0.2)',
                }}
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
function ToolUseBubble({ entry, showAvatar }: { entry: ActivityEntry; showAvatar: boolean }) {
  return (
    <div className="flex items-end gap-2 animate-[fadeSlideIn_0.25s_ease-out_both]">
      {showAvatar ? <Avatar icon={agentIcon(entry.agent)} side="left" /> : <AvatarSpacer />}
      <div className="max-w-[70%] overflow-hidden">
        {showAvatar && entry.agent && (
          <div className="text-[11px] font-medium mb-0.5 ml-1" style={{ color: 'var(--text-muted)' }}>{entry.agent}</div>
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
}

// ---------- Tool group (collapsed) bubble ----------
function ToolGroupBubble({
  agent,
  entries,
  showAvatar,
}: {
  agent: string;
  entries: ActivityEntry[];
  showAvatar: boolean;
}) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="flex items-end gap-2 animate-[fadeSlideIn_0.25s_ease-out_both]">
      {showAvatar ? <Avatar icon={agentIcon(agent)} side="left" /> : <AvatarSpacer />}
      <div className="max-w-[70%] overflow-hidden">
        {showAvatar && (
          <div className="text-[11px] font-medium mb-0.5 ml-1" style={{ color: 'var(--text-muted)' }}>{agent}</div>
        )}
        <div className="rounded-2xl rounded-bl-md px-3 py-2 text-xs"
          style={{ background: 'var(--bg-card)', border: '1px solid var(--border-dim)' }}>
          <button
            onClick={() => setExpanded(!expanded)}
            className="flex items-center gap-1.5 w-full transition-colors"
            style={{ color: 'var(--text-secondary)' }}
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
function AgentStartedBubble({ entry, showAvatar }: { entry: ActivityEntry; showAvatar: boolean }) {
  return (
    <div className="flex items-end gap-2 animate-[fadeSlideIn_0.3s_ease-out_both]">
      {showAvatar ? <Avatar icon={agentIcon(entry.agent)} side="left" /> : <AvatarSpacer />}
      <div className="max-w-[70%] overflow-hidden">
        {showAvatar && entry.agent && (
          <div className="text-[11px] font-medium mb-0.5 ml-1" style={{ color: 'var(--text-muted)' }}>{entry.agent}</div>
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
}

// ---------- Agent finished bubble ----------
function AgentFinishedBubble({ entry, showAvatar }: { entry: ActivityEntry; showAvatar: boolean }) {
  const isError = entry.is_error;
  const stats: string[] = [];
  if (entry.cost !== undefined) stats.push(`$${entry.cost.toFixed(4)}`);
  if (entry.turns !== undefined) stats.push(`${entry.turns} turns`);
  if (entry.duration !== undefined) stats.push(`${entry.duration}s`);

  return (
    <div className="flex items-end gap-2 animate-[fadeSlideIn_0.3s_ease-out_both]">
      {showAvatar ? <Avatar icon={agentIcon(entry.agent)} side="left" /> : <AvatarSpacer />}
      <div className="max-w-[70%] overflow-hidden">
        {showAvatar && entry.agent && (
          <div className="text-[11px] font-medium mb-0.5 ml-1" style={{ color: 'var(--text-muted)' }}>{entry.agent}</div>
        )}
        <div className="rounded-2xl rounded-bl-md px-3.5 py-2.5 text-sm"
          style={{ background: 'var(--bg-card)', border: '1px solid var(--border-dim)', color: 'var(--text-primary)' }}>
          <div className="flex items-center gap-2">
            <span className="text-xs" style={{ color: isError ? 'var(--accent-red)' : 'var(--accent-green)' }}>
              {isError ? '✘' : '✔'}
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
        </div>
      </div>
    </div>
  );
}

// ---------- Delegation bubble (system/center) ----------
function DelegationBubble({ entry }: { entry: ActivityEntry }) {
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
}

// ============================================================
// MAIN COMPONENT
// ============================================================

export default function ActivityFeed({ activities, hasMore, onLoadMore }: Props) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const [viewMode, setViewMode] = useState<ViewMode>('detail');

  // Auto-scroll to bottom when new messages arrive
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [activities.length]);

  // Filter activities based on view mode
  const filtered = viewMode === 'summary'
    ? activities.filter(a =>
        a.type === 'user_message' ||
        a.type === 'delegation' ||
        a.type === 'agent_text' ||
        a.type === 'agent_finished' ||
        a.type === 'error'
      )
    : activities;

  // Empty state
  if (activities.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-sm px-4">
        <div className="w-14 h-14 rounded-full flex items-center justify-center mb-3 text-2xl"
          style={{ background: 'var(--bg-elevated)' }}>
          💬
        </div>
        <p className="font-medium" style={{ color: 'var(--text-secondary)' }}>No messages yet</p>
        <p className="text-xs mt-1" style={{ color: 'var(--text-muted)' }}>Send a message to get started</p>
      </div>
    );
  }

  const groups = groupBySender(filtered);

  return (
    <div
      ref={scrollRef}
      className="flex flex-col h-full overflow-y-auto overflow-x-hidden p-4 scroll-smooth"
      style={{ wordBreak: 'break-word', overflowWrap: 'anywhere' }}
    >
      {/* View mode toggle */}
      <div className="flex justify-end mb-2 sticky top-0 z-10">
        <div className="rounded-full p-0.5 flex gap-0.5"
          style={{ background: 'var(--bg-panel)', border: '1px solid var(--border-dim)', backdropFilter: 'blur(8px)' }}>
          <button
            onClick={() => setViewMode('summary')}
            className="px-2.5 py-1 rounded-full text-[10px] font-medium transition-colors"
            style={{
              background: viewMode === 'summary' ? 'var(--bg-elevated)' : 'transparent',
              color: viewMode === 'summary' ? 'var(--text-primary)' : 'var(--text-muted)',
            }}
          >
            Summary
          </button>
          <button
            onClick={() => setViewMode('detail')}
            className="px-2.5 py-1 rounded-full text-[10px] font-medium transition-colors"
            style={{
              background: viewMode === 'detail' ? 'var(--bg-elevated)' : 'transparent',
              color: viewMode === 'detail' ? 'var(--text-primary)' : 'var(--text-muted)',
            }}
          >
            Detail
          </button>
        </div>
      </div>
      {/* Load earlier messages */}
      {hasMore && onLoadMore && (
        <div className="flex justify-center mb-3">
          <button
            onClick={onLoadMore}
            className="px-3 py-1.5 text-xs rounded-lg transition-colors"
            style={{
              color: 'var(--text-muted)',
              background: 'var(--bg-panel)',
              border: '1px solid var(--border-dim)',
            }}
          >
            Load earlier messages
          </button>
        </div>
      )}
      {groups.map((group, gi) => {
        const items: JSX.Element[] = [];
        let toolAccum: ActivityEntry[] = [];

        const flushTools = () => {
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
            case 'error':
              items.push(<ErrorBubble key={entry.id} entry={entry} />);
              break;
            default:
              break;
          }
        }
        flushTools();

        const lastTs = group.entries[group.entries.length - 1].timestamp;
        const tsAlign =
          group.sender === 'user' ? 'right' : group.sender === 'agent' ? 'left' : 'center';

        return (
          <div key={`g-${gi}`} className={`flex flex-col gap-1 ${gi > 0 ? 'mt-4' : ''}`}>
            {items}
            <GroupTimestamp ts={lastTs} align={tsAlign as 'left' | 'right' | 'center'} />
          </div>
        );
      })}
      <div ref={endRef} />
    </div>
  );
}

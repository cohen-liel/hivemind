import { useEffect, useRef, useState } from 'react';
import type { ActivityEntry } from '../types';

type ViewMode = 'detail' | 'summary';

interface Props {
  activities: ActivityEntry[];
  hasMore?: boolean;
  onLoadMore?: () => void;
}

// --- Agent icon mapping (same as AgentStatusPanel) ---
const AGENT_ICONS: Record<string, string> = {
  orchestrator: '\u{1F3AF}',
  developer: '\u{1F4BB}',
  reviewer: '\u{1F50D}',
  tester: '\u{1F9EA}',
  devops: '\u{2699}\uFE0F',
};

function agentIcon(name?: string): string {
  if (!name) return '\u{1F916}';
  return AGENT_ICONS[name.toLowerCase()] || '\u{1F916}';
}

// --- Determine who "sent" each message ---
type Sender = 'user' | 'agent' | 'system';

function senderOf(entry: ActivityEntry): Sender {
  if (entry.type === 'user_message') return 'user';
  if (
    entry.type === 'agent_text' ||
    entry.type === 'tool_use' ||
    entry.type === 'agent_started' ||
    entry.type === 'agent_finished'
  )
    return 'agent';
  return 'system'; // delegation, loop_progress
}

// --- Group consecutive messages from the same sender+agent ---
interface MessageGroup {
  sender: Sender;
  agent?: string; // agent name (for avatar); undefined for user/system
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

// --- Time formatting ---
function formatTime(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
  });
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
          className="bg-black/40 rounded-lg p-3 my-1.5 text-xs font-mono overflow-x-auto whitespace-pre text-gray-200 border border-white/5"
        >
          {lang && (
            <div className="text-[10px] text-gray-500 mb-1.5 uppercase tracking-wide font-sans">
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

/** Small circular avatar */
function Avatar({ icon, side }: { icon: string; side: 'left' | 'right' }) {
  return (
    <div
      className={`w-8 h-8 rounded-full bg-gray-700 flex items-center justify-center text-sm flex-shrink-0 ${
        side === 'right' ? 'order-last' : ''
      }`}
    >
      {icon}
    </div>
  );
}

/** Invisible spacer matching avatar width (for grouped messages without avatar) */
function AvatarSpacer() {
  return <div className="w-8 flex-shrink-0" />;
}

/** Timestamp shown below a group */
function GroupTimestamp({ ts, align }: { ts: number; align: 'left' | 'right' | 'center' }) {
  const justify =
    align === 'right' ? 'justify-end pr-10' : align === 'left' ? 'justify-start pl-10' : 'justify-center';
  return (
    <div className={`flex ${justify} mt-0.5`}>
      <span className="text-[11px] text-gray-500 select-none">{formatTime(ts)}</span>
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
      <div className="max-w-[70%] min-w-[60px]">
        {showAvatar && entry.agent && (
          <div className="text-[11px] text-gray-500 font-medium mb-0.5 ml-1">{entry.agent}</div>
        )}
        <div className="bg-gray-800 text-gray-100 rounded-2xl rounded-bl-md px-3.5 py-2.5 text-sm whitespace-pre-wrap break-words leading-relaxed shadow-md">
          {renderContent(shown)}
          {isLong && (
            <button
              onClick={() => setExpanded(!expanded)}
              className="block text-xs text-blue-400 hover:text-blue-300 mt-1.5 font-medium"
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
      <div className="max-w-[70%] min-w-[60px]">
        <div className="bg-blue-600 text-white rounded-2xl rounded-br-md px-3.5 py-2.5 text-sm whitespace-pre-wrap break-words leading-relaxed shadow-md">
          {entry.content}
        </div>
      </div>
      {showAvatar ? <Avatar icon={'\u{1F464}'} side="right" /> : <AvatarSpacer />}
    </div>
  );
}

// ---------- Tool use bubble ----------
function ToolUseBubble({ entry, showAvatar }: { entry: ActivityEntry; showAvatar: boolean }) {
  return (
    <div className="flex items-end gap-2 animate-[fadeSlideIn_0.25s_ease-out_both]">
      {showAvatar ? <Avatar icon={agentIcon(entry.agent)} side="left" /> : <AvatarSpacer />}
      <div className="max-w-[70%]">
        {showAvatar && entry.agent && (
          <div className="text-[11px] text-gray-500 font-medium mb-0.5 ml-1">{entry.agent}</div>
        )}
        <div className="bg-gray-800/70 text-gray-400 rounded-2xl rounded-bl-md px-3 py-2 text-xs font-mono flex items-center gap-2 shadow-sm border border-gray-700/50">
          <span className="text-gray-500">{'\u{1F527}'}</span>
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
      <div className="max-w-[70%]">
        {showAvatar && (
          <div className="text-[11px] text-gray-500 font-medium mb-0.5 ml-1">{agent}</div>
        )}
        <div className="bg-gray-800/70 rounded-2xl rounded-bl-md px-3 py-2 text-xs shadow-sm border border-gray-700/50">
          <button
            onClick={() => setExpanded(!expanded)}
            className="text-gray-400 hover:text-gray-300 flex items-center gap-1.5 w-full"
          >
            <span className="text-gray-500">{'\u{1F527}'}</span>
            <span className="font-mono truncate">
              {expanded
                ? `Collapse ${entries.length} tool calls`
                : `${entries.length} tool calls`}
            </span>
            <svg
              className={`w-3 h-3 ml-auto flex-shrink-0 text-gray-600 transition-transform ${
                expanded ? 'rotate-180' : ''
              }`}
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth="2"
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
            </svg>
          </button>
          {expanded && (
            <div className="mt-1.5 pt-1.5 border-t border-gray-700/50 space-y-0.5 font-mono text-gray-500">
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
      <div className="max-w-[70%]">
        {showAvatar && entry.agent && (
          <div className="text-[11px] text-gray-500 font-medium mb-0.5 ml-1">{entry.agent}</div>
        )}
        <div className="bg-gray-800 text-gray-100 rounded-2xl rounded-bl-md px-3.5 py-2.5 text-sm shadow-md">
          <div className="flex items-center gap-2">
            <span className="text-green-400 text-xs">{'\u25B6'}</span>
            <span>
              <span className="font-medium text-green-400">Started</span>
              {entry.task && <span className="text-gray-400 ml-1.5">: {entry.task}</span>}
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
      <div className="max-w-[70%]">
        {showAvatar && entry.agent && (
          <div className="text-[11px] text-gray-500 font-medium mb-0.5 ml-1">{entry.agent}</div>
        )}
        <div className="bg-gray-800 text-gray-100 rounded-2xl rounded-bl-md px-3.5 py-2.5 text-sm shadow-md">
          <div className="flex items-center gap-2">
            <span className={isError ? 'text-red-400 text-xs' : 'text-green-400 text-xs'}>
              {isError ? '\u2718' : '\u2714'}
            </span>
            <span>
              <span className={`font-medium ${isError ? 'text-red-400' : 'text-green-400'}`}>
                {isError ? 'Failed' : 'Finished'}
              </span>
              {stats.length > 0 && (
                <span className="text-gray-500 text-xs ml-1.5">({stats.join(', ')})</span>
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
      <div className="bg-blue-950/40 border border-blue-800/30 text-blue-300 rounded-2xl px-4 py-2 text-xs shadow-sm inline-flex items-center gap-2">
        <span className="font-medium">{entry.from_agent}</span>
        <svg
          width="14"
          height="14"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          className="text-blue-500"
        >
          <path
            d="M5 12h14M12 5l7 7-7 7"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
        <span className="font-medium">{entry.to_agent}</span>
        {entry.task && (
          <span className="text-blue-400/70 ml-0.5 truncate max-w-[200px]">: {entry.task}</span>
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
        a.type === 'agent_finished'
      )
    : activities;

  // Empty state
  if (activities.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-gray-500 text-sm px-4">
        <div className="w-14 h-14 rounded-full bg-gray-800 flex items-center justify-center mb-3 text-2xl">
          {'\u{1F4AC}'}
        </div>
        <p className="font-medium text-gray-400">No messages yet</p>
        <p className="text-gray-600 text-xs mt-1">Send a message to get started</p>
      </div>
    );
  }

  const groups = groupBySender(filtered);

  return (
    <div
      ref={scrollRef}
      className="flex flex-col h-full overflow-y-auto p-4 scroll-smooth"
      style={{ scrollBehavior: 'smooth' }}
    >
      {/* View mode toggle */}
      <div className="flex justify-end mb-2 sticky top-0 z-10">
        <div className="bg-gray-900/90 backdrop-blur-sm rounded-full p-0.5 flex gap-0.5 border border-gray-800/50">
          <button
            onClick={() => setViewMode('summary')}
            className={`px-2.5 py-1 rounded-full text-[10px] font-medium transition-colors
              ${viewMode === 'summary' ? 'bg-gray-800 text-gray-200' : 'text-gray-500 hover:text-gray-400'}`}
          >
            Summary
          </button>
          <button
            onClick={() => setViewMode('detail')}
            className={`px-2.5 py-1 rounded-full text-[10px] font-medium transition-colors
              ${viewMode === 'detail' ? 'bg-gray-800 text-gray-200' : 'text-gray-500 hover:text-gray-400'}`}
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
            className="px-3 py-1.5 text-xs text-gray-500 hover:text-gray-300 bg-gray-900 border border-gray-800 rounded-lg transition-colors"
          >
            Load earlier messages
          </button>
        </div>
      )}
      {groups.map((group, gi) => {
        // Build sub-items for the group
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
              items.push(
                <AgentTextBubble key={entry.id} entry={entry} showAvatar={showAvatar} />
              );
              break;
            case 'user_message':
              items.push(
                <UserMessageBubble key={entry.id} entry={entry} showAvatar={showAvatar} />
              );
              break;
            case 'agent_started':
              items.push(
                <AgentStartedBubble key={entry.id} entry={entry} showAvatar={showAvatar} />
              );
              break;
            case 'agent_finished':
              items.push(
                <AgentFinishedBubble key={entry.id} entry={entry} showAvatar={showAvatar} />
              );
              break;
            case 'delegation':
              items.push(<DelegationBubble key={entry.id} entry={entry} />);
              break;
            default:
              break;
          }
        }
        flushTools();

        // Timestamp for the group (use last entry's timestamp)
        const lastTs = group.entries[group.entries.length - 1].timestamp;
        const tsAlign =
          group.sender === 'user' ? 'right' : group.sender === 'agent' ? 'left' : 'center';

        return (
          <div
            key={`g-${gi}`}
            className={`flex flex-col gap-1 ${gi > 0 ? 'mt-4' : ''}`}
          >
            {items}
            <GroupTimestamp ts={lastTs} align={tsAlign as 'left' | 'right' | 'center'} />
          </div>
        );
      })}
      <div ref={endRef} />
    </div>
  );
}

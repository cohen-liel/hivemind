import { useEffect, useRef, useState } from 'react';
import type { ActivityEntry } from '../types';

interface Props {
  activities: ActivityEntry[];
}

// Group consecutive tool_use entries from the same agent
type GroupedEntry = {
  type: 'single';
  entry: ActivityEntry;
} | {
  type: 'tool_group';
  agent: string;
  entries: ActivityEntry[];
};

function groupActivities(activities: ActivityEntry[]): GroupedEntry[] {
  const result: GroupedEntry[] = [];
  let i = 0;

  while (i < activities.length) {
    const entry = activities[i];

    if (entry.type === 'tool_use') {
      // Collect consecutive tool_use from same agent
      const group: ActivityEntry[] = [entry];
      let j = i + 1;
      while (
        j < activities.length &&
        activities[j].type === 'tool_use' &&
        activities[j].agent === entry.agent
      ) {
        group.push(activities[j]);
        j++;
      }

      if (group.length > 1) {
        result.push({ type: 'tool_group', agent: entry.agent || '', entries: group });
      } else {
        result.push({ type: 'single', entry });
      }
      i = j;
    } else {
      result.push({ type: 'single', entry });
      i++;
    }
  }

  return result;
}

function formatTime(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function ToolUseRow({ entry }: { entry: ActivityEntry }) {
  return (
    <div className="flex items-start gap-2 py-0.5 text-xs font-mono text-gray-500">
      <span className="text-gray-700 flex-shrink-0 w-16">{formatTime(entry.timestamp)}</span>
      <span className="text-gray-600">{entry.agent}</span>
      <span className="text-gray-700 mx-0.5">&rarr;</span>
      <span className="text-gray-400 truncate">{entry.tool_description || entry.tool_name}</span>
    </div>
  );
}

function ToolGroup({ agent, entries }: { agent: string; entries: ActivityEntry[] }) {
  const [expanded, setExpanded] = useState(false);
  const shown = expanded ? entries : entries.slice(-1);

  return (
    <div className="border-l-2 border-gray-800 pl-3 my-0.5">
      {!expanded && entries.length > 1 && (
        <button
          onClick={() => setExpanded(true)}
          className="text-[11px] text-gray-600 hover:text-gray-400 mb-0.5"
        >
          {entries.length - 1} more tool calls from {agent}...
        </button>
      )}
      {expanded && entries.length > 1 && (
        <button
          onClick={() => setExpanded(false)}
          className="text-[11px] text-gray-600 hover:text-gray-400 mb-0.5"
        >
          Collapse {entries.length} tool calls
        </button>
      )}
      {shown.map((e) => (
        <ToolUseRow key={e.id} entry={e} />
      ))}
    </div>
  );
}

function AgentStartedRow({ entry }: { entry: ActivityEntry }) {
  return (
    <div className="flex items-start gap-2 py-1.5 text-sm">
      <span className="text-gray-700 text-xs flex-shrink-0 w-16 pt-0.5">{formatTime(entry.timestamp)}</span>
      <span className="text-green-500 flex-shrink-0">
        <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor"><path d="M4 3l9 5-9 5V3z"/></svg>
      </span>
      <span className="text-green-400">
        <span className="font-medium">{entry.agent}</span>
        {' '}started{entry.task ? ': ' : ''}
        {entry.task && <span className="text-gray-400">{entry.task}</span>}
      </span>
    </div>
  );
}

function AgentFinishedRow({ entry }: { entry: ActivityEntry }) {
  const color = entry.is_error ? 'text-red-400' : 'text-green-400';
  const icon = entry.is_error ? (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor" className="text-red-500"><path d="M8 1a7 7 0 100 14A7 7 0 008 1zm0 10.5a.75.75 0 110-1.5.75.75 0 010 1.5zM8.75 4.75v4a.75.75 0 01-1.5 0v-4a.75.75 0 011.5 0z"/></svg>
  ) : (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor" className="text-green-500"><path d="M8 1a7 7 0 100 14A7 7 0 008 1zm3.22 5.28l-4 4a.75.75 0 01-1.06 0l-2-2a.75.75 0 111.06-1.06L6.69 8.69l3.47-3.47a.75.75 0 111.06 1.06z"/></svg>
  );

  const stats: string[] = [];
  if (entry.cost !== undefined) stats.push(`$${entry.cost.toFixed(4)}`);
  if (entry.turns !== undefined) stats.push(`${entry.turns} turns`);
  if (entry.duration !== undefined) stats.push(`${entry.duration}s`);

  return (
    <div className="flex items-start gap-2 py-1.5 text-sm">
      <span className="text-gray-700 text-xs flex-shrink-0 w-16 pt-0.5">{formatTime(entry.timestamp)}</span>
      <span className="flex-shrink-0">{icon}</span>
      <span className={color}>
        <span className="font-medium">{entry.agent}</span>
        {' '}{entry.is_error ? 'failed' : 'finished'}
        {stats.length > 0 && (
          <span className="text-gray-600 text-xs ml-1.5">({stats.join(', ')})</span>
        )}
      </span>
    </div>
  );
}

function DelegationRow({ entry }: { entry: ActivityEntry }) {
  return (
    <div className="flex items-start gap-2 py-1.5 text-sm bg-blue-950/20 rounded-lg px-2 -mx-2">
      <span className="text-gray-700 text-xs flex-shrink-0 w-16 pt-0.5">{formatTime(entry.timestamp)}</span>
      <span className="text-blue-500 flex-shrink-0">
        <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor"><path d="M1 8h10M8 4l4 4-4 4" stroke="currentColor" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round"/></svg>
      </span>
      <span className="text-blue-400">
        <span className="font-medium">{entry.from_agent}</span>
        <span className="text-gray-600 mx-1">&rarr;</span>
        <span className="font-medium">{entry.to_agent}</span>
        {entry.task && <span className="text-gray-400">: {entry.task}</span>}
      </span>
    </div>
  );
}

function AgentTextRow({ entry }: { entry: ActivityEntry }) {
  const [expanded, setExpanded] = useState(false);
  const content = entry.content || '';
  const isLong = content.length > 300;
  const shown = expanded ? content : content.slice(0, 300);

  // Format code blocks
  const parts = shown.split(/(```[\s\S]*?```)/g);

  return (
    <div className="flex items-start gap-2 py-1.5 text-sm">
      <span className="text-gray-700 text-xs flex-shrink-0 w-16 pt-0.5">{formatTime(entry.timestamp)}</span>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5 mb-0.5">
          <span className="text-xs font-semibold text-gray-500">{entry.agent}</span>
          {entry.cost !== undefined && entry.cost > 0 && (
            <span className="text-[10px] text-gray-700">${entry.cost.toFixed(4)}</span>
          )}
        </div>
        <div className="text-gray-300 text-sm whitespace-pre-wrap break-words leading-relaxed">
          {parts.map((part, i) => {
            if (part.startsWith('```') && part.endsWith('```')) {
              const inner = part.slice(3, -3);
              const nlIdx = inner.indexOf('\n');
              const code = nlIdx >= 0 ? inner.slice(nlIdx + 1) : inner;
              return (
                <pre key={i} className="bg-black/30 rounded-md p-2 my-1 text-xs font-mono overflow-x-auto whitespace-pre">
                  {code}
                </pre>
              );
            }
            return <span key={i}>{part}</span>;
          })}
        </div>
        {isLong && (
          <button
            onClick={() => setExpanded(!expanded)}
            className="text-xs text-blue-500 hover:text-blue-400 mt-1"
          >
            {expanded ? 'Show less' : `Show more (${(content.length / 1024).toFixed(1)}KB)`}
          </button>
        )}
      </div>
    </div>
  );
}

function UserMessageRow({ entry }: { entry: ActivityEntry }) {
  return (
    <div className="flex items-start gap-2 py-2 text-sm">
      <span className="text-gray-700 text-xs flex-shrink-0 w-16 pt-0.5">{formatTime(entry.timestamp)}</span>
      <span className="text-blue-500 flex-shrink-0">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M20 21v-2a4 4 0 00-4-4H8a4 4 0 00-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
      </span>
      <div className="flex-1 min-w-0">
        <div className="text-blue-400 text-sm whitespace-pre-wrap break-words">
          {entry.content}
        </div>
      </div>
    </div>
  );
}

export default function ActivityFeed({ activities }: Props) {
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [activities.length]);

  if (activities.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-gray-500 text-sm px-4">
        <div className="w-12 h-12 rounded-full bg-gray-800 flex items-center justify-center mb-3">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" className="text-gray-600">
            <path d="M13 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V9z" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
            <path d="M13 2v7h7" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
          </svg>
        </div>
        <p>No activity yet</p>
        <p className="text-gray-600 text-xs mt-1">Send a message to get started</p>
      </div>
    );
  }

  const grouped = groupActivities(activities);

  return (
    <div className="flex flex-col h-full overflow-y-auto px-4 py-3">
      {grouped.map((item, i) => {
        if (item.type === 'tool_group') {
          return <ToolGroup key={`g-${i}`} agent={item.agent} entries={item.entries} />;
        }

        const entry = item.entry;
        switch (entry.type) {
          case 'tool_use':
            return <ToolUseRow key={entry.id} entry={entry} />;
          case 'agent_started':
            return <AgentStartedRow key={entry.id} entry={entry} />;
          case 'agent_finished':
            return <AgentFinishedRow key={entry.id} entry={entry} />;
          case 'delegation':
            return <DelegationRow key={entry.id} entry={entry} />;
          case 'agent_text':
            return <AgentTextRow key={entry.id} entry={entry} />;
          case 'user_message':
            return <UserMessageRow key={entry.id} entry={entry} />;
          default:
            return null;
        }
      })}
      <div ref={endRef} />
    </div>
  );
}

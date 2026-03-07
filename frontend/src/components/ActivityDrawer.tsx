import { useEffect, useRef, useState } from 'react';
import type { ActivityEntry } from '../types';

interface Props {
  activities: ActivityEntry[];
}

function formatTime(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function EntryRow({ entry }: { entry: ActivityEntry }) {
  switch (entry.type) {
    case 'tool_use':
      return (
        <div className="flex items-center gap-2 text-xs font-mono text-gray-500 py-0.5">
          <span className="text-gray-700 w-14 flex-shrink-0">{formatTime(entry.timestamp)}</span>
          <span className="text-gray-500">{entry.agent}</span>
          <span className="text-gray-700">&rarr;</span>
          <span className="text-gray-400 truncate">{entry.tool_description || entry.tool_name}</span>
        </div>
      );

    case 'agent_started':
      return (
        <div className="flex items-center gap-2 text-xs py-1">
          <span className="text-gray-700 w-14 flex-shrink-0">{formatTime(entry.timestamp)}</span>
          <svg width="10" height="10" viewBox="0 0 16 16" fill="currentColor" className="text-green-500 flex-shrink-0">
            <path d="M4 3l9 5-9 5V3z"/>
          </svg>
          <span className="text-green-400 truncate">
            <span className="font-semibold">{entry.agent}</span> started
            {entry.task && <span className="text-gray-500">: {entry.task}</span>}
          </span>
        </div>
      );

    case 'agent_finished': {
      const color = entry.is_error ? 'text-red-400' : 'text-green-400';
      const stats: string[] = [];
      if (entry.cost !== undefined) stats.push(`$${entry.cost.toFixed(3)}`);
      if (entry.turns !== undefined) stats.push(`${entry.turns}t`);
      if (entry.duration !== undefined) stats.push(`${Math.round(entry.duration)}s`);
      return (
        <div className="flex items-center gap-2 text-xs py-1">
          <span className="text-gray-700 w-14 flex-shrink-0">{formatTime(entry.timestamp)}</span>
          <span className={`flex-shrink-0 ${entry.is_error ? 'text-red-500' : 'text-green-500'}`}>
            {entry.is_error ? '\u2717' : '\u2713'}
          </span>
          <span className={`${color} truncate`}>
            <span className="font-semibold">{entry.agent}</span> {entry.is_error ? 'failed' : 'done'}
            {stats.length > 0 && <span className="text-gray-600 ml-1">({stats.join(' ')})</span>}
          </span>
        </div>
      );
    }

    case 'delegation':
      return (
        <div className="flex items-center gap-2 text-xs py-1 text-blue-400">
          <span className="text-gray-700 w-14 flex-shrink-0">{formatTime(entry.timestamp)}</span>
          <svg width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className="flex-shrink-0">
            <path d="M1 8h10M8 4l4 4-4 4"/>
          </svg>
          <span className="truncate">
            <span className="font-semibold">{entry.from_agent}</span>
            <span className="text-gray-600 mx-1">&rarr;</span>
            <span className="font-semibold">{entry.to_agent}</span>
            {entry.task && <span className="text-gray-500">: {entry.task}</span>}
          </span>
        </div>
      );

    case 'user_message':
      return (
        <div className="flex items-center gap-2 text-xs py-1">
          <span className="text-gray-700 w-14 flex-shrink-0">{formatTime(entry.timestamp)}</span>
          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" className="text-blue-400 flex-shrink-0">
            <circle cx="12" cy="7" r="4"/><path d="M5.5 21a6.5 6.5 0 0113 0"/>
          </svg>
          <span className="text-blue-300 truncate">{entry.content}</span>
        </div>
      );

    case 'agent_text':
      return (
        <div className="flex items-start gap-2 text-xs py-1">
          <span className="text-gray-700 w-14 flex-shrink-0">{formatTime(entry.timestamp)}</span>
          <span className="text-gray-500 font-semibold flex-shrink-0">{entry.agent}</span>
          <span className="text-gray-400 truncate">{(entry.content || '').slice(0, 150)}</span>
        </div>
      );

    default:
      return null;
  }
}

export default function ActivityDrawer({ activities }: Props) {
  const [expanded, setExpanded] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Auto-scroll when expanded and new activities arrive
  useEffect(() => {
    if (expanded && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [activities.length, expanded]);

  // Show last N entries when collapsed
  const recentCount = 4;
  const recent = activities.slice(-recentCount);

  return (
    <div className={`border-t border-gray-800/60 bg-gray-900/95 backdrop-blur-md transition-all duration-300
      ${expanded ? 'max-h-[50vh]' : 'max-h-[140px]'}`}>

      {/* Drawer handle */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center justify-between px-4 py-2 hover:bg-gray-800/30 transition-colors"
      >
        <div className="flex items-center gap-2">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className="text-gray-500">
            <polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/>
          </svg>
          <span className="text-xs font-semibold text-gray-500 uppercase tracking-wider">
            Activity Log
          </span>
          <span className="text-[10px] text-gray-700 bg-gray-800 rounded-full px-1.5 py-0.5">
            {activities.length}
          </span>
        </div>
        <svg
          width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor"
          strokeWidth="2.5" strokeLinecap="round"
          className={`text-gray-600 transition-transform duration-200 ${expanded ? 'rotate-180' : ''}`}
        >
          <polyline points="6 9 12 15 18 9"/>
        </svg>
      </button>

      {/* Content */}
      <div
        ref={scrollRef}
        className={`overflow-y-auto px-4 pb-2 transition-all duration-300
          ${expanded ? 'max-h-[calc(50vh-40px)]' : 'max-h-[95px]'}`}
      >
        {expanded ? (
          // Full log
          activities.map((entry) => <EntryRow key={entry.id} entry={entry} />)
        ) : (
          // Recent only
          <>
            {activities.length > recentCount && (
              <div className="text-[10px] text-gray-700 mb-1">
                ... {activities.length - recentCount} earlier entries
              </div>
            )}
            {recent.map((entry) => <EntryRow key={entry.id} entry={entry} />)}
          </>
        )}
      </div>
    </div>
  );
}

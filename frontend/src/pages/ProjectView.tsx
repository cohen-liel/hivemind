import { useEffect, useState, useCallback } from 'react';
import { useParams } from 'react-router-dom';
import { getProject, getMessages, getFiles, sendMessage, talkToAgent, pauseProject, resumeProject, stopProject } from '../api';
import { useWebSocket } from '../useWebSocket';
import ActivityFeed from '../components/ActivityFeed';
import ActivityDrawer from '../components/ActivityDrawer';
import AgentStatusPanel from '../components/AgentStatusPanel';
import ConductorBar from '../components/ConductorBar';
import ProjectHeader from '../components/ProjectHeader';
import FileDiff from '../components/FileDiff';
import Controls from '../components/Controls';
import CodeBrowser from '../components/CodeBrowser';
import type { Project, ProjectMessage, FileChanges, WSEvent, ActivityEntry, AgentState as AgentStateType, LoopProgress } from '../types';

let activityIdCounter = 0;
function nextId(): string {
  return `a-${++activityIdCounter}`;
}

function messagesToActivities(messages: ProjectMessage[]): ActivityEntry[] {
  return messages.map((msg) => ({
    id: `msg-${msg.timestamp}-${msg.agent_name}`,
    type: msg.agent_name === 'user' ? 'user_message' as const : 'agent_text' as const,
    timestamp: msg.timestamp,
    agent: msg.agent_name,
    content: msg.content,
    cost: msg.cost_usd,
  }));
}

type MobileView = 'orchestra' | 'activity' | 'code' | 'changes';

export default function ProjectView() {
  const { id } = useParams<{ id: string }>();
  const [project, setProject] = useState<Project | null>(null);
  const [activities, setActivities] = useState<ActivityEntry[]>([]);
  const [agentStates, setAgentStates] = useState<Record<string, AgentStateType>>({});
  const [loopProgress, setLoopProgress] = useState<LoopProgress | null>(null);
  const [files, setFiles] = useState<FileChanges | null>(null);
  const [mobileView, setMobileView] = useState<MobileView>('orchestra');
  const [selectedAgent, setSelectedAgent] = useState<string | null>(null);

  const loadProject = useCallback(async () => {
    if (!id) return;
    const p = await getProject(id);
    setProject(p);
  }, [id]);

  const loadFiles = useCallback(async () => {
    if (!id) return;
    const f = await getFiles(id);
    setFiles(f);
  }, [id]);

  useEffect(() => {
    if (!id) return;
    loadProject();
    getMessages(id, 100).then((data) => {
      setActivities(messagesToActivities(data.messages));
    });
    loadFiles();
  }, [id, loadProject, loadFiles]);

  const handleWSEvent = useCallback((event: WSEvent) => {
    if (event.project_id !== id) return;

    switch (event.type) {
      case 'tool_use':
        setActivities(prev => [...prev, {
          id: nextId(), type: 'tool_use', timestamp: event.timestamp,
          agent: event.agent, tool_name: event.tool_name, tool_description: event.description,
        }]);
        if (event.agent) {
          setAgentStates(prev => ({
            ...prev,
            [event.agent!]: { ...prev[event.agent!], name: event.agent!, current_tool: event.description },
          }));
        }
        break;

      case 'agent_started':
        setActivities(prev => [...prev, {
          id: nextId(), type: 'agent_started', timestamp: event.timestamp,
          agent: event.agent, task: event.task,
        }]);
        if (event.agent) {
          setAgentStates(prev => ({
            ...prev,
            [event.agent!]: {
              name: event.agent!, state: 'working', task: event.task, current_tool: undefined,
              cost: prev[event.agent!]?.cost ?? 0, turns: prev[event.agent!]?.turns ?? 0,
              duration: prev[event.agent!]?.duration ?? 0,
            },
          }));
        }
        break;

      case 'agent_finished':
        setActivities(prev => [...prev, {
          id: nextId(), type: 'agent_finished', timestamp: event.timestamp,
          agent: event.agent, cost: event.cost, turns: event.turns,
          duration: event.duration, is_error: event.is_error,
        }]);
        if (event.agent) {
          setAgentStates(prev => ({
            ...prev,
            [event.agent!]: {
              ...prev[event.agent!], name: event.agent!,
              state: event.is_error ? 'error' : 'done', current_tool: undefined,
              cost: (prev[event.agent!]?.cost ?? 0) + (event.cost ?? 0),
              turns: (prev[event.agent!]?.turns ?? 0) + (event.turns ?? 0),
              duration: event.duration ?? 0,
            },
          }));
        }
        break;

      case 'delegation':
        setActivities(prev => [...prev, {
          id: nextId(), type: 'delegation', timestamp: event.timestamp,
          from_agent: event.from_agent, to_agent: event.to_agent, task: event.task,
        }]);
        break;

      case 'loop_progress':
        setLoopProgress({
          loop: event.loop ?? 0, max_loops: event.max_loops ?? 0,
          turn: event.turn ?? 0, max_turns: event.max_turns ?? 0,
          cost: event.cost ?? 0, max_budget: event.max_budget ?? 0,
        });
        break;

      case 'agent_result':
        if (event.text) {
          const agentMatch = event.text.match(/\*(\w+)\*/);
          setActivities(prev => [...prev, {
            id: nextId(), type: 'agent_text', timestamp: event.timestamp,
            agent: agentMatch ? agentMatch[1] : 'agent', content: event.text,
          }]);
        }
        break;

      case 'agent_final':
        loadProject();
        loadFiles();
        if (event.text) {
          setActivities(prev => [...prev, {
            id: nextId(), type: 'agent_text', timestamp: event.timestamp,
            agent: 'system', content: event.text,
          }]);
        }
        setAgentStates(prev => {
          const reset: Record<string, AgentStateType> = {};
          for (const [k, v] of Object.entries(prev)) {
            reset[k] = { ...v, state: 'idle', current_tool: undefined };
          }
          return reset;
        });
        setLoopProgress(null);
        break;

      case 'project_status':
        loadProject();
        break;
    }
  }, [id, loadProject, loadFiles]);

  const { connected } = useWebSocket(handleWSEvent);

  if (!project || !id) {
    return (
      <div className="min-h-screen bg-gray-950 flex items-center justify-center text-gray-500">
        <div className="animate-pulse text-sm">Loading...</div>
      </div>
    );
  }

  const handleSend = async (message: string, agent?: string) => {
    setActivities(prev => [...prev, {
      id: nextId(), type: 'user_message', timestamp: Date.now() / 1000,
      agent: 'user', content: message,
    }]);
    if (agent) {
      await talkToAgent(id, agent, message);
    } else {
      await sendMessage(id, message);
    }
    loadProject();
  };

  const handlePause = async () => { await pauseProject(id); loadProject(); };
  const handleResume = async () => { await resumeProject(id); loadProject(); };
  const handleStop = async () => { await stopProject(id); loadProject(); };

  const agentStateList: AgentStateType[] = project.agents.map(name => (
    agentStates[name] ?? { name, state: 'idle', cost: 0, turns: 0, duration: 0 }
  ));

  const orchestratorState = agentStateList.find(a => a.name === 'orchestrator') ?? null;

  const mobileNavItems: { id: MobileView; icon: JSX.Element; label: string }[] = [
    {
      id: 'orchestra',
      label: 'Agents',
      icon: <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="7" r="4"/><path d="M5.5 21a6.5 6.5 0 0113 0"/></svg>,
    },
    {
      id: 'activity',
      label: 'Log',
      icon: <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>,
    },
    {
      id: 'code',
      label: 'Code',
      icon: <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>,
    },
    {
      id: 'changes',
      label: 'Diff',
      icon: <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M12 3v18M3 12h18"/></svg>,
    },
  ];

  return (
    <div className="min-h-screen bg-gray-950 flex flex-col">

      {/* ===== MOBILE LAYOUT ===== */}
      <div className="lg:hidden flex flex-col h-screen">

        {/* Conductor (top) */}
        <ConductorBar
          projectName={project.project_name}
          status={project.status}
          connected={connected}
          orchestrator={orchestratorState}
          progress={loopProgress}
          totalCost={project.total_cost_usd}
        />

        {/* Content (middle, scrollable) */}
        <div className="flex-1 overflow-y-auto">
          {mobileView === 'orchestra' && (
            <div className="p-3 space-y-3">
              <AgentStatusPanel
                agents={agentStateList}
                onSelectAgent={setSelectedAgent}
                selectedAgent={selectedAgent}
                layout="grid"
              />

              {files && (files.stat || files.status) && (
                <div className="bg-gray-900/60 border border-gray-800/50 rounded-xl p-3">
                  <FileDiff files={files} />
                </div>
              )}
            </div>
          )}

          {mobileView === 'activity' && (
            <ActivityFeed activities={activities} />
          )}

          {mobileView === 'code' && (
            <CodeBrowser projectId={id} />
          )}

          {mobileView === 'changes' && (
            <div className="p-3">
              <div className="bg-gray-900/60 border border-gray-800/50 rounded-xl p-3">
                <FileDiff files={files} />
              </div>
            </div>
          )}
        </div>

        {/* Tab nav + Controls (bottom) */}
        <div>
          <div className="flex items-center border-t border-gray-800/50 bg-gray-900/60">
            {mobileNavItems.map(item => (
              <button
                key={item.id}
                onClick={() => setMobileView(item.id)}
                className={`flex-1 flex flex-col items-center gap-0.5 py-2 transition-colors
                  ${mobileView === item.id ? 'text-blue-400' : 'text-gray-600'}`}
              >
                {item.icon}
                <span className="text-[9px] font-medium">{item.label}</span>
              </button>
            ))}
          </div>

          <Controls
            projectId={id}
            status={project.status}
            agents={project.agents}
            onPause={handlePause}
            onResume={handleResume}
            onStop={handleStop}
            onSend={handleSend}
          />
        </div>
      </div>

      {/* ===== DESKTOP LAYOUT ===== */}
      <div className="hidden lg:flex flex-col h-screen">
        {/* Conductor header with progress */}
        <ConductorBar
          projectName={project.project_name}
          status={project.status}
          connected={connected}
          orchestrator={orchestratorState}
          progress={loopProgress}
          totalCost={project.total_cost_usd}
        />

        {/* MAIN: Agent Orchestra - takes center stage */}
        <div className="flex-1 overflow-y-auto">
          <div className="max-w-5xl mx-auto w-full px-6 py-6">
            {/* Agent cards grid */}
            <AgentStatusPanel
              agents={agentStateList}
              onSelectAgent={setSelectedAgent}
              selectedAgent={selectedAgent}
              layout="grid"
            />

            {/* File changes (collapsible, below agents) */}
            {files && (files.stat || files.status || files.diff) && (
              <div className="mt-6 bg-gray-900/60 border border-gray-800/50 rounded-xl p-4">
                <FileDiff files={files} />
              </div>
            )}
          </div>
        </div>

        {/* Activity Drawer (collapsible from bottom) */}
        <ActivityDrawer activities={activities} />

        {/* Controls bar */}
        <Controls
          projectId={id}
          status={project.status}
          agents={project.agents}
          onPause={handlePause}
          onResume={handleResume}
          onStop={handleStop}
          onSend={handleSend}
        />
      </div>
    </div>
  );
}

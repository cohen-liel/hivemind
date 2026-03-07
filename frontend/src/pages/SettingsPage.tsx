import { useEffect, useState } from 'react';
import { getSettings } from '../api';
import type { Settings } from '../types';

export default function SettingsPage() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    getSettings()
      .then(setSettings)
      .catch(() => setError('Could not load settings. Is the backend running?'));
  }, []);

  if (error) {
    return (
      <div className="p-8">
        <h1 className="text-2xl font-bold text-white mb-4">Settings</h1>
        <div className="bg-red-500/10 border border-red-500/30 rounded-lg px-4 py-3 text-sm text-red-400">
          {error}
        </div>
      </div>
    );
  }

  if (!settings) {
    return (
      <div className="p-8">
        <h1 className="text-2xl font-bold text-white mb-4">Settings</h1>
        <div className="text-gray-500 animate-pulse">Loading...</div>
      </div>
    );
  }

  const sections = [
    {
      title: 'Agent Limits',
      items: [
        { label: 'Max Turns per Cycle', value: settings.max_turns_per_cycle, desc: 'Maximum turns before pausing (MAX_TURNS_PER_CYCLE)' },
        { label: 'Max Budget', value: `$${settings.max_budget_usd.toFixed(2)}`, desc: 'Budget limit per session (MAX_BUDGET_USD)' },
        { label: 'Agent Timeout', value: `${settings.agent_timeout_seconds}s`, desc: 'Timeout for each agent query (AGENT_TIMEOUT_SECONDS)' },
        { label: 'Max Orchestrator Loops', value: settings.max_orchestrator_loops, desc: 'Safety limit on orchestrator iterations (MAX_ORCHESTRATOR_LOOPS)' },
      ],
    },
    {
      title: 'SDK Settings',
      items: [
        { label: 'Max Turns per Query', value: settings.sdk_max_turns_per_query, desc: 'Turns per sub-agent query (SDK_MAX_TURNS_PER_QUERY)' },
        { label: 'Max Budget per Query', value: `$${settings.sdk_max_budget_per_query.toFixed(2)}`, desc: 'Budget per sub-agent query (SDK_MAX_BUDGET_PER_QUERY)' },
      ],
    },
    {
      title: 'General',
      items: [
        { label: 'Projects Base Directory', value: settings.projects_base_dir, desc: 'Default directory for new projects (CLAUDE_PROJECTS_DIR)' },
        { label: 'Max Message Length', value: `${settings.max_user_message_length} chars`, desc: 'User message size limit (MAX_USER_MESSAGE_LENGTH)' },
        { label: 'Session Expiry', value: `${settings.session_expiry_hours}h`, desc: 'Sessions expire after this period (SESSION_EXPIRY_HOURS)' },
      ],
    },
  ];

  return (
    <div className="p-8 max-w-2xl">
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-white mb-2">Settings</h1>
        <p className="text-gray-400 text-sm">
          Configuration values from <code className="text-gray-500 bg-gray-800 px-1.5 py-0.5 rounded text-xs">.env</code>.
          Edit the file and restart the bot to change these.
        </p>
      </div>

      <div className="space-y-6">
        {sections.map(section => (
          <div key={section.title} className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
            <div className="px-4 py-3 border-b border-gray-800">
              <h2 className="text-sm font-semibold text-gray-300">{section.title}</h2>
            </div>
            <div className="divide-y divide-gray-800/50">
              {section.items.map(item => (
                <div key={item.label} className="px-4 py-3 flex items-center justify-between gap-4">
                  <div className="min-w-0">
                    <div className="text-sm text-gray-300">{item.label}</div>
                    <div className="text-xs text-gray-600 mt-0.5">{item.desc}</div>
                  </div>
                  <div className="text-sm font-mono text-white bg-gray-800 px-2.5 py-1 rounded flex-shrink-0">
                    {item.value}
                  </div>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

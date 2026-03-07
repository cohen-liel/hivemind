import { useEffect, useState } from 'react';
import { getSettings, updateSettings, persistSettings } from '../api';
import type { Settings } from '../types';

interface EditableField {
  key: string;
  label: string;
  desc: string;
  type: 'number' | 'float';
  min?: number;
  max?: number;
}

const EDITABLE_FIELDS: { title: string; fields: EditableField[] }[] = [
  {
    title: 'Agent Limits',
    fields: [
      { key: 'max_turns_per_cycle', label: 'Max Turns per Cycle', desc: 'Maximum turns before pausing', type: 'number', min: 1, max: 1000 },
      { key: 'max_budget_usd', label: 'Max Budget (USD)', desc: 'Budget limit per session', type: 'float', min: 0.1, max: 1000 },
      { key: 'agent_timeout_seconds', label: 'Agent Timeout (sec)', desc: 'Timeout for each agent query', type: 'number', min: 30, max: 3600 },
      { key: 'max_orchestrator_loops', label: 'Max Orchestrator Loops', desc: 'Safety limit on orchestrator iterations', type: 'number', min: 1, max: 100 },
    ],
  },
  {
    title: 'SDK Settings',
    fields: [
      { key: 'sdk_max_turns_per_query', label: 'Max Turns per Query', desc: 'Turns per sub-agent query', type: 'number', min: 1, max: 200 },
      { key: 'sdk_max_budget_per_query', label: 'Max Budget per Query (USD)', desc: 'Budget per sub-agent query', type: 'float', min: 0.1, max: 100 },
    ],
  },
  {
    title: 'General',
    fields: [
      { key: 'max_user_message_length', label: 'Max Message Length', desc: 'User message size limit in chars', type: 'number', min: 100, max: 100000 },
    ],
  },
];

export default function SettingsPage() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [draft, setDraft] = useState<Record<string, number>>({});
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    getSettings()
      .then((s) => {
        setSettings(s);
        // Initialize draft with current values
        const d: Record<string, number> = {};
        const raw = s as unknown as Record<string, unknown>;
        for (const section of EDITABLE_FIELDS) {
          for (const field of section.fields) {
            d[field.key] = typeof raw[field.key] === 'number' ? (raw[field.key] as number) : 0;
          }
        }
        setDraft(d);
      })
      .catch(() => setError('Could not load settings. Is the backend running?'));
  }, []);

  const handleSave = async () => {
    setSaving(true);
    setSaved(false);
    try {
      await updateSettings(draft);
      await persistSettings(draft).catch(() => {});
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    } catch {
      setError('Failed to save settings');
    } finally {
      setSaving(false);
    }
  };

  const hasChanges = settings ? EDITABLE_FIELDS.some(s => {
    const raw = settings as unknown as Record<string, unknown>;
    return s.fields.some(f => draft[f.key] !== raw[f.key]);
  }) : false;

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

  return (
    <div className="p-8 max-w-2xl">
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-white mb-2">Settings</h1>
        <p className="text-gray-400 text-sm">
          Edit configuration values. Changes take effect immediately for new sessions.
        </p>
      </div>

      <div className="space-y-6">
        {EDITABLE_FIELDS.map(section => (
          <div key={section.title} className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
            <div className="px-4 py-3 border-b border-gray-800">
              <h2 className="text-sm font-semibold text-gray-300">{section.title}</h2>
            </div>
            <div className="divide-y divide-gray-800/50">
              {section.fields.map(field => (
                <div key={field.key} className="px-4 py-3 flex items-center justify-between gap-4">
                  <div className="min-w-0">
                    <div className="text-sm text-gray-300">{field.label}</div>
                    <div className="text-xs text-gray-600 mt-0.5">{field.desc}</div>
                  </div>
                  <input
                    type="number"
                    value={draft[field.key] ?? 0}
                    onChange={(e) => {
                      const val = field.type === 'float'
                        ? parseFloat(e.target.value) || 0
                        : parseInt(e.target.value) || 0;
                      setDraft(prev => ({ ...prev, [field.key]: val }));
                    }}
                    min={field.min}
                    max={field.max}
                    step={field.type === 'float' ? 0.1 : 1}
                    className="w-24 bg-gray-800 border border-gray-700/50 text-white text-sm font-mono
                               px-2.5 py-1.5 rounded-lg focus:border-blue-500 focus:outline-none text-right"
                  />
                </div>
              ))}
            </div>
          </div>
        ))}

        {/* Read-only info */}
        <div className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
          <div className="px-4 py-3 border-b border-gray-800">
            <h2 className="text-sm font-semibold text-gray-300">Read-Only</h2>
          </div>
          <div className="divide-y divide-gray-800/50">
            <div className="px-4 py-3 flex items-center justify-between gap-4">
              <div className="min-w-0">
                <div className="text-sm text-gray-300">Projects Base Directory</div>
                <div className="text-xs text-gray-600 mt-0.5">Set via CLAUDE_PROJECTS_DIR env var</div>
              </div>
              <div className="text-sm font-mono text-gray-500 bg-gray-800 px-2.5 py-1 rounded flex-shrink-0 truncate max-w-[200px]">
                {settings.projects_base_dir}
              </div>
            </div>
            <div className="px-4 py-3 flex items-center justify-between gap-4">
              <div className="min-w-0">
                <div className="text-sm text-gray-300">Session Expiry</div>
                <div className="text-xs text-gray-600 mt-0.5">Set via SESSION_EXPIRY_HOURS env var</div>
              </div>
              <div className="text-sm font-mono text-gray-500 bg-gray-800 px-2.5 py-1 rounded flex-shrink-0">
                {settings.session_expiry_hours}h
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Save button */}
      <div className="mt-6 flex items-center gap-3">
        <button
          onClick={handleSave}
          disabled={!hasChanges || saving}
          className={`px-5 py-2 rounded-lg text-sm font-medium transition-all
            ${hasChanges && !saving
              ? 'bg-blue-600 hover:bg-blue-500 text-white shadow-[0_0_12px_rgba(59,130,246,0.3)]'
              : 'bg-gray-800 text-gray-600 cursor-not-allowed'}`}
        >
          {saving ? 'Saving...' : 'Save Changes'}
        </button>
        {saved && (
          <span className="text-sm text-green-400 animate-[fadeSlideIn_0.3s_ease-out]">
            Settings saved
          </span>
        )}
      </div>
    </div>
  );
}

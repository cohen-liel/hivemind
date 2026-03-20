import { useEffect, useState, useCallback } from 'react';
import { getSettings, updateSettings, persistSettings } from '../api';
import { SettingsSkeleton } from '../components/Skeleton';
import ErrorState from '../components/ErrorState';
import { useToast } from '../components/Toast';
import WatchdogStatusPanel from '../components/WatchdogStatusPanel';
import type { Settings, NumericSettingsKey } from '../types';

interface ApprovedDevice {
  device_id: string;
  name: string;
  approved_at: number;
  last_seen: number;
  ip: string;
  user_agent: string;
}

interface EditableField {
  key: NumericSettingsKey;
  label: string;
  desc: string;
  type: 'number' | 'float';
  min?: number;
  max?: number;
}

const EDITABLE_FIELDS: { title: string; icon: string; fields: EditableField[] }[] = [
  {
    title: 'Agent Limits',
    icon: '🤖',
    fields: [
      { key: 'max_turns_per_cycle', label: 'Max Turns per Cycle', desc: 'Maximum turns before pausing', type: 'number', min: 1, max: 1000 },
      { key: 'max_budget_usd', label: 'Max Budget (USD)', desc: 'Budget limit per session', type: 'float', min: 0.1, max: 1000 },
      { key: 'agent_timeout_seconds', label: 'Agent Timeout (sec)', desc: 'Timeout for each agent query', type: 'number', min: 30, max: 3600 },
      { key: 'max_orchestrator_loops', label: 'Max Orchestrator Loops', desc: 'Safety limit on orchestrator iterations', type: 'number', min: 1, max: 100 },
    ],
  },
  {
    title: 'SDK Settings',
    icon: '⚙️',
    fields: [
      { key: 'sdk_max_turns_per_query', label: 'Max Turns per Query', desc: 'Turns per sub-agent query', type: 'number', min: 1, max: 200 },
      { key: 'sdk_max_budget_per_query', label: 'Max Budget per Query (USD)', desc: 'Budget per sub-agent query', type: 'float', min: 0.1, max: 100 },
    ],
  },
  {
    title: 'General',
    icon: '📝',
    fields: [
      { key: 'max_user_message_length', label: 'Max Message Length', desc: 'User message size limit in chars', type: 'number', min: 100, max: 100000 },
    ],
  },
];

export default function SettingsPage() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [draft, setDraft] = useState<Record<string, number>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const toast = useToast();

  const loadSettings = () => {
    setLoading(true);
    setError('');
    getSettings()
      .then((s) => {
        setSettings(s);
        const d: Record<string, number> = {};
        for (const section of EDITABLE_FIELDS) {
          for (const field of section.fields) {
            const val = s[field.key];
            d[field.key] = typeof val === 'number' ? val : 0;
          }
        }
        setDraft(d);
      })
      .catch(() => setError('Could not load settings. Is the backend running?'))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    loadSettings();
  }, []);

  const handleSave = async () => {
    setSaving(true);
    try {
      await updateSettings(draft);
      // persistSettings is best-effort — don't fail the save if it errors
      try {
        await persistSettings(draft);
      } catch {
        // persist failed (e.g. disk write) — in-memory update still succeeded
      }
      toast.success('Settings saved successfully');
      // Update the settings reference so hasChanges resets
      setSettings(prev => {
        if (!prev) return prev;
        return { ...prev, ...draft } as Settings;
      });
    } catch {
      toast.error('Failed to save settings');
    } finally {
      setSaving(false);
    }
  };

  const hasChanges = settings ? EDITABLE_FIELDS.some(s => {
    return s.fields.some(f => draft[f.key] !== settings[f.key]);
  }) : false;

  if (loading) {
    return <SettingsSkeleton />;
  }

  if (error && !settings) {
    return (
      <div className="min-h-screen flex items-center justify-center" style={{ background: 'var(--bg-void)' }}>
        <ErrorState variant="connection" onRetry={loadSettings} />
      </div>
    );
  }

  if (!settings) return null;

  return (
    <div className="min-h-screen safe-area-top page-enter" style={{ background: 'var(--bg-void)' }}>
      {/* Header */}
      <header className="relative overflow-hidden" style={{ borderBottom: '1px solid var(--border-dim)' }}>
        <div className="absolute inset-0" style={{
          background: 'radial-gradient(ellipse at 30% 50%, rgba(167,139,250,0.06) 0%, transparent 50%)',
        }} />
        <div className="relative max-w-2xl mx-auto px-4 sm:px-6 py-6">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl flex items-center justify-center text-xl"
              style={{ background: 'rgba(167,139,250,0.1)', boxShadow: '0 0 20px rgba(167,139,250,0.1)' }}>
              ⚙️
            </div>
            <div>
              <h1 className="text-2xl font-bold" style={{ color: 'var(--text-primary)', fontFamily: 'var(--font-display)' }}>
                Settings
              </h1>
              <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
                Changes take effect immediately for new sessions
              </p>
            </div>
          </div>
        </div>
      </header>

      <div className="max-w-2xl mx-auto px-4 sm:px-6 py-6 space-y-5">
        {EDITABLE_FIELDS.map(section => (
          <div
            key={section.title}
            className="rounded-2xl overflow-hidden"
            style={{ background: 'var(--bg-card)', border: '1px solid var(--border-dim)' }}
          >
            <div className="px-5 py-3.5 flex items-center gap-2.5" style={{ borderBottom: '1px solid var(--border-dim)' }}>
              <span className="text-base">{section.icon}</span>
              <h2 className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>{section.title}</h2>
            </div>
            <div>
              {section.fields.map((field, i) => (
                <div
                  key={field.key}
                  className="px-5 py-3.5 flex items-center justify-between gap-4"
                  style={{ borderBottom: i < section.fields.length - 1 ? '1px solid var(--border-dim)' : 'none' }}
                >
                  <div className="min-w-0">
                    <label htmlFor={`field-${field.key}`} className="text-sm" style={{ color: 'var(--text-primary)' }}>{field.label}</label>
                    <div id={`desc-${field.key}`} className="text-xs mt-0.5" style={{ color: 'var(--text-muted)' }}>{field.desc}</div>
                  </div>
                  <input
                    id={`field-${field.key}`}
                    type="number"
                    value={draft[field.key] ?? 0}
                    aria-label={field.label}
                    aria-describedby={`desc-${field.key}`}
                    onChange={(e) => {
                      const val = field.type === 'float'
                        ? parseFloat(e.target.value) || 0
                        : parseInt(e.target.value) || 0;
                      setDraft(prev => ({ ...prev, [field.key]: val }));
                    }}
                    min={field.min}
                    max={field.max}
                    step={field.type === 'float' ? 0.1 : 1}
                    className="w-24 text-sm text-right px-2.5 py-1.5 rounded-xl focus:outline-none transition-colors"
                    style={{
                      background: 'var(--bg-elevated)',
                      border: '1px solid var(--border-subtle)',
                      color: 'var(--text-primary)',
                      fontFamily: 'var(--font-mono)',
                    }}
                  />
                </div>
              ))}
            </div>
          </div>
        ))}

        {/* Read-only info */}
        <div
          className="rounded-2xl overflow-hidden"
          style={{ background: 'var(--bg-card)', border: '1px solid var(--border-dim)' }}
        >
          <div className="px-5 py-3.5 flex items-center gap-2.5" style={{ borderBottom: '1px solid var(--border-dim)' }}>
            <span className="text-base">🔒</span>
            <h2 className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>Read-Only</h2>
          </div>
          <div>
            <div className="px-5 py-3.5 flex items-center justify-between gap-4">
              <div className="min-w-0">
                <div className="text-sm" style={{ color: 'var(--text-primary)' }}>Session Expiry</div>
                <div className="text-xs mt-0.5" style={{ color: 'var(--text-muted)' }}>Set via SESSION_EXPIRY_HOURS env var</div>
              </div>
              <div className="text-xs px-2.5 py-1 rounded-lg flex-shrink-0"
                style={{ background: 'var(--bg-elevated)', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
                {settings.session_expiry_hours}h
              </div>
            </div>
          </div>
        </div>

        {/* Device Management Section */}
        <DeviceManagement />

        {/* Watchdog Test Health */}
        <WatchdogStatusPanel />

        {/* Save button - sticky */}
        {hasChanges && (
          <div
            className="sticky bottom-4 flex items-center justify-center"
            style={{ animation: 'slideUp 0.3s ease-out' }}
          >
            <button
              onClick={handleSave}
              disabled={saving}
              className="px-6 py-2.5 text-sm font-medium rounded-xl transition-all active:scale-95 text-white"
              style={{
                background: saving ? 'var(--bg-elevated)' : 'linear-gradient(135deg, var(--accent-blue), #4f6ef5)',
                boxShadow: saving ? 'none' : '0 4px 20px var(--glow-blue)',
                color: saving ? 'var(--text-muted)' : 'white',
              }}
            >
              {saving ? 'Saving...' : 'Save Changes'}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

/** Device Management sub-component for Settings page */
function DeviceManagement() {
  const [devices, setDevices] = useState<ApprovedDevice[]>([]);
  const [loading, setLoading] = useState(true);
  const [revoking, setRevoking] = useState<string | null>(null);
  const [rotating, setRotating] = useState(false);
  const toast = useToast();

  const loadDevices = useCallback(() => {
    setLoading(true);
    fetch('/api/auth/devices')
      .then(res => res.json())
      .then(data => setDevices(data.devices || []))
      .catch(() => { /* auth might not be enabled */ })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { loadDevices(); }, [loadDevices]);

  const revokeDevice = async (deviceId: string) => {
    setRevoking(deviceId);
    try {
      const res = await fetch(`/api/auth/devices/${deviceId}`, { method: 'DELETE' });
      if (res.ok) {
        toast.success('Device revoked');
        loadDevices();
      } else {
        toast.error('Failed to revoke device');
      }
    } catch {
      toast.error('Connection error');
    } finally {
      setRevoking(null);
    }
  };

  const rotateCode = async () => {
    setRotating(true);
    try {
      const res = await fetch('/api/auth/rotate-code', { method: 'POST' });
      if (res.ok) {
        toast.success('Access code rotated. Check the terminal for the new code.');
      } else {
        toast.error('Failed to rotate code');
      }
    } catch {
      toast.error('Connection error');
    } finally {
      setRotating(false);
    }
  };

  const formatDate = (value: string | number) => {
    try {
      // Handle Unix timestamps (seconds) — multiply by 1000 for JS Date
      let d: Date;
      if (typeof value === 'number' || /^\d+(\.\d+)?$/.test(String(value))) {
        const ts = typeof value === 'number' ? value : parseFloat(value);
        // If timestamp is too small for milliseconds, it's in seconds
        d = new Date(ts < 1e12 ? ts * 1000 : ts);
      } else {
        d = new Date(value);
      }
      if (isNaN(d.getTime())) return String(value);
      return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    } catch { return String(value); }
  };

  const parseUA = (ua: string) => {
    if (ua.includes('iPhone')) return 'iPhone';
    if (ua.includes('iPad')) return 'iPad';
    if (ua.includes('Android')) return 'Android';
    if (ua.includes('Mac')) return 'Mac';
    if (ua.includes('Windows')) return 'Windows';
    if (ua.includes('Linux')) return 'Linux';
    return 'Unknown device';
  };

  return (
    <div
      className="rounded-2xl overflow-hidden"
      style={{ background: 'var(--bg-card)', border: '1px solid var(--border-dim)' }}
    >
      <div className="px-5 py-3.5 flex items-center justify-between" style={{ borderBottom: '1px solid var(--border-dim)' }}>
        <div className="flex items-center gap-2.5">
          <span className="text-base">📱</span>
          <h2 className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>Approved Devices</h2>
          <span className="text-xs px-1.5 py-0.5 rounded-md" style={{ background: 'var(--bg-elevated)', color: 'var(--text-muted)' }}>
            {devices.length}
          </span>
        </div>
        <button
          onClick={rotateCode}
          disabled={rotating}
          className="text-xs px-3 py-1.5 rounded-lg transition-colors"
          style={{
            background: 'var(--bg-elevated)',
            color: 'var(--accent-amber, #f59e0b)',
            border: '1px solid var(--border-subtle)',
          }}
          title="Generate a new access code (old code stops working)"
        >
          {rotating ? 'Rotating...' : 'Rotate Access Code'}
        </button>
      </div>
      <div>
        {loading ? (
          <div className="px-5 py-8 text-center text-sm" style={{ color: 'var(--text-muted)' }}>Loading devices...</div>
        ) : devices.length === 0 ? (
          <div className="px-5 py-8 text-center text-sm" style={{ color: 'var(--text-muted)' }}>No devices approved yet</div>
        ) : (
          devices.map((device, i) => (
            <div
              key={device.device_id}
              className="px-5 py-3.5 flex items-center justify-between gap-4"
              style={{ borderBottom: i < devices.length - 1 ? '1px solid var(--border-dim)' : 'none' }}
            >
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="text-sm" style={{ color: 'var(--text-primary)' }}>
                    {device.name || parseUA(device.user_agent)}
                  </span>
                  <span className="text-xs px-1.5 py-0.5 rounded" style={{ background: 'var(--bg-elevated)', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
                    {device.ip}
                  </span>
                </div>
                <div className="text-xs mt-0.5" style={{ color: 'var(--text-muted)' }}>
                  Approved: {formatDate(device.approved_at)} &middot; Last seen: {formatDate(device.last_seen)}
                </div>
              </div>
              <button
                onClick={() => revokeDevice(device.device_id)}
                disabled={revoking === device.device_id}
                className="text-xs px-3 py-1.5 rounded-lg transition-colors flex-shrink-0"
                style={{
                  background: 'rgba(239, 68, 68, 0.1)',
                  color: 'var(--accent-red, #ef4444)',
                  border: '1px solid rgba(239, 68, 68, 0.2)',
                }}
              >
                {revoking === device.device_id ? 'Revoking...' : 'Revoke'}
              </button>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

import { useState } from 'react';
import type { CircleMember } from '../../types';

interface CircleMembersProps {
  members: CircleMember[];
  onAddMember: (userId: string, role?: string) => Promise<boolean>;
  onRemoveMember: (userId: string) => Promise<boolean>;
}

const ROLE_COLORS: Record<string, string> = {
  owner: 'var(--accent-amber)',
  admin: 'var(--accent-purple)',
  member: 'var(--accent-blue)',
  viewer: 'var(--text-muted)',
};

export default function CircleMembers({ members, onAddMember, onRemoveMember }: CircleMembersProps): JSX.Element {
  const [inviteId, setInviteId] = useState('');
  const [inviting, setInviting] = useState(false);

  const handleInvite = async (): Promise<void> => {
    if (!inviteId.trim()) return;
    setInviting(true);
    const ok = await onAddMember(inviteId.trim());
    if (ok) setInviteId('');
    setInviting(false);
  };

  return (
    <div className="space-y-4">
      {/* Invite form */}
      <div className="flex gap-2">
        <input
          type="text"
          value={inviteId}
          onChange={e => setInviteId(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') handleInvite(); }}
          placeholder="User ID to invite..."
          className="hivemind-input flex-1 px-3 py-2 text-sm rounded-xl"
          aria-label="User ID to invite"
        />
        <button
          onClick={handleInvite}
          disabled={inviting || !inviteId.trim()}
          className="px-4 py-2 text-sm font-medium rounded-xl transition-all duration-200 disabled:opacity-40"
          style={{
            background: 'var(--accent-blue)',
            color: 'white',
          }}
          aria-label="Invite member"
        >
          {inviting ? (
            <span className="inline-block w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
          ) : (
            'Invite'
          )}
        </button>
      </div>

      {/* Member list */}
      <div className="space-y-1">
        {members.map((member, i) => (
          <div
            key={member.user_id}
            className="flex items-center gap-3 px-3 py-2.5 rounded-xl transition-colors duration-150 stagger-item"
            style={{
              animationDelay: `${i * 40}ms`,
              background: 'var(--bg-card)',
            }}
          >
            {/* Avatar placeholder */}
            <div
              className="w-8 h-8 rounded-lg flex items-center justify-center text-xs font-bold flex-shrink-0"
              style={{
                background: 'var(--bg-elevated)',
                color: 'var(--text-secondary)',
              }}
            >
              {member.user_id.slice(0, 2).toUpperCase()}
            </div>

            <div className="flex-1 min-w-0">
              <span className="text-sm font-medium truncate block" style={{ color: 'var(--text-primary)' }}>
                {member.user_id}
              </span>
            </div>

            {/* Role badge */}
            <span
              className="text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-md"
              style={{
                color: ROLE_COLORS[member.role] || 'var(--text-muted)',
                background: `color-mix(in srgb, ${ROLE_COLORS[member.role] || 'var(--text-muted)'} 12%, transparent)`,
              }}
            >
              {member.role}
            </span>

            {/* Remove button (not for owners) */}
            {member.role !== 'owner' && (
              <button
                onClick={() => onRemoveMember(member.user_id)}
                className="p-1.5 rounded-lg transition-colors duration-150 opacity-0 group-hover:opacity-100 hover:opacity-100 focus-visible:opacity-100"
                style={{ color: 'var(--accent-red)' }}
                aria-label={`Remove ${member.user_id}`}
                title="Remove member"
              >
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none">
                  <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
                </svg>
              </button>
            )}
          </div>
        ))}

        {members.length === 0 && (
          <div className="text-center py-8" style={{ color: 'var(--text-muted)' }}>
            <p className="text-sm">No members yet</p>
            <p className="text-xs mt-1">Invite someone to get started</p>
          </div>
        )}
      </div>
    </div>
  );
}

import { useState, useRef, useEffect } from 'react';

interface CreateCircleDialogProps {
  isOpen: boolean;
  onClose: () => void;
  onCreate: (data: { name: string; description?: string }) => Promise<unknown>;
}

export default function CreateCircleDialog({ isOpen, onClose, onCreate }: CreateCircleDialogProps): JSX.Element | null {
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [creating, setCreating] = useState(false);
  const nameRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (isOpen) {
      setName('');
      setDescription('');
      setTimeout(() => nameRef.current?.focus(), 100);
    }
  }, [isOpen]);

  // Close on Escape
  useEffect(() => {
    if (!isOpen) return;
    const handleEsc = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handleEsc);
    return () => document.removeEventListener('keydown', handleEsc);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  const handleSubmit = async (e: React.FormEvent): Promise<void> => {
    e.preventDefault();
    if (!name.trim() || creating) return;
    setCreating(true);
    await onCreate({ name: name.trim(), description: description.trim() || undefined });
    setCreating(false);
    onClose();
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      role="dialog"
      aria-modal="true"
      aria-label="Create new circle"
    >
      {/* Backdrop */}
      <div
        className="absolute inset-0"
        style={{ background: 'rgba(0, 0, 0, 0.6)', backdropFilter: 'blur(4px)' }}
        onClick={onClose}
        aria-hidden="true"
      />

      {/* Dialog */}
      <form
        onSubmit={handleSubmit}
        className="relative w-full max-w-md rounded-2xl p-6 space-y-5"
        style={{
          background: 'var(--bg-panel)',
          border: '1px solid var(--border-subtle)',
          boxShadow: '0 24px 48px rgba(0, 0, 0, 0.4)',
          animation: 'slideUp 0.25s ease-out',
        }}
      >
        <div className="flex items-center justify-between">
          <h2
            className="text-lg font-bold"
            style={{ color: 'var(--text-primary)', fontFamily: 'var(--font-display)' }}
          >
            Create Circle
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="p-1.5 rounded-lg transition-colors duration-150"
            style={{ color: 'var(--text-muted)' }}
            aria-label="Close dialog"
          >
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
              <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            </svg>
          </button>
        </div>

        <div className="space-y-4">
          <div>
            <label
              htmlFor="circle-name"
              className="block text-xs font-semibold uppercase tracking-wider mb-1.5"
              style={{ color: 'var(--text-muted)' }}
            >
              Name
            </label>
            <input
              ref={nameRef}
              id="circle-name"
              type="text"
              value={name}
              onChange={e => setName(e.target.value)}
              placeholder="e.g. Frontend Team"
              className="hivemind-input w-full px-3 py-2.5 text-sm rounded-xl"
              maxLength={255}
              required
            />
          </div>

          <div>
            <label
              htmlFor="circle-desc"
              className="block text-xs font-semibold uppercase tracking-wider mb-1.5"
              style={{ color: 'var(--text-muted)' }}
            >
              Description
              <span className="text-[10px] font-normal ml-1 normal-case">(optional)</span>
            </label>
            <textarea
              id="circle-desc"
              value={description}
              onChange={e => setDescription(e.target.value)}
              placeholder="What is this circle about?"
              className="hivemind-input w-full px-3 py-2.5 text-sm rounded-xl resize-none"
              rows={3}
              maxLength={2000}
            />
          </div>
        </div>

        <div className="flex gap-3 justify-end pt-2">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2.5 text-sm font-medium rounded-xl transition-colors duration-150"
            style={{ color: 'var(--text-secondary)', background: 'var(--bg-elevated)' }}
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={!name.trim() || creating}
            className="px-5 py-2.5 text-sm font-semibold rounded-xl transition-all duration-200 disabled:opacity-40"
            style={{
              background: 'linear-gradient(135deg, var(--accent-blue), var(--accent-purple))',
              color: 'white',
              boxShadow: '0 3px 12px var(--glow-blue)',
            }}
          >
            {creating ? (
              <span className="flex items-center gap-2">
                <span className="inline-block w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                Creating...
              </span>
            ) : (
              'Create Circle'
            )}
          </button>
        </div>
      </form>
    </div>
  );
}

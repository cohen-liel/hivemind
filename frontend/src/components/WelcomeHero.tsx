interface WelcomeHeroProps {
  onNewProject?: () => void;
  onSelectTemplate?: () => void;
  projectCount?: number;
}

export default function WelcomeHero({
  onNewProject,
  onSelectTemplate,
  projectCount = 0,
}: WelcomeHeroProps) {
  if (projectCount > 0) return null;

  return (
    <div
      className="rounded-2xl p-8 md:p-12 text-center animate-fade-in-up relative overflow-hidden"
      style={{
        background: 'linear-gradient(135deg, var(--bg-card) 0%, var(--bg-elevated) 100%)',
        border: '1px solid var(--border-subtle)',
      }}
    >
      {/* Background glow effect */}
      <div
        className="absolute inset-0 opacity-20"
        style={{
          background:
            'radial-gradient(ellipse at 50% 0%, rgba(139, 92, 246, 0.15) 0%, transparent 70%)',
        }}
      />

      <div className="relative z-10">
        <div className="text-5xl mb-4">{'\u{1f41d}'}</div>
        <h1
          className="text-2xl md:text-3xl font-bold mb-3"
          style={{ color: 'var(--text-primary)' }}
        >
          Your AI Engineering Team is Ready
        </h1>
        <p
          className="text-base md:text-lg mb-8 max-w-xl mx-auto"
          style={{ color: 'var(--text-secondary)' }}
        >
          One prompt. A full engineering team. Production-ready code.
          <br />
          <span style={{ color: 'var(--text-muted)' }}>
            Go lie on the couch.
          </span>
        </p>

        <div className="flex flex-col sm:flex-row gap-3 justify-center">
          <button
            onClick={onNewProject}
            className="px-6 py-3 rounded-xl font-semibold text-white transition-all duration-200 hover:scale-[1.02] active:scale-[0.98]"
            style={{
              background: 'linear-gradient(135deg, #7c3aed 0%, #6d28d9 100%)',
              boxShadow: '0 0 20px -5px rgba(124, 58, 237, 0.5)',
            }}
          >
            + New Project
          </button>
          <button
            onClick={onSelectTemplate}
            className="px-6 py-3 rounded-xl font-semibold transition-all duration-200 hover:scale-[1.02] active:scale-[0.98]"
            style={{
              background: 'var(--bg-elevated)',
              color: 'var(--text-primary)',
              border: '1px solid var(--border-subtle)',
            }}
          >
            Browse Templates
          </button>
        </div>

        <div
          className="mt-6 flex items-center justify-center gap-6 text-xs font-mono"
          style={{ color: 'var(--text-muted)' }}
        >
          <span className="flex items-center gap-1.5">
            <span
              className="w-2 h-2 rounded-full"
              style={{ background: 'var(--accent-green)' }}
            />
            11 Agents Online
          </span>
          <span className="flex items-center gap-1.5">
            <span
              className="w-2 h-2 rounded-full"
              style={{ background: 'var(--accent-blue)' }}
            />
            DAG Engine Ready
          </span>
          <span className="flex items-center gap-1.5">
            <span
              className="w-2 h-2 rounded-full"
              style={{ background: 'var(--accent-purple)' }}
            />
            Multi-Runtime
          </span>
        </div>
      </div>
    </div>
  );
}

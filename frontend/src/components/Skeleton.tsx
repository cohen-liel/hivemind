// ============================================================
// SKELETON LOADING COMPONENTS
// ============================================================

/** Generic skeleton block */
export function SkeletonBlock({ width, height, className = '' }: { width?: string; height?: string; className?: string }) {
  return (
    <div
      className={`skeleton ${className}`}
      role="presentation"
      aria-hidden="true"
      style={{ width: width ?? '100%', height: height ?? '16px' }}
    />
  );
}

/** Skeleton for a stat card (Dashboard) */
export function SkeletonStatCard() {
  return (
    <div
      className="rounded-2xl p-5"
      role="presentation"
      aria-hidden="true"
      style={{ background: 'var(--bg-card)', border: '1px solid var(--border-dim)' }}
    >
      <div className="flex items-center justify-between mb-3">
        <SkeletonBlock width="80px" height="12px" />
        <SkeletonBlock width="32px" height="32px" className="rounded-xl" />
      </div>
      <SkeletonBlock width="120px" height="28px" className="mb-2" />
      <SkeletonBlock width="60px" height="12px" />
    </div>
  );
}

/** Skeleton for a project card (Dashboard) */
export function SkeletonProjectCard() {
  return (
    <div
      className="rounded-2xl p-4"
      role="presentation"
      aria-hidden="true"
      style={{ background: 'var(--bg-card)', border: '1px solid var(--border-dim)' }}
    >
      <div className="flex items-center gap-3 mb-3">
        <SkeletonBlock width="10px" height="10px" className="rounded-full" />
        <SkeletonBlock width="140px" height="16px" />
        <div className="ml-auto">
          <SkeletonBlock width="60px" height="20px" className="rounded-full" />
        </div>
      </div>
      <SkeletonBlock width="200px" height="12px" className="mb-2" />
      <SkeletonBlock width="100px" height="12px" />
    </div>
  );
}

/** Skeleton for a settings form */
export function SkeletonSettingsForm() {
  return (
    <div className="space-y-6" role="presentation" aria-hidden="true">
      {[1, 2, 3].map(i => (
        <div key={i}>
          <SkeletonBlock width="100px" height="12px" className="mb-2" />
          <SkeletonBlock width="100%" height="40px" className="rounded-xl" />
        </div>
      ))}
      <SkeletonBlock width="100px" height="36px" className="rounded-xl mt-4" />
    </div>
  );
}

/** Skeleton for a schedule row */
export function SkeletonScheduleRow() {
  return (
    <div
      className="flex items-center gap-4 p-4 rounded-xl"
      role="presentation"
      aria-hidden="true"
      style={{ background: 'var(--bg-card)', border: '1px solid var(--border-dim)' }}
    >
      <SkeletonBlock width="40px" height="40px" className="rounded-xl" />
      <div className="flex-1 space-y-2">
        <SkeletonBlock width="160px" height="14px" />
        <SkeletonBlock width="100px" height="12px" />
      </div>
      <SkeletonBlock width="60px" height="24px" className="rounded-full" />
    </div>
  );
}

/** Full page skeleton for Dashboard */
export function DashboardSkeleton() {
  return (
    <div className="p-6 space-y-6 page-enter" aria-busy="true" aria-label="Loading dashboard…">
      {/* Header */}
      <div className="flex items-center justify-between" aria-hidden="true">
        <div>
          <SkeletonBlock width="200px" height="28px" className="mb-2" />
          <SkeletonBlock width="300px" height="14px" />
        </div>
        <SkeletonBlock width="120px" height="40px" className="rounded-xl" />
      </div>

      {/* Stat cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4" aria-hidden="true">
        {[1, 2, 3, 4].map(i => (
          <SkeletonStatCard key={i} />
        ))}
      </div>

      {/* Project list */}
      <div aria-hidden="true">
        <SkeletonBlock width="120px" height="16px" className="mb-4" />
        <div className="space-y-3">
          {[1, 2, 3].map(i => (
            <SkeletonProjectCard key={i} />
          ))}
        </div>
      </div>
    </div>
  );
}

/** Full page skeleton for Settings */
export function SettingsSkeleton() {
  return (
    <div className="p-6 max-w-2xl mx-auto space-y-6 page-enter" aria-busy="true" aria-label="Loading settings…">
      <SkeletonBlock width="160px" height="28px" className="mb-6" />
      {[1, 2].map(section => (
        <div
          key={section}
          className="rounded-2xl p-6"
          aria-hidden="true"
          style={{ background: 'var(--bg-card)', border: '1px solid var(--border-dim)' }}
        >
          <SkeletonBlock width="140px" height="18px" className="mb-4" />
          <SkeletonSettingsForm />
        </div>
      ))}
    </div>
  );
}

/** Full page skeleton for Schedules */
export function SchedulesSkeleton() {
  return (
    <div className="p-6 max-w-3xl mx-auto space-y-4 page-enter" aria-busy="true" aria-label="Loading schedules…">
      <div className="flex items-center justify-between mb-4" aria-hidden="true">
        <SkeletonBlock width="160px" height="28px" />
        <SkeletonBlock width="120px" height="36px" className="rounded-xl" />
      </div>
      {[1, 2, 3].map(i => (
        <SkeletonScheduleRow key={i} />
      ))}
    </div>
  );
}

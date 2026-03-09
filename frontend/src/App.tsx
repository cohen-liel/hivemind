import { BrowserRouter, Routes, Route, useLocation, useNavigate, Link } from 'react-router-dom';
import { WebSocketProvider } from './WebSocketContext';
import { ThemeProvider } from './ThemeContext';
import { ToastProvider } from './components/Toast';
import { ErrorBoundary } from './components/ErrorBoundary';
import KeyboardShortcutsModal from './components/KeyboardShortcutsModal';
import Sidebar from './components/Sidebar';
import Dashboard from './pages/Dashboard';
import ProjectView from './pages/ProjectView';
import SettingsPage from './pages/SettingsPage';
import SchedulesPage from './pages/SchedulesPage';
import NewProjectDialog from './components/NewProjectDialog';
import { useEffect, useCallback, useState } from 'react';

/** Global keyboard shortcuts */
function KeyboardShortcuts() {
  const navigate = useNavigate();
  const location = useLocation();
  const [showShortcuts, setShowShortcuts] = useState(false);

  const handler = useCallback((e: KeyboardEvent) => {
    // Don't intercept when typing in inputs or contenteditable
    const tag = (e.target as HTMLElement)?.tagName;
    const isEditable = (e.target as HTMLElement)?.isContentEditable;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || isEditable) return;

    // '?' → show shortcuts modal
    if (e.key === '?' && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      setShowShortcuts(prev => !prev);
      return;
    }

    // Ctrl/Cmd + N → new project
    if ((e.ctrlKey || e.metaKey) && e.key === 'n') {
      e.preventDefault();
      navigate('/new');
    }
    // Ctrl/Cmd + , → settings
    if ((e.ctrlKey || e.metaKey) && e.key === ',') {
      e.preventDefault();
      navigate('/settings');
    }
    // Escape → go back to dashboard (if not already there)
    if (e.key === 'Escape' && location.pathname !== '/') {
      // Don't navigate if a modal might be open
      const hasModal = document.querySelector('[role="dialog"]');
      if (!hasModal) navigate('/');
    }
  }, [navigate, location.pathname]);

  useEffect(() => {
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [handler]);

  return (
    <KeyboardShortcutsModal
      isOpen={showShortcuts}
      onClose={() => setShowShortcuts(false)}
    />
  );
}

/** Mobile bottom navigation bar — only visible on small screens */
function MobileBottomNav() {
  const location = useLocation();

  // Hide on project view (it has its own mobile nav)
  if (location.pathname.startsWith('/project/')) return null;

  const items = [
    {
      path: '/',
      label: 'Projects',
      icon: (
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <rect x="3" y="3" width="7" height="7" rx="1.5"/>
          <rect x="14" y="3" width="7" height="7" rx="1.5"/>
          <rect x="3" y="14" width="7" height="7" rx="1.5"/>
          <rect x="14" y="14" width="7" height="7" rx="1.5"/>
        </svg>
      ),
    },
    {
      path: '/new',
      label: 'New',
      icon: (
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
          <circle cx="12" cy="12" r="9"/>
          <path d="M12 8v8M8 12h8"/>
        </svg>
      ),
    },
    {
      path: '/schedules',
      label: 'Schedules',
      icon: (
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="9"/>
          <path d="M12 7v5l3 3"/>
        </svg>
      ),
    },
    {
      path: '/settings',
      label: 'Settings',
      icon: (
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="3"/>
          <path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 01-2.83 2.83l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-4 0v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83-2.83l.06-.06A1.65 1.65 0 004.68 15a1.65 1.65 0 00-1.51-1H3a2 2 0 010-4h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 012.83-2.83l.06.06A1.65 1.65 0 009 4.68a1.65 1.65 0 001-1.51V3a2 2 0 014 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 2.83l-.06.06A1.65 1.65 0 0019.4 9a1.65 1.65 0 001.51 1H21a2 2 0 010 4h-.09a1.65 1.65 0 00-1.51 1z"/>
        </svg>
      ),
    },
  ];

  return (
    <nav
      className="lg:hidden fixed bottom-0 left-0 right-0 z-40 flex items-center justify-around safe-area-bottom"
      aria-label="Mobile navigation"
      style={{
        background: 'var(--bg-panel)',
        borderTop: '1px solid var(--border-dim)',
        backdropFilter: 'blur(12px)',
        paddingTop: '6px',
        paddingBottom: 'max(6px, env(safe-area-inset-bottom, 6px))',
      }}
    >
      {items.map(item => {
        const isActive = item.path === '/'
          ? location.pathname === '/'
          : location.pathname.startsWith(item.path);
        return (
          <Link
            key={item.path}
            to={item.path}
            aria-current={isActive ? 'page' : undefined}
            aria-label={item.label}
            className="flex flex-col items-center gap-0.5 px-3 py-1 rounded-lg transition-colors"
            style={{
              color: isActive ? 'var(--accent-blue)' : 'var(--text-muted)',
              opacity: isActive ? 1 : 0.6,
            }}
          >
            <span aria-hidden="true">{item.icon}</span>
            <span className="text-[9px] font-medium" aria-hidden="true">{item.label}</span>
          </Link>
        );
      })}
    </nav>
  );
}

/** Page wrapper with fade-in animation */
function AnimatedRoutes() {
  const location = useLocation();

  return (
    <div key={location.pathname} className="animate-[fadeSlideIn_0.2s_ease-out] h-full">
      <Routes location={location}>
        <Route path="/" element={<Dashboard />} />
        <Route path="/project/:id" element={<ProjectView />} />
        <Route path="/new" element={<NewProjectDialog />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="/schedules" element={<SchedulesPage />} />
      </Routes>
    </div>
  );
}

export default function App() {
  return (
    <ErrorBoundary>
      <ThemeProvider>
        <BrowserRouter>
          <WebSocketProvider>
            <ToastProvider>
              <div className="flex h-screen overflow-hidden" style={{ background: 'var(--bg-void)' }}>
                {/* Sidebar: hidden on mobile, visible on desktop */}
                <div className="hidden lg:flex">
                  <Sidebar />
                </div>
                <main className="flex-1 overflow-y-auto min-w-0 pb-14 lg:pb-0">
                  <KeyboardShortcuts />
                  <ErrorBoundary>
                    <AnimatedRoutes />
                  </ErrorBoundary>
                </main>
                {/* Mobile bottom nav */}
                <MobileBottomNav />
              </div>
            </ToastProvider>
          </WebSocketProvider>
        </BrowserRouter>
      </ThemeProvider>
    </ErrorBoundary>
  );
}

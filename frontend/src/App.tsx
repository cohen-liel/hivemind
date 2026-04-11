import { BrowserRouter, Routes, Route, useLocation, useNavigate, useParams, Link } from 'react-router-dom';
import { WebSocketProvider } from './WebSocketContext';
import { ThemeProvider } from './ThemeContext';
import { ToastProvider } from './components/Toast';
import { ErrorBoundary } from './components/ErrorBoundary';
import KeyboardShortcutsModal from './components/KeyboardShortcutsModal';
import Sidebar from './components/Sidebar';
import ProjectView from './pages/ProjectView';
import NewProjectDialog from './components/NewProjectDialog';
import WSReconnectBanner from './components/WSReconnectBanner';
import RouteLoadingFallback from './components/RouteLoadingFallback';
import LoginScreen from './components/LoginScreen';
import { useWSConnectionToast } from './hooks/useWSConnectionToast';
import { lazy, Suspense, useEffect, useCallback, useState, useRef } from 'react';

// ── Lazy-loaded route chunks (ARCH-01) ───────────────────────────
// Dashboard, SettingsPage, and SchedulesPage are code-split into
// separate chunks so the initial bundle stays small. Vite produces
// automatic chunk files for each React.lazy() import.
const Dashboard = lazy(() => import('./pages/Dashboard'));
const SettingsPage = lazy(() => import('./pages/SettingsPage'));
const SchedulesPage = lazy(() => import('./pages/SchedulesPage'));
const PluginsPage = lazy(() => import('./pages/PluginsPage'));
const DagPage = lazy(() => import('./pages/DagPage'));

/**
 * Mounts global WS-to-toast notifications.
 * Must be inside both ToastProvider and WebSocketProvider.
 */
function GlobalWSHandlers() {
  useWSConnectionToast();
  return null;
}

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
      path: '/plugins',
      label: 'Plugins',
      icon: (
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="M9 3H5a2 2 0 00-2 2v4m6-6h10a2 2 0 012 2v4M9 3v18m0 0h10a2 2 0 002-2V9M9 21H5a2 2 0 01-2-2V9m0 0h18"/>
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

/** Forces full remount of ProjectView when navigating between projects */
function KeyedProjectView() {
  const { id } = useParams<{ id: string }>();
  return <ProjectView key={id} />;
}

/** Page wrapper with fade-in animation and Suspense for lazy routes */
function AnimatedRoutes(): JSX.Element {
  const location = useLocation();
  const containerRef = useRef<HTMLDivElement>(null);
  const prevPathRef = useRef<string>(location.pathname);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    // Only animate when the top-level route segment changes
    if (prevPathRef.current === location.pathname) return;
    prevPathRef.current = location.pathname;

    // Re-trigger the pageEnter animation by removing then re-adding the class
    el.classList.remove('page-enter');
    // Force a reflow so the browser registers the class removal
    void el.offsetHeight;
    el.classList.add('page-enter');
  }, [location.pathname]);

  return (
    <div ref={containerRef} className="h-full page-enter">
      <Suspense fallback={<RouteLoadingFallback />}>
        <Routes location={location}>
          <Route path="/" element={<Dashboard />} />
          <Route path="/project/:id" element={<KeyedProjectView />} />
          <Route path="/projects/:projectId/dag" element={<DagPage />} />
          <Route path="/new" element={<NewProjectDialog />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/schedules" element={<SchedulesPage />} />
          <Route path="/plugins" element={<PluginsPage />} />
        </Routes>
      </Suspense>
    </div>
  );
}

export default function App() {
  const [authState, setAuthState] = useState<'checking' | 'authenticated' | 'unauthenticated'>('checking');

  useEffect(() => {
    // Check if device is already authenticated
    fetch('/api/auth/status')
      .then(res => res.json())
      .then(data => {
        setAuthState(data.authenticated ? 'authenticated' : 'unauthenticated');
      })
      .catch(() => {
        // If auth endpoint fails (e.g., old server without auth), allow access
        setAuthState('authenticated');
      });

    // Listen for auth expiry from API calls (401 responses)
    const handleAuthExpired = () => setAuthState('unauthenticated');
    window.addEventListener('hivemind-auth-expired', handleAuthExpired);
    return () => window.removeEventListener('hivemind-auth-expired', handleAuthExpired);
  }, []);

  // Show loading while checking auth
  if (authState === 'checking') {
    return (
      <div style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'var(--bg-void, #0a0a0f)',
      }}>
        <div style={{
          width: '24px',
          height: '24px',
          border: '2px solid #27272a',
          borderTopColor: '#6366f1',
          borderRadius: '50%',
          animation: 'spin 0.8s linear infinite',
        }} />
        <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
      </div>
    );
  }

  // Show login screen if not authenticated
  if (authState === 'unauthenticated') {
    return (
      <ErrorBoundary>
        <ThemeProvider>
          <LoginScreen onAuthenticated={() => setAuthState('authenticated')} />
        </ThemeProvider>
      </ErrorBoundary>
    );
  }

  return (
    <ErrorBoundary>
      <ThemeProvider>
        <BrowserRouter>
          <WebSocketProvider>
            <ToastProvider>
              {/* Global WS toast + reconnect banner */}
              <GlobalWSHandlers />
              <WSReconnectBanner />
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

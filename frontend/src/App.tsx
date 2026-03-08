import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { WebSocketProvider } from './WebSocketContext';
import Sidebar from './components/Sidebar';
import Dashboard from './pages/Dashboard';
import ProjectView from './pages/ProjectView';
import SettingsPage from './pages/SettingsPage';
import SchedulesPage from './pages/SchedulesPage';
import NewProjectDialog from './components/NewProjectDialog';

export default function App() {
  return (
    <BrowserRouter>
      <WebSocketProvider>
        <div className="flex h-screen overflow-hidden" style={{ background: 'var(--bg-void)' }}>
          {/* Sidebar: hidden on mobile, visible on desktop */}
          <div className="hidden lg:flex">
            <Sidebar />
          </div>
          <main className="flex-1 overflow-y-auto min-w-0">
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/project/:id" element={<ProjectView />} />
              <Route path="/new" element={<NewProjectDialog />} />
              <Route path="/settings" element={<SettingsPage />} />
              <Route path="/schedules" element={<SchedulesPage />} />
            </Routes>
          </main>
        </div>
      </WebSocketProvider>
    </BrowserRouter>
  );
}

import { BrowserRouter, Routes, Route } from 'react-router-dom';
import Sidebar from './components/Sidebar';
import Dashboard from './pages/Dashboard';
import ProjectView from './pages/ProjectView';
import SettingsPage from './pages/SettingsPage';
import NewProjectDialog from './components/NewProjectDialog';

export default function App() {
  return (
    <BrowserRouter>
      <div className="flex h-screen bg-gray-950 overflow-hidden">
        <Sidebar />
        <main className="flex-1 overflow-y-auto">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/project/:id" element={<ProjectView />} />
            <Route path="/new" element={<NewProjectDialog />} />
            <Route path="/settings" element={<SettingsPage />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
}

import { useState } from 'react';

interface Template {
  name: string;
  description: string;
  tags: string[];
  estimated_time_minutes: number;
  team_size: string;
}

const TEMPLATES: Template[] = [
  {
    name: 'SaaS Starter',
    description: 'Full-stack SaaS with auth, billing, and dashboard. React + FastAPI + PostgreSQL.',
    tags: ['saas', 'fullstack', 'auth', 'billing'],
    estimated_time_minutes: 30,
    team_size: 'full',
  },
  {
    name: 'REST API',
    description: 'Production-ready FastAPI backend with PostgreSQL, auth, and Docker.',
    tags: ['api', 'backend', 'fastapi', 'docker'],
    estimated_time_minutes: 15,
    team_size: 'team',
  },
  {
    name: 'React Dashboard',
    description: 'Admin dashboard with charts, tables, dark mode, and responsive design.',
    tags: ['frontend', 'dashboard', 'react', 'charts'],
    estimated_time_minutes: 20,
    team_size: 'team',
  },
  {
    name: 'CLI Tool',
    description: 'Professional Python CLI with subcommands, config, and colored output.',
    tags: ['cli', 'python', 'tool'],
    estimated_time_minutes: 10,
    team_size: 'solo',
  },
  {
    name: 'Mobile App',
    description: 'Cross-platform Expo + React Native app with navigation and auth.',
    tags: ['mobile', 'react-native', 'expo'],
    estimated_time_minutes: 25,
    team_size: 'full',
  },
];

const teamSizeIcons: Record<string, string> = {
  solo: '\u{1f9d1}\u200d\u{1f4bb}',
  team: '\u{1f465}',
  full: '\u{1f3c6}',
};

const teamSizeLabels: Record<string, string> = {
  solo: '1 Agent',
  team: '3-5 Agents',
  full: 'Full Team',
};

interface TemplateGalleryProps {
  onSelect?: (templateName: string) => void;
}

export default function TemplateGallery({ onSelect }: TemplateGalleryProps) {
  const [hoveredIdx, setHoveredIdx] = useState<number | null>(null);

  return (
    <div className="animate-fade-in">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2
            className="text-lg font-semibold"
            style={{ color: 'var(--text-primary)' }}
          >
            Project Templates
          </h2>
          <p
            className="text-sm mt-1"
            style={{ color: 'var(--text-muted)' }}
          >
            Pick a template. The team handles the rest.
          </p>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {TEMPLATES.map((template, idx) => (
          <button
            key={template.name}
            onClick={() => onSelect?.(template.name)}
            onMouseEnter={() => setHoveredIdx(idx)}
            onMouseLeave={() => setHoveredIdx(null)}
            className="text-left rounded-xl p-5 transition-all duration-200 border"
            style={{
              background: hoveredIdx === idx ? 'var(--bg-elevated)' : 'var(--bg-card)',
              borderColor: hoveredIdx === idx ? 'var(--border-active)' : 'var(--border-dim)',
              boxShadow: hoveredIdx === idx ? '0 0 20px -5px rgba(139, 92, 246, 0.2)' : 'none',
            }}
          >
            <div className="flex items-start justify-between mb-3">
              <h3
                className="font-semibold text-base"
                style={{ color: 'var(--text-primary)' }}
              >
                {template.name}
              </h3>
              <span className="text-lg" title={teamSizeLabels[template.team_size]}>
                {teamSizeIcons[template.team_size]}
              </span>
            </div>

            <p
              className="text-sm mb-4 leading-relaxed"
              style={{ color: 'var(--text-secondary)' }}
            >
              {template.description}
            </p>

            <div className="flex items-center justify-between">
              <div className="flex flex-wrap gap-1.5">
                {template.tags.slice(0, 3).map((tag) => (
                  <span
                    key={tag}
                    className="text-2xs px-2 py-0.5 rounded-full font-medium"
                    style={{
                      background: 'var(--glow-blue)',
                      color: 'var(--accent-blue)',
                    }}
                  >
                    {tag}
                  </span>
                ))}
              </div>
              <span
                className="text-xs font-mono"
                style={{ color: 'var(--text-muted)' }}
              >
                ~{template.estimated_time_minutes}m
              </span>
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}

import React, { useEffect, useState } from 'react';

// ============================================================================
// SVG Illustrations
// ============================================================================

function HivemindNetworkSVG(): React.ReactElement {
  return (
    <svg
      width="200"
      height="140"
      viewBox="0 0 200 140"
      fill="none"
      className="mx-auto"
      aria-hidden="true"
    >
      {/* Animated connection lines */}
      <line x1="100" y1="70" x2="40" y2="30" stroke="var(--accent-blue)" strokeWidth="1.2" opacity="0.25">
        <animate attributeName="opacity" values="0.15;0.35;0.15" dur="3s" repeatCount="indefinite" />
      </line>
      <line x1="100" y1="70" x2="160" y2="28" stroke="var(--accent-purple)" strokeWidth="1.2" opacity="0.25">
        <animate attributeName="opacity" values="0.15;0.35;0.15" dur="3.5s" repeatCount="indefinite" />
      </line>
      <line x1="100" y1="70" x2="155" y2="110" stroke="var(--accent-green)" strokeWidth="1.2" opacity="0.25">
        <animate attributeName="opacity" values="0.15;0.35;0.15" dur="2.8s" repeatCount="indefinite" />
      </line>
      <line x1="100" y1="70" x2="45" y2="108" stroke="var(--accent-cyan)" strokeWidth="1.2" opacity="0.2">
        <animate attributeName="opacity" values="0.12;0.3;0.12" dur="3.2s" repeatCount="indefinite" />
      </line>
      <line x1="100" y1="70" x2="100" y2="18" stroke="var(--accent-amber)" strokeWidth="1.2" opacity="0.2">
        <animate attributeName="opacity" values="0.12;0.3;0.12" dur="2.6s" repeatCount="indefinite" />
      </line>

      {/* Central hub — pulsing ring */}
      <circle cx="100" cy="70" r="22" fill="var(--glow-blue)" />
      <circle cx="100" cy="70" r="22" stroke="var(--accent-blue)" strokeWidth="1.5" fill="none" opacity="0.3">
        <animate attributeName="r" values="22;25;22" dur="2s" repeatCount="indefinite" />
        <animate attributeName="opacity" values="0.3;0.12;0.3" dur="2s" repeatCount="indefinite" />
      </circle>
      {/* Lightning bolt icon */}
      <path d="M104 62L96 72H104L96 82" stroke="var(--accent-blue)" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />

      {/* Agent node: PM */}
      <circle cx="40" cy="30" r="12" fill="var(--glow-blue)" />
      <circle cx="40" cy="30" r="12" stroke="var(--accent-blue)" strokeWidth="1" fill="none" opacity="0.3" />
      <path d="M36 34V26h4a3 3 0 010 6h-4" stroke="var(--accent-blue)" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" opacity="0.7" />

      {/* Agent node: Developer */}
      <circle cx="160" cy="28" r="11" fill="var(--glow-blue)" />
      <circle cx="160" cy="28" r="11" stroke="var(--accent-purple)" strokeWidth="1" fill="none" opacity="0.3" />
      <path d="M156 25l-3 3 3 3M164 25l3 3-3 3" stroke="var(--accent-purple)" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" opacity="0.7" />

      {/* Agent node: Reviewer */}
      <circle cx="155" cy="110" r="13" fill="var(--glow-green)" />
      <circle cx="155" cy="110" r="13" stroke="var(--accent-green)" strokeWidth="1" fill="none" opacity="0.3" />
      <path d="M149 110l4 4 8-8" stroke="var(--accent-green)" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" opacity="0.7" />

      {/* Agent node: QA */}
      <circle cx="45" cy="108" r="10" fill="var(--glow-blue)" />
      <circle cx="45" cy="108" r="10" stroke="var(--accent-cyan)" strokeWidth="1" fill="none" opacity="0.3" />
      <path d="M41 105h8M45 105v8" stroke="var(--accent-cyan)" strokeWidth="1.3" strokeLinecap="round" opacity="0.6" />

      {/* Agent node: Researcher */}
      <circle cx="100" cy="18" r="9" fill="var(--glow-blue)" />
      <circle cx="100" cy="18" r="9" stroke="var(--accent-amber)" strokeWidth="1" fill="none" opacity="0.3" />
      <circle cx="98" cy="16" r="3" stroke="var(--accent-amber)" strokeWidth="1.2" fill="none" opacity="0.6" />
      <line x1="100" y1="18.5" x2="103" y2="21.5" stroke="var(--accent-amber)" strokeWidth="1.2" strokeLinecap="round" opacity="0.6" />
    </svg>
  );
}

// ============================================================================
// Feature Card
// ============================================================================

interface FeatureItemProps {
  icon: React.ReactElement;
  label: string;
  desc: string;
  delay: number;
}

function FeatureItem({ icon, label, desc, delay }: FeatureItemProps): React.ReactElement {
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    const t = setTimeout(() => setVisible(true), delay);
    return () => clearTimeout(t);
  }, [delay]);

  return (
    <div
      className="flex items-start gap-3 text-left"
      style={{
        opacity: visible ? 1 : 0,
        transform: visible ? 'translateY(0)' : 'translateY(8px)',
        transition: 'opacity 0.4s ease-out, transform 0.4s ease-out',
      }}
    >
      <div
        className="w-9 h-9 rounded-xl flex items-center justify-center flex-shrink-0"
        style={{
          background: 'var(--bg-elevated)',
          border: '1px solid var(--border-dim)',
        }}
      >
        {icon}
      </div>
      <div className="min-w-0">
        <p className="text-xs font-bold" style={{ color: 'var(--text-primary)' }}>{label}</p>
        <p className="text-[11px] leading-relaxed" style={{ color: 'var(--text-muted)' }}>{desc}</p>
      </div>
    </div>
  );
}

// ============================================================================
// WelcomeHero Component
// ============================================================================

interface WelcomeHeroProps {
  onNewProject?: () => void;
  onSelectTemplate?: () => void;
  projectCount?: number;
}

export default function WelcomeHero({
  onNewProject,
  onSelectTemplate,
  projectCount = 0,
}: WelcomeHeroProps): React.ReactElement | null {
  const [mounted, setMounted] = useState(false);
  useEffect(() => {
    requestAnimationFrame(() => setMounted(true));
  }, []);

  if (projectCount > 0) return null;

  return (
    <div
      className="rounded-2xl p-6 sm:p-8 md:p-12 text-center relative overflow-hidden"
      style={{
        background: 'linear-gradient(135deg, var(--bg-card) 0%, var(--bg-elevated) 100%)',
        border: '1px solid var(--border-subtle)',
        opacity: mounted ? 1 : 0,
        transform: mounted ? 'translateY(0)' : 'translateY(16px)',
        transition: 'opacity 0.5s ease-out, transform 0.5s ease-out',
      }}
      role="region"
      aria-label="Welcome to Hivemind"
    >
      {/* Background glow */}
      <div
        className="absolute inset-0 pointer-events-none"
        style={{
          background:
            'radial-gradient(ellipse at 50% 0%, rgba(99,140,255,0.1) 0%, transparent 60%), radial-gradient(ellipse at 80% 100%, rgba(139,92,246,0.06) 0%, transparent 50%)',
        }}
      />

      <div className="relative z-10">
        {/* Network illustration */}
        <div
          className="mb-5"
          style={{
            opacity: mounted ? 1 : 0,
            transition: 'opacity 0.6s ease-out 0.15s',
          }}
        >
          <HivemindNetworkSVG />
        </div>

        <h1
          className="text-xl sm:text-2xl md:text-3xl font-bold mb-2"
          style={{ color: 'var(--text-primary)', fontFamily: 'var(--font-display)' }}
        >
          Your AI Engineering Team is Ready
        </h1>
        <p
          className="text-sm sm:text-base mb-6 max-w-md mx-auto leading-relaxed"
          style={{ color: 'var(--text-secondary)' }}
        >
          One prompt. A full engineering team. Production-ready code.
          <br />
          <span style={{ color: 'var(--text-muted)' }}>Go lie on the couch.</span>
        </p>

        {/* CTA Buttons */}
        <div className="flex flex-col sm:flex-row gap-3 justify-center mb-8">
          <button
            onClick={onNewProject}
            className="px-6 py-3 rounded-xl font-semibold text-white transition-all duration-200 hover:scale-[1.02] active:scale-[0.97] focus:outline-none focus:ring-2 focus:ring-[var(--accent-purple)] focus:ring-offset-2"
            style={{
              background: 'linear-gradient(135deg, #7c3aed 0%, #6d28d9 100%)',
              boxShadow: '0 0 20px -5px rgba(124, 58, 237, 0.5)',
              focusRingOffset: 'var(--bg-card)',
            } as React.CSSProperties}
            aria-label="Create a new project"
          >
            <span className="flex items-center justify-center gap-2">
              <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                <path d="M8 3v10M3 8h10" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
              </svg>
              Get Started
            </span>
          </button>
          <button
            onClick={onSelectTemplate}
            className="px-6 py-3 rounded-xl font-semibold transition-all duration-200 hover:scale-[1.02] active:scale-[0.97] focus:outline-none focus:ring-2 focus:ring-[var(--accent-blue)]"
            style={{
              background: 'var(--bg-elevated)',
              color: 'var(--text-primary)',
              border: '1px solid var(--border-subtle)',
            }}
            aria-label="Browse project templates"
          >
            Browse Templates
          </button>
        </div>

        {/* Feature Overview */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 max-w-lg sm:max-w-2xl mx-auto">
          <FeatureItem
            delay={200}
            icon={
              <svg width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden="true">
                <circle cx="9" cy="5" r="3" stroke="var(--accent-blue)" strokeWidth="1.3" opacity="0.8" />
                <circle cx="4" cy="14" r="2.5" stroke="var(--accent-green)" strokeWidth="1.3" opacity="0.6" />
                <circle cx="14" cy="14" r="2.5" stroke="var(--accent-purple)" strokeWidth="1.3" opacity="0.6" />
                <line x1="7" y1="7.5" x2="5" y2="12" stroke="var(--accent-blue)" strokeWidth="1" opacity="0.3" />
                <line x1="11" y1="7.5" x2="13" y2="12" stroke="var(--accent-blue)" strokeWidth="1" opacity="0.3" />
              </svg>
            }
            label="DAG Orchestration"
            desc="Agents work in parallel, respecting dependencies"
          />
          <FeatureItem
            delay={350}
            icon={
              <svg width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden="true">
                <path d="M3 14l4-4 3 3 5-6" stroke="var(--accent-green)" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" opacity="0.8" />
                <rect x="2" y="2" width="14" height="14" rx="2" stroke="var(--accent-green)" strokeWidth="1.2" opacity="0.3" />
              </svg>
            }
            label="Auto Review & QA"
            desc="Code reviewed and tested before commit"
          />
          <FeatureItem
            delay={500}
            icon={
              <svg width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden="true">
                <rect x="3" y="3" width="12" height="12" rx="2" stroke="var(--accent-amber)" strokeWidth="1.2" opacity="0.4" />
                <path d="M7 7h4M7 9h4M7 11h2" stroke="var(--accent-amber)" strokeWidth="1.2" strokeLinecap="round" opacity="0.7" />
              </svg>
            }
            label="Real-time Dashboard"
            desc="Watch every agent's progress live"
          />
        </div>

        {/* Status indicators */}
        <div
          className="mt-6 flex items-center justify-center gap-4 sm:gap-6 text-[10px] sm:text-xs font-mono flex-wrap"
          style={{ color: 'var(--text-muted)' }}
        >
          <span className="flex items-center gap-1.5">
            <span
              className="w-2 h-2 rounded-full"
              style={{ background: 'var(--accent-green)' }}
            />
            Agents Online
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

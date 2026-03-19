/**
 * NewProjectDialog.test.tsx — Tests for agent swarm preview count per team type.
 *
 * Verifies:
 * - Solo (agentsCount=1) renders exactly 1 agent preview
 * - Team (agentsCount=2) renders exactly 5 agent previews
 * - Full Team (agentsCount=3) renders all 12 agent previews
 * - Each preview has the correct stagger animation delay
 * - Clicking team type buttons changes the preview count
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import NewProjectDialog from '../components/NewProjectDialog';

// Mock api module
vi.mock('../api', () => ({
  createProject: vi.fn(),
  browseDirs: vi.fn().mockResolvedValue({
    current: '/home/user',
    parent: '/home',
    entries: [],
    home: '/home/user',
  }),
  getSettings: vi.fn().mockResolvedValue({
    projects_base_dir: '/home/user/projects',
  }),
}));

// ── Helpers ──────────────────────────────────────────────────────────

function renderNewProject() {
  return render(
    <MemoryRouter>
      <NewProjectDialog />
    </MemoryRouter>,
  );
}

// ── Tests ──────────────────────────────────────────────────────────

describe('NewProjectDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('swarm preview agent count', () => {
    it('test_swarm_preview_when_team_selected_should_render_5_agents', () => {
      // Default is agentsCount=2 (Team)
      const { container } = renderNewProject();

      // The swarm preview agents are rendered as divs with the `stagger-item` class
      const swarmAgents = container.querySelectorAll('.stagger-item');
      expect(swarmAgents).toHaveLength(5);
    });

    it('test_swarm_preview_when_solo_selected_should_render_1_agent', () => {
      const { container } = renderNewProject();

      // Click "Solo" button
      const soloBtn = screen.getByText('Solo');
      fireEvent.click(soloBtn);

      const swarmAgents = container.querySelectorAll('.stagger-item');
      expect(swarmAgents).toHaveLength(1);
    });

    it('test_swarm_preview_when_full_team_selected_should_render_12_agents', () => {
      const { container } = renderNewProject();

      // Click "Full Team" button
      const fullTeamBtn = screen.getByText('Full Team');
      fireEvent.click(fullTeamBtn);

      const swarmAgents = container.querySelectorAll('.stagger-item');
      expect(swarmAgents).toHaveLength(12);
    });

    it('test_swarm_preview_when_switching_team_types_should_update_agent_count', () => {
      const { container } = renderNewProject();

      // Start with Team (5 agents)
      expect(container.querySelectorAll('.stagger-item')).toHaveLength(5);

      // Switch to Solo
      fireEvent.click(screen.getByText('Solo'));
      expect(container.querySelectorAll('.stagger-item')).toHaveLength(1);

      // Switch to Full Team
      fireEvent.click(screen.getByText('Full Team'));
      expect(container.querySelectorAll('.stagger-item')).toHaveLength(12);

      // Switch back to Team
      fireEvent.click(screen.getByText('Team'));
      expect(container.querySelectorAll('.stagger-item')).toHaveLength(5);
    });

    it('test_swarm_preview_agents_should_have_staggered_animation_delay', () => {
      const { container } = renderNewProject();

      // Click Full Team to get all 12 agents
      fireEvent.click(screen.getByText('Full Team'));

      const swarmAgents = container.querySelectorAll('.stagger-item');
      expect(swarmAgents).toHaveLength(12);

      // Check that animation delays are staggered at 60ms intervals
      swarmAgents.forEach((agent, i) => {
        const style = (agent as HTMLElement).style;
        expect(style.animationDelay).toBe(`${i * 60}ms`);
      });
    });

    it('test_swarm_preview_solo_agent_should_be_centered', () => {
      const { container } = renderNewProject();

      // Click Solo
      fireEvent.click(screen.getByText('Solo'));

      const swarmAgents = container.querySelectorAll('.stagger-item');
      expect(swarmAgents).toHaveLength(1);

      // Solo agent should be centered (radius = 0, so cx = cy = 48)
      const agent = swarmAgents[0] as HTMLElement;
      // Size is 28 for count <= 8, so left = 48 - 14 = 34px, top = 48 - 14 = 34px
      expect(agent.style.left).toBe('34px');
      expect(agent.style.top).toBe('34px');
    });

    it('test_swarm_preview_agents_should_display_correct_labels', () => {
      const { container } = renderNewProject();

      // Default Team shows first 5 agents: PM, FE, BE, DB, QA
      const swarmAgents = container.querySelectorAll('.stagger-item');
      const labels = Array.from(swarmAgents).map(el => el.textContent);
      expect(labels).toEqual(['PM', 'FE', 'BE', 'DB', 'QA']);
    });
  });
});

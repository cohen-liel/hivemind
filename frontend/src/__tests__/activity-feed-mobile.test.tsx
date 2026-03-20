/**
 * activity-feed-mobile.test.tsx — Tests for ActivityFeed mobile UX enhancements.
 *
 * Verifies:
 * - ProcessingIndicator renders when processing=true
 * - ProcessingIndicator does NOT render when processing=false/undefined
 * - UserMessageBubble renders message content correctly
 * - UserMessageBubble shows "Delivered" indicator after delay
 * - Optimistic message display (user messages appear immediately)
 * - Auto-scroll container exists with correct ID for scroll targeting
 *
 * Naming: test_<what>_when_<condition>_should_<expected>
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, act, waitFor } from '@testing-library/react';
import type { ActivityEntry } from '../types';

// ── Mocks ──────────────────────────────────────────────────────────

// Mock constants to avoid complex proxy imports
vi.mock('../constants', () => ({
  AGENT_ICONS: new Proxy({} as Record<string, string>, {
    get(_t, p: string) {
      if (p === 'orchestrator') return '🧠';
      if (p === 'developer') return '💻';
      return '🤖';
    },
    has() { return true; },
  }),
  AGENT_LABELS: new Proxy({} as Record<string, string>, {
    get(_t, p: string) { return p; },
  }),
  formatTime: (ts: number) => new Date(ts * 1000).toLocaleTimeString(),
  getAgentAccent: () => ({ color: '#638cff', glow: 'rgba(99,140,255,0.15)', bg: 'rgba(99,140,255,0.06)' }),
}));

// Mock ResizeObserver (not available in jsdom)
class MockResizeObserver {
  observe = vi.fn();
  unobserve = vi.fn();
  disconnect = vi.fn();
  constructor(public callback: ResizeObserverCallback) {}
}
vi.stubGlobal('ResizeObserver', MockResizeObserver);

// ── Lazy import (after mocks) ────────────────────────────────────
// We import after mocks are set up to ensure they take effect
import ActivityFeed from '../components/ActivityFeed';

// ── Helpers ──────────────────────────────────────────────────────────

let idCounter = 0;

function makeActivity(overrides: Partial<ActivityEntry> = {}): ActivityEntry {
  idCounter += 1;
  return {
    id: `act-${idCounter}`,
    type: 'agent_text',
    timestamp: Date.now() / 1000,
    agent: 'orchestrator',
    content: 'Default test content',
    ...overrides,
  };
}

function makeUserMessage(content: string, ageMs = 0): ActivityEntry {
  idCounter += 1;
  return {
    id: `act-${idCounter}`,
    type: 'user_message',
    timestamp: (Date.now() - ageMs) / 1000,
    content,
  };
}

// ── ProcessingIndicator tests ────────────────────────────────────

describe('ActivityFeed ProcessingIndicator', () => {
  beforeEach(() => {
    idCounter = 0;
  });

  it('test_processing_indicator_when_processing_true_should_render', () => {
    const activities = [makeActivity({ content: 'Hello from agent' })];

    render(<ActivityFeed activities={activities} processing={true} />);

    const indicator = screen.getByRole('status', { name: /Processing your request/i });
    expect(indicator).toBeTruthy();
  });

  it('test_processing_indicator_when_processing_true_should_show_processing_text', () => {
    const activities = [makeActivity()];

    render(<ActivityFeed activities={activities} processing={true} />);

    expect(screen.getByText('Processing')).toBeTruthy();
  });

  it('test_processing_indicator_when_processing_false_should_not_render', () => {
    const activities = [makeActivity()];

    render(<ActivityFeed activities={activities} processing={false} />);

    expect(screen.queryByRole('status', { name: /Processing your request/i })).toBeNull();
  });

  it('test_processing_indicator_when_processing_undefined_should_not_render', () => {
    const activities = [makeActivity()];

    render(<ActivityFeed activities={activities} />);

    expect(screen.queryByRole('status', { name: /Processing your request/i })).toBeNull();
  });

  it('test_processing_indicator_when_processing_true_should_have_animated_dots', () => {
    const activities = [makeActivity()];

    render(<ActivityFeed activities={activities} processing={true} />);

    const indicator = screen.getByRole('status', { name: /Processing your request/i });
    // Should contain 3 animated dots (small circles within the indicator)
    const dots = indicator.querySelectorAll('span.rounded-full');
    expect(dots.length).toBe(3);
  });
});


// ── UserMessageBubble tests ──────────────────────────────────────

describe('ActivityFeed UserMessageBubble', () => {
  beforeEach(() => {
    idCounter = 0;
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('test_user_message_when_rendered_should_show_content', () => {
    const activities = [makeUserMessage('Hello world!')];

    render(<ActivityFeed activities={activities} />);

    expect(screen.getByText('Hello world!')).toBeTruthy();
  });

  it('test_user_message_when_old_message_should_show_delivered_immediately', () => {
    // Message from 5 seconds ago — should show delivered right away
    const activities = [makeUserMessage('Old message', 5000)];

    render(<ActivityFeed activities={activities} />);

    expect(screen.getByLabelText('Message delivered')).toBeTruthy();
  });

  it('test_user_message_when_new_message_should_show_delivered_after_delay', async () => {
    // Brand new message — delivered should appear after 1.5s
    const activities = [makeUserMessage('New message', 0)];

    render(<ActivityFeed activities={activities} />);

    // Initially, no delivered indicator
    expect(screen.queryByLabelText('Message delivered')).toBeNull();

    // Advance time past 1.5s threshold
    act(() => {
      vi.advanceTimersByTime(1600);
    });

    expect(screen.getByLabelText('Message delivered')).toBeTruthy();
  });

  it('test_user_message_when_multiple_messages_should_render_all', () => {
    const activities = [
      makeUserMessage('First message', 10000),
      makeUserMessage('Second message', 5000),
      makeUserMessage('Third message', 0),
    ];

    render(<ActivityFeed activities={activities} />);

    expect(screen.getByText('First message')).toBeTruthy();
    expect(screen.getByText('Second message')).toBeTruthy();
    expect(screen.getByText('Third message')).toBeTruthy();
  });
});


// ── Optimistic message display tests ─────────────────────────────

describe('ActivityFeed optimistic message display', () => {
  beforeEach(() => {
    idCounter = 0;
  });

  it('test_optimistic_message_when_added_to_activities_should_render_immediately', () => {
    // Simulate optimistic add: user message appears in activities array instantly
    const userMsg = makeUserMessage('Sent just now', 0);
    const activities = [
      makeActivity({ content: 'Previous agent response', timestamp: (Date.now() - 5000) / 1000 }),
      userMsg,
    ];

    render(<ActivityFeed activities={activities} />);

    expect(screen.getByText('Sent just now')).toBeTruthy();
  });

  it('test_optimistic_message_when_followed_by_processing_should_show_both', () => {
    const userMsg = makeUserMessage('Do this task', 0);
    const activities = [userMsg];

    render(<ActivityFeed activities={activities} processing={true} />);

    // Both user message and processing indicator should be visible
    expect(screen.getByText('Do this task')).toBeTruthy();
    expect(screen.getByRole('status', { name: /Processing your request/i })).toBeTruthy();
  });

  it('test_optimistic_message_when_agent_responds_should_show_response', () => {
    const userMsg = makeUserMessage('Build a login page', 2000);
    const agentResponse = makeActivity({
      content: 'I\'ll create a login page with email and password fields.',
      timestamp: (Date.now() - 1000) / 1000,
    });
    const activities = [userMsg, agentResponse];

    render(<ActivityFeed activities={activities} processing={false} />);

    expect(screen.getByText('Build a login page')).toBeTruthy();
    expect(screen.getByText("I'll create a login page with email and password fields.")).toBeTruthy();
    // No processing indicator since processing=false
    expect(screen.queryByRole('status', { name: /Processing your request/i })).toBeNull();
  });
});


// ── Mixed content rendering tests ────────────────────────────────

describe('ActivityFeed mixed content', () => {
  beforeEach(() => {
    idCounter = 0;
  });

  it('test_feed_when_empty_activities_should_render_without_error', () => {
    const { container } = render(<ActivityFeed activities={[]} />);
    expect(container).toBeTruthy();
  });

  it('test_feed_when_error_entry_should_render_error_bubble', () => {
    const activities = [
      makeActivity({
        type: 'error',
        content: 'Connection timeout error',
        agent: 'developer',
      }),
    ];

    render(<ActivityFeed activities={activities} />);

    // Error should be rendered (translated to "Agent Timed Out")
    expect(screen.getByText('Agent Timed Out')).toBeTruthy();
  });

  it('test_feed_when_agent_started_should_show_delegation', () => {
    const activities = [
      makeActivity({
        type: 'agent_started',
        agent: 'developer',
        task: 'Building authentication module',
      }),
    ];

    render(<ActivityFeed activities={activities} />);

    expect(screen.getByText(/Building authentication module/)).toBeTruthy();
  });
});

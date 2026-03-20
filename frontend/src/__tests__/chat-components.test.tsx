/**
 * Tests for chat components: ChannelList, MessageComposer, TypingIndicator.
 *
 * Uses @testing-library/react for component rendering and user interactions.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ChannelList from '../components/chat/ChannelList';
import MessageComposer from '../components/chat/MessageComposer';
import TypingIndicator from '../components/chat/TypingIndicator';
import type { ChatChannel, TypingUser } from '../types';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeChannel(overrides: Partial<ChatChannel> = {}): ChatChannel {
  return {
    id: 'chan-1',
    name: 'general',
    channel_type: 'circle',
    circle_id: 'c1',
    project_id: null,
    description: 'General chat',
    is_archived: false,
    created_by: 'user-1',
    created_at: '2026-03-20T00:00:00Z',
    unread_count: 0,
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// ChannelList
// ---------------------------------------------------------------------------

describe('ChannelList', () => {
  const mockOnSelect = vi.fn();
  const mockOnCreate = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('test_channel_list_when_channels_exist_should_render_all', () => {
    const channels = [
      makeChannel({ id: 'c1', name: 'general' }),
      makeChannel({ id: 'c2', name: 'dev-chat' }),
    ];
    render(
      <ChannelList
        channels={channels}
        activeChannelId={null}
        onSelectChannel={mockOnSelect}
        onCreateChannel={mockOnCreate}
      />
    );
    expect(screen.getByText('general')).toBeTruthy();
    expect(screen.getByText('dev-chat')).toBeTruthy();
  });

  it('test_channel_list_when_empty_should_show_empty_state', () => {
    render(
      <ChannelList
        channels={[]}
        activeChannelId={null}
        onSelectChannel={mockOnSelect}
        onCreateChannel={mockOnCreate}
      />
    );
    expect(screen.getByText('No channels yet')).toBeTruthy();
    expect(screen.getByText('Create one')).toBeTruthy();
  });

  it('test_channel_list_when_active_channel_should_mark_current', () => {
    const channels = [
      makeChannel({ id: 'c1', name: 'general' }),
      makeChannel({ id: 'c2', name: 'dev-chat' }),
    ];
    render(
      <ChannelList
        channels={channels}
        activeChannelId="c1"
        onSelectChannel={mockOnSelect}
        onCreateChannel={mockOnCreate}
      />
    );
    const activeBtn = screen.getByRole('button', { name: /general/i });
    expect(activeBtn.getAttribute('aria-current')).toBe('page');
  });

  it('test_channel_list_when_clicked_should_call_onSelect', async () => {
    const channels = [makeChannel({ id: 'c1', name: 'general' })];
    render(
      <ChannelList
        channels={channels}
        activeChannelId={null}
        onSelectChannel={mockOnSelect}
        onCreateChannel={mockOnCreate}
      />
    );
    await userEvent.click(screen.getByText('general'));
    expect(mockOnSelect).toHaveBeenCalledWith('c1');
  });

  it('test_channel_list_when_unread_should_show_badge', () => {
    const channels = [makeChannel({ id: 'c1', name: 'general', unread_count: 5 })];
    render(
      <ChannelList
        channels={channels}
        activeChannelId={null}
        onSelectChannel={mockOnSelect}
        onCreateChannel={mockOnCreate}
      />
    );
    expect(screen.getByText('5')).toBeTruthy();
  });

  it('test_channel_list_when_unread_over_99_should_show_99_plus', () => {
    const channels = [makeChannel({ id: 'c1', name: 'general', unread_count: 150 })];
    render(
      <ChannelList
        channels={channels}
        activeChannelId={null}
        onSelectChannel={mockOnSelect}
        onCreateChannel={mockOnCreate}
      />
    );
    expect(screen.getByText('99+')).toBeTruthy();
  });

  it('test_channel_list_when_create_button_clicked_should_call_onCreate', async () => {
    render(
      <ChannelList
        channels={[]}
        activeChannelId={null}
        onSelectChannel={mockOnSelect}
        onCreateChannel={mockOnCreate}
      />
    );
    await userEvent.click(screen.getByLabelText('Create new channel'));
    expect(mockOnCreate).toHaveBeenCalledOnce();
  });

  it('test_channel_list_when_no_unread_should_not_show_badge', () => {
    const channels = [makeChannel({ id: 'c1', name: 'general', unread_count: 0 })];
    render(
      <ChannelList
        channels={channels}
        activeChannelId={null}
        onSelectChannel={mockOnSelect}
        onCreateChannel={mockOnCreate}
      />
    );
    // Should NOT show any badge number
    expect(screen.queryByText('0')).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// MessageComposer
// ---------------------------------------------------------------------------

describe('MessageComposer', () => {
  const mockOnSend = vi.fn<(content: string) => Promise<boolean>>();
  const mockOnTyping = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
    mockOnSend.mockResolvedValue(true);
  });

  it('test_composer_when_rendered_should_show_input_and_button', () => {
    render(<MessageComposer onSend={mockOnSend} onTyping={mockOnTyping} />);
    expect(screen.getByLabelText('Message input')).toBeTruthy();
    expect(screen.getByLabelText('Send message')).toBeTruthy();
  });

  it('test_composer_when_empty_should_disable_send', () => {
    render(<MessageComposer onSend={mockOnSend} onTyping={mockOnTyping} />);
    const sendBtn = screen.getByLabelText('Send message');
    expect(sendBtn).toHaveProperty('disabled', true);
  });

  it('test_composer_when_typing_should_notify_typing', async () => {
    render(<MessageComposer onSend={mockOnSend} onTyping={mockOnTyping} />);
    const input = screen.getByLabelText('Message input');
    await userEvent.type(input, 'Hello');
    expect(mockOnTyping).toHaveBeenCalled();
    // First char: typing(true)
    expect(mockOnTyping).toHaveBeenCalledWith(true);
  });

  it('test_composer_when_send_clicked_should_call_onSend', async () => {
    render(<MessageComposer onSend={mockOnSend} onTyping={mockOnTyping} />);
    const input = screen.getByLabelText('Message input');
    await userEvent.type(input, 'Hello World');
    const sendBtn = screen.getByLabelText('Send message');
    await userEvent.click(sendBtn);
    expect(mockOnSend).toHaveBeenCalledWith('Hello World');
  });

  it('test_composer_when_enter_pressed_should_send', async () => {
    render(<MessageComposer onSend={mockOnSend} onTyping={mockOnTyping} />);
    const input = screen.getByLabelText('Message input');
    await userEvent.type(input, 'Quick message');
    await userEvent.keyboard('{Enter}');
    expect(mockOnSend).toHaveBeenCalledWith('Quick message');
  });

  it('test_composer_when_shift_enter_should_not_send', async () => {
    render(<MessageComposer onSend={mockOnSend} onTyping={mockOnTyping} />);
    const input = screen.getByLabelText('Message input');
    await userEvent.type(input, 'Line 1');
    await userEvent.keyboard('{Shift>}{Enter}{/Shift}');
    expect(mockOnSend).not.toHaveBeenCalled();
  });

  it('test_composer_when_disabled_should_not_allow_send', async () => {
    render(<MessageComposer onSend={mockOnSend} onTyping={mockOnTyping} disabled={true} />);
    const input = screen.getByLabelText('Message input');
    expect(input).toHaveProperty('disabled', true);
  });

  it('test_composer_when_custom_placeholder_should_show_it', () => {
    render(
      <MessageComposer onSend={mockOnSend} onTyping={mockOnTyping} placeholder="Say something..." />
    );
    const input = screen.getByPlaceholderText('Say something...');
    expect(input).toBeTruthy();
  });

  it('test_composer_when_send_succeeds_should_clear_input', async () => {
    render(<MessageComposer onSend={mockOnSend} onTyping={mockOnTyping} />);
    const input = screen.getByLabelText('Message input') as HTMLTextAreaElement;
    await userEvent.type(input, 'Hello');
    await userEvent.keyboard('{Enter}');
    await waitFor(() => {
      expect(input.value).toBe('');
    });
  });

  it('test_composer_when_send_fails_should_keep_input', async () => {
    mockOnSend.mockResolvedValue(false);
    render(<MessageComposer onSend={mockOnSend} onTyping={mockOnTyping} />);
    const input = screen.getByLabelText('Message input') as HTMLTextAreaElement;
    await userEvent.type(input, 'Hello');
    await userEvent.keyboard('{Enter}');
    await waitFor(() => {
      expect(input.value).toBe('Hello');
    });
  });
});

// ---------------------------------------------------------------------------
// TypingIndicator
// ---------------------------------------------------------------------------

describe('TypingIndicator', () => {
  it('test_typing_when_no_users_should_return_null', () => {
    const { container } = render(<TypingIndicator users={[]} />);
    expect(container.innerHTML).toBe('');
  });

  it('test_typing_when_one_user_should_show_singular', () => {
    const users: TypingUser[] = [{ user_id: 'alice', started_at: Date.now() }];
    render(<TypingIndicator users={users} />);
    expect(screen.getByText('alice is typing')).toBeTruthy();
  });

  it('test_typing_when_two_users_should_show_both', () => {
    const users: TypingUser[] = [
      { user_id: 'alice', started_at: Date.now() },
      { user_id: 'bob', started_at: Date.now() },
    ];
    render(<TypingIndicator users={users} />);
    expect(screen.getByText('alice and bob are typing')).toBeTruthy();
  });

  it('test_typing_when_three_users_should_show_count', () => {
    const users: TypingUser[] = [
      { user_id: 'alice', started_at: Date.now() },
      { user_id: 'bob', started_at: Date.now() },
      { user_id: 'charlie', started_at: Date.now() },
    ];
    render(<TypingIndicator users={users} />);
    expect(screen.getByText('alice and 2 others are typing')).toBeTruthy();
  });

  it('test_typing_when_users_should_have_status_role', () => {
    const users: TypingUser[] = [{ user_id: 'alice', started_at: Date.now() }];
    render(<TypingIndicator users={users} />);
    expect(screen.getByRole('status')).toBeTruthy();
  });

  it('test_typing_when_users_should_have_aria_label', () => {
    const users: TypingUser[] = [{ user_id: 'alice', started_at: Date.now() }];
    render(<TypingIndicator users={users} />);
    expect(screen.getByLabelText('alice is typing')).toBeTruthy();
  });

  it('test_typing_should_show_animated_dots', () => {
    const users: TypingUser[] = [{ user_id: 'alice', started_at: Date.now() }];
    const { container } = render(<TypingIndicator users={users} />);
    // 3 dot spans
    const dots = container.querySelectorAll('[aria-hidden="true"] span');
    expect(dots.length).toBe(3);
  });
});

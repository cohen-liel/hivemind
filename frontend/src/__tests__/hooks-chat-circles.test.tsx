/**
 * Tests for hooks: useChat and useCircles.
 *
 * Since these hooks interact with the API and WebSocket, we mock the api module
 * and test the state management logic.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import type { ChatChannel, ChatMessage, Circle, CircleMember, CircleMemberRole } from '../types';

// ---------------------------------------------------------------------------
// Mock api module
// ---------------------------------------------------------------------------

const mockGetChatChannels = vi.fn<() => Promise<ChatChannel[]>>();
const mockGetChatMessages = vi.fn();
const mockSendChatMessage = vi.fn();
const mockCreateChatChannel = vi.fn();
const mockMarkMessageRead = vi.fn();
const mockGetCircles = vi.fn<() => Promise<Circle[]>>();
const mockGetCircle = vi.fn();
const mockGetCircleMembers = vi.fn<() => Promise<CircleMember[]>>();
const mockGetCircleProjects = vi.fn();
const mockCreateCircle = vi.fn();
const mockDeleteCircle = vi.fn();
const mockAddCircleMember = vi.fn();
const mockRemoveCircleMember = vi.fn();

vi.mock('../api', () => ({
  getChatChannels: (...args: unknown[]) => mockGetChatChannels(...(args as [])),
  getChatMessages: (...args: unknown[]) => mockGetChatMessages(...(args as any[])),
  sendChatMessage: (...args: unknown[]) => mockSendChatMessage(...(args as any[])),
  createChatChannel: (...args: unknown[]) => mockCreateChatChannel(...(args as any[])),
  markMessageRead: (...args: unknown[]) => mockMarkMessageRead(...(args as any[])),
  getCircles: () => mockGetCircles(),
  getCircle: (...args: unknown[]) => mockGetCircle(...(args as any[])),
  getCircleMembers: (...args: unknown[]) => mockGetCircleMembers(...(args as [])),
  getCircleProjects: (...args: unknown[]) => mockGetCircleProjects(...(args as any[])),
  createCircle: (...args: unknown[]) => mockCreateCircle(...(args as any[])),
  deleteCircle: (...args: unknown[]) => mockDeleteCircle(...(args as any[])),
  addCircleMember: (...args: unknown[]) => mockAddCircleMember(...(args as any[])),
  removeCircleMember: (...args: unknown[]) => mockRemoveCircleMember(...(args as any[])),
}));

// Mock WebSocket
const mockWsSend = vi.fn();
const mockWsClose = vi.fn();

class MockWebSocket {
  static OPEN = 1;
  readyState = MockWebSocket.OPEN;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;
  send = mockWsSend;
  close = mockWsClose;
  constructor(_url: string) {}
}

vi.stubGlobal('WebSocket', MockWebSocket);

import { useChat } from '../hooks/useChat';
import { useCircles, useCircleDetail } from '../hooks/useCircles';

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
    description: null,
    is_archived: false,
    created_by: 'u1',
    created_at: 1742428800,
    unread_count: 0,
    ...overrides,
  };
}

function makeCircle(overrides: Partial<Circle> = {}): Circle {
  return {
    id: 'circle-1',
    name: 'Test Circle',
    description: 'desc',
    avatar_url: null,
    member_count: 3,
    project_count: 1,
    created_by: 'u1',
    created_at: 1742428800,
    updated_at: 1742428800,
    settings: null,
    ...overrides,
  };
}

function makeMessage(overrides: Partial<ChatMessage> = {}): ChatMessage {
  return {
    id: 'msg-1',
    channel_id: 'chan-1',
    sender_id: 'u1',
    content: 'Hello',
    message_type: 'text',
    parent_message_id: null,
    metadata: null,
    created_at: 1742428800,
    updated_at: null,
    is_deleted: false,
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// useCircles
// ---------------------------------------------------------------------------

describe('useCircles', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetCircles.mockResolvedValue([]);
  });

  it('test_useCircles_when_mounted_should_load_circles', async () => {
    const circles = [makeCircle()];
    mockGetCircles.mockResolvedValue(circles);

    const { result } = renderHook(() => useCircles());
    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });
    expect(result.current.circles).toHaveLength(1);
    expect(result.current.circles[0].name).toBe('Test Circle');
  });

  it('test_useCircles_when_api_fails_should_set_error', async () => {
    mockGetCircles.mockRejectedValue(new Error('Network error'));

    const { result } = renderHook(() => useCircles());
    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });
    expect(result.current.error).toBe('Network error');
  });

  it('test_useCircles_when_create_should_call_api_and_refresh', async () => {
    mockGetCircles.mockResolvedValue([]);
    const newCircle = makeCircle({ id: 'new', name: 'New Circle' });
    mockCreateCircle.mockResolvedValue(newCircle);

    const { result } = renderHook(() => useCircles());
    await waitFor(() => expect(result.current.loading).toBe(false));

    mockGetCircles.mockResolvedValue([newCircle]);
    let created: Circle | null = null;
    await act(async () => {
      created = await result.current.create({ name: 'New Circle' });
    });
    expect(created).not.toBeNull();
    expect(mockCreateCircle).toHaveBeenCalledWith({ name: 'New Circle' });
  });

  it('test_useCircles_when_remove_should_remove_from_list', async () => {
    const circles = [makeCircle({ id: 'c1' })];
    mockGetCircles.mockResolvedValue(circles);
    mockDeleteCircle.mockResolvedValue(undefined);

    const { result } = renderHook(() => useCircles());
    await waitFor(() => expect(result.current.loading).toBe(false));

    let removed = false;
    await act(async () => {
      removed = await result.current.remove('c1');
    });
    expect(removed).toBe(true);
    expect(result.current.circles).toHaveLength(0);
  });

  it('test_useCircles_when_remove_fails_should_set_error', async () => {
    mockGetCircles.mockResolvedValue([makeCircle()]);
    mockDeleteCircle.mockRejectedValue(new Error('Forbidden'));

    const { result } = renderHook(() => useCircles());
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      await result.current.remove('circle-1');
    });
    expect(result.current.error).toBe('Forbidden');
  });
});

// ---------------------------------------------------------------------------
// useCircleDetail
// ---------------------------------------------------------------------------

describe('useCircleDetail', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetCircle.mockResolvedValue(makeCircle());
    mockGetCircleMembers.mockResolvedValue([]);
    mockGetCircleProjects.mockResolvedValue([]);
  });

  it('test_useCircleDetail_when_circleId_provided_should_load', async () => {
    const { result } = renderHook(() => useCircleDetail('circle-1'));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.circle?.name).toBe('Test Circle');
    expect(mockGetCircle).toHaveBeenCalledWith('circle-1');
  });

  it('test_useCircleDetail_when_null_id_should_not_load', async () => {
    const { result } = renderHook(() => useCircleDetail(null));
    // Should stay in loading=true but not call API
    expect(mockGetCircle).not.toHaveBeenCalled();
  });

  it('test_useCircleDetail_when_addMember_should_call_api', async () => {
    mockAddCircleMember.mockResolvedValue(undefined);

    const { result } = renderHook(() => useCircleDetail('circle-1'));
    await waitFor(() => expect(result.current.loading).toBe(false));

    let added = false;
    await act(async () => {
      added = await result.current.addMember('user-2', 'member');
    });
    expect(added).toBe(true);
    expect(mockAddCircleMember).toHaveBeenCalledWith('circle-1', 'user-2', 'member');
  });

  it('test_useCircleDetail_when_removeMember_should_update_list', async () => {
    const members: CircleMember[] = [
      { user_id: 'user-1', role: 'owner' as CircleMemberRole },
      { user_id: 'user-2', role: 'member' as CircleMemberRole },
    ];
    mockGetCircleMembers.mockResolvedValue(members);
    mockRemoveCircleMember.mockResolvedValue(undefined);

    const { result } = renderHook(() => useCircleDetail('circle-1'));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.members).toHaveLength(2);

    await act(async () => {
      await result.current.removeMember('user-2');
    });
    expect(result.current.members).toHaveLength(1);
  });

  it('test_useCircleDetail_when_api_fails_should_set_error', async () => {
    mockGetCircle.mockRejectedValue(new Error('Not found'));

    const { result } = renderHook(() => useCircleDetail('circle-1'));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBe('Not found');
  });
});

// ---------------------------------------------------------------------------
// useChat
// ---------------------------------------------------------------------------

describe('useChat', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetChatChannels.mockResolvedValue([]);
    mockGetChatMessages.mockResolvedValue({ messages: [], has_more: false });
    mockMarkMessageRead.mockResolvedValue(undefined);
  });

  it('test_useChat_when_mounted_should_load_channels', async () => {
    const channels = [makeChannel()];
    mockGetChatChannels.mockResolvedValue(channels);

    const { result } = renderHook(() => useChat());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.channels).toHaveLength(1);
  });

  it('test_useChat_when_channels_loaded_should_auto_select_first', async () => {
    const channels = [makeChannel({ id: 'ch1' }), makeChannel({ id: 'ch2' })];
    mockGetChatChannels.mockResolvedValue(channels);
    mockGetChatMessages.mockResolvedValue({ messages: [], has_more: false });

    const { result } = renderHook(() => useChat());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.activeChannel?.id).toBe('ch1');
  });

  it('test_useChat_when_selectChannel_should_change_active', async () => {
    const channels = [makeChannel({ id: 'ch1' }), makeChannel({ id: 'ch2' })];
    mockGetChatChannels.mockResolvedValue(channels);
    mockGetChatMessages.mockResolvedValue({ messages: [], has_more: false });

    const { result } = renderHook(() => useChat());
    await waitFor(() => expect(result.current.loading).toBe(false));

    act(() => {
      result.current.selectChannel('ch2');
    });
    expect(result.current.activeChannel?.id).toBe('ch2');
  });

  it('test_useChat_when_api_fails_should_set_error', async () => {
    mockGetChatChannels.mockRejectedValue(new Error('Network error'));

    const { result } = renderHook(() => useChat());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBe('Network error');
  });

  it('test_useChat_when_empty_should_have_zero_unread', async () => {
    mockGetChatChannels.mockResolvedValue([]);

    const { result } = renderHook(() => useChat());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.totalUnread).toBe(0);
  });

  it('test_useChat_when_channels_have_unread_should_sum', async () => {
    const channels = [
      makeChannel({ id: 'ch1', unread_count: 3 }),
      makeChannel({ id: 'ch2', unread_count: 7 }),
    ];
    mockGetChatChannels.mockResolvedValue(channels);

    const { result } = renderHook(() => useChat());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.totalUnread).toBe(10);
  });

  it('test_useChat_when_createChannel_should_call_api', async () => {
    mockGetChatChannels.mockResolvedValue([]);
    const newChannel = makeChannel({ id: 'new-ch', name: 'new-channel' });
    mockCreateChatChannel.mockResolvedValue(newChannel);

    const { result } = renderHook(() => useChat('c1'));
    await waitFor(() => expect(result.current.loading).toBe(false));

    mockGetChatChannels.mockResolvedValue([newChannel]);
    let created: ChatChannel | null = null;
    await act(async () => {
      created = await result.current.createChannel('new-channel');
    });
    expect(created).not.toBeNull();
    expect(mockCreateChatChannel).toHaveBeenCalled();
  });

  it('test_useChat_when_sendMessage_empty_should_return_false', async () => {
    const channels = [makeChannel()];
    mockGetChatChannels.mockResolvedValue(channels);
    mockGetChatMessages.mockResolvedValue({ messages: [], has_more: false });

    const { result } = renderHook(() => useChat());
    await waitFor(() => expect(result.current.activeChannel).not.toBeNull());

    let sent = true;
    await act(async () => {
      sent = await result.current.sendMessage('   ');
    });
    expect(sent).toBe(false);
  });
});

import { useState, useEffect, useCallback, useRef } from 'react';
import {
  getChatChannels,
  getChatMessages,
  sendChatMessage,
  createChatChannel,
  markMessageRead,
} from '../api';
import type { ChatChannel, ChatMessage, TypingUser } from '../types';

interface UseChatReturn {
  channels: ChatChannel[];
  activeChannel: ChatChannel | null;
  messages: ChatMessage[];
  typingUsers: TypingUser[];
  loading: boolean;
  sendingMessage: boolean;
  hasMore: boolean;
  error: string | null;
  totalUnread: number;
  selectChannel: (channelId: string) => void;
  sendMessage: (content: string, parentId?: string) => Promise<boolean>;
  loadMore: () => Promise<void>;
  refreshChannels: () => Promise<void>;
  createChannel: (name: string, circleId?: string) => Promise<ChatChannel | null>;
  setTyping: (isTyping: boolean) => void;
}

export function useChat(circleId?: string): UseChatReturn {
  const [channels, setChannels] = useState<ChatChannel[]>([]);
  const [activeChannelId, setActiveChannelId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [typingUsers, setTypingUsers] = useState<TypingUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [sendingMessage, setSendingMessage] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const typingTimeoutRef = useRef<ReturnType<typeof setTimeout>>();

  const activeChannel = channels.find(c => c.id === activeChannelId) ?? null;
  const totalUnread = channels.reduce((sum, c) => sum + (c.unread_count || 0), 0);

  const refreshChannels = useCallback(async () => {
    try {
      setError(null);
      const data = await getChatChannels(circleId);
      setChannels(data);
      if (data.length > 0 && !activeChannelId) {
        setActiveChannelId(data[0].id);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load channels');
    } finally {
      setLoading(false);
    }
  }, [circleId, activeChannelId]);

  useEffect(() => {
    refreshChannels();
  }, [refreshChannels]);

  // Load messages when channel changes
  useEffect(() => {
    if (!activeChannelId) return;
    let cancelled = false;

    const loadMessages = async () => {
      try {
        const data = await getChatMessages(activeChannelId, { limit: 50 });
        if (!cancelled) {
          setMessages(data.messages.reverse());
          setHasMore(data.has_more);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load messages');
        }
      }
    };

    loadMessages();
    return () => { cancelled = true; };
  }, [activeChannelId]);

  // WebSocket connection for real-time
  useEffect(() => {
    if (!activeChannelId) return;

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const ws = new WebSocket(`${protocol}//${window.location.host}/api/chat/ws/${activeChannelId}`);
    wsRef.current = ws;

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.type === 'CHAT_MESSAGE' || data.type === 'chat_message') {
          const msg = data.message || data;
          setMessages(prev => [...prev, msg]);
          // Mark as read
          if (msg.id) {
            markMessageRead(msg.id).catch(() => {});
          }
        } else if (data.type === 'CHAT_TYPING' || data.type === 'chat_typing') {
          const userId = data.user_id;
          const isTyping = data.is_typing;
          setTypingUsers(prev => {
            if (isTyping) {
              const exists = prev.some(u => u.user_id === userId);
              if (exists) return prev;
              return [...prev, { user_id: userId, started_at: Date.now() }];
            }
            return prev.filter(u => u.user_id !== userId);
          });
        }
      } catch {
        // Ignore parse errors
      }
    };

    ws.onerror = () => {
      // Silent — will reconnect via close handler or polling fallback
    };

    return () => {
      ws.close();
      wsRef.current = null;
    };
  }, [activeChannelId]);

  // Clear stale typing indicators
  useEffect(() => {
    const interval = setInterval(() => {
      const cutoff = Date.now() - 5000;
      setTypingUsers(prev => prev.filter(u => u.started_at > cutoff));
    }, 2000);
    return () => clearInterval(interval);
  }, []);

  const selectChannel = useCallback((channelId: string) => {
    setActiveChannelId(channelId);
    setMessages([]);
    setTypingUsers([]);
  }, []);

  const sendMessage = useCallback(async (content: string, parentId?: string): Promise<boolean> => {
    if (!activeChannelId || !content.trim()) return false;
    setSendingMessage(true);
    try {
      // Try WebSocket first
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify({
          type: 'message',
          content: content.trim(),
          message_type: 'text',
          parent_message_id: parentId,
        }));
        setSendingMessage(false);
        return true;
      }
      // Fallback to REST
      const msg = await sendChatMessage(activeChannelId, content.trim(), {
        parent_message_id: parentId,
      });
      setMessages(prev => [...prev, msg]);
      setSendingMessage(false);
      return true;
    } catch {
      setSendingMessage(false);
      return false;
    }
  }, [activeChannelId]);

  const loadMore = useCallback(async () => {
    if (!activeChannelId || !hasMore || messages.length === 0) return;
    const oldest = messages[0];
    try {
      const data = await getChatMessages(activeChannelId, { before: oldest.id, limit: 50 });
      setMessages(prev => [...data.messages.reverse(), ...prev]);
      setHasMore(data.has_more);
    } catch {
      // Silent
    }
  }, [activeChannelId, hasMore, messages]);

  const createChannel = useCallback(async (name: string, cId?: string): Promise<ChatChannel | null> => {
    try {
      const channel = await createChatChannel({
        name,
        circle_id: cId || circleId,
        channel_type: 'circle',
      });
      await refreshChannels();
      setActiveChannelId(channel.id);
      return channel;
    } catch {
      return null;
    }
  }, [circleId, refreshChannels]);

  const setTyping = useCallback((isTyping: boolean) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: 'typing', is_typing: isTyping }));
    }
    if (typingTimeoutRef.current) clearTimeout(typingTimeoutRef.current);
    if (isTyping) {
      typingTimeoutRef.current = setTimeout(() => {
        if (wsRef.current?.readyState === WebSocket.OPEN) {
          wsRef.current.send(JSON.stringify({ type: 'typing', is_typing: false }));
        }
      }, 3000);
    }
  }, []);

  return {
    channels,
    activeChannel,
    messages,
    typingUsers,
    loading,
    sendingMessage,
    hasMore,
    error,
    totalUnread,
    selectChannel,
    sendMessage,
    loadMore,
    refreshChannels,
    createChannel,
    setTyping,
  };
}

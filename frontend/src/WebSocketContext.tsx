import { createContext, useContext, useEffect, useRef, useState, useCallback, type ReactNode } from 'react';
import type { WSEvent } from './types';

type Subscriber = (event: WSEvent) => void;

interface WSContextValue {
  connected: boolean;
  subscribe: (callback: Subscriber) => () => void;
}

const WSContext = createContext<WSContextValue>({
  connected: false,
  subscribe: () => () => {},
});

export function WebSocketProvider({ children }: { children: ReactNode }) {
  const wsRef = useRef<WebSocket | null>(null);
  const [connected, setConnected] = useState(false);
  const subscribersRef = useRef<Set<Subscriber>>(new Set());
  const retryDelayRef = useRef(1000);
  const mountedRef = useRef(true);

  const connect = useCallback(() => {
    if (!mountedRef.current) return;

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host;
    const ws = new WebSocket(`${protocol}//${host}/ws`);

    ws.onopen = () => {
      setConnected(true);
      retryDelayRef.current = 1000; // reset backoff on success
    };

    ws.onmessage = (e) => {
      try {
        const event = JSON.parse(e.data) as WSEvent;
        for (const cb of subscribersRef.current) {
          try {
            cb(event);
          } catch {
            // subscriber error — don't break others
          }
        }
      } catch {
        // ignore malformed messages
      }
    };

    ws.onclose = () => {
      setConnected(false);
      if (!mountedRef.current) return;
      // Exponential backoff: 1s → 2s → 4s → 8s → 16s → 30s cap
      const delay = retryDelayRef.current;
      retryDelayRef.current = Math.min(delay * 2, 30000);
      setTimeout(connect, delay);
    };

    ws.onerror = () => {
      ws.close();
    };

    wsRef.current = ws;
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    connect();
    return () => {
      mountedRef.current = false;
      wsRef.current?.close();
    };
  }, [connect]);

  const subscribe = useCallback((callback: Subscriber) => {
    subscribersRef.current.add(callback);
    return () => {
      subscribersRef.current.delete(callback);
    };
  }, []);

  return (
    <WSContext.Provider value={{ connected, subscribe }}>
      {children}
    </WSContext.Provider>
  );
}

/**
 * Subscribe to WebSocket events. The callback is called for every event.
 * Returns { connected } status.
 */
export function useWSSubscribe(callback: Subscriber): { connected: boolean } {
  const { connected, subscribe } = useContext(WSContext);
  const callbackRef = useRef(callback);
  callbackRef.current = callback;

  useEffect(() => {
    return subscribe((event) => callbackRef.current(event));
  }, [subscribe]);

  return { connected };
}

import { useEffect, useRef, useState, useCallback } from 'react';
import type { WSEvent } from './types';

export function useWebSocket(onEvent: (event: WSEvent) => void) {
  const wsRef = useRef<WebSocket | null>(null);
  const [connected, setConnected] = useState(false);
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;
  const reconnectAttempt = useRef(0);

  const connect = useCallback(() => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host;
    const ws = new WebSocket(`${protocol}//${host}/ws`);

    ws.onopen = () => {
      setConnected(true);
      reconnectAttempt.current = 0;
    };

    ws.onmessage = (e) => {
      try {
        const event = JSON.parse(e.data);
        // Respond to server pings with pong (keepalive)
        if (event.type === 'ping') {
          ws.send(JSON.stringify({ type: 'pong' }));
          return;
        }
        onEventRef.current(event as WSEvent);
      } catch {
        // ignore malformed messages
      }
    };

    ws.onclose = () => {
      setConnected(false);
      // Exponential backoff: 1s, 2s, 4s, 8s, max 15s
      const delay = Math.min(1000 * Math.pow(2, reconnectAttempt.current), 15000);
      reconnectAttempt.current += 1;
      setTimeout(connect, delay);
    };

    ws.onerror = () => {
      ws.close();
    };

    wsRef.current = ws;
  }, []);

  useEffect(() => {
    connect();
    return () => {
      wsRef.current?.close();
    };
  }, [connect]);

  return { connected };
}

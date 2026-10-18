import { useEffect, useRef, useState, useCallback } from 'react';
import { WSMessage } from '../types/market';

export interface UseWebSocketOptions {
  onMessage?: (message: WSMessage) => void;
  url?: string;
  autoReconnect?: boolean;
}

export function useWebSocket({
  onMessage,
  url,
  autoReconnect = true,
}: UseWebSocketOptions = {}) {
  const [isConnected, setIsConnected] = useState<boolean>(false);
  const [latencyMs, setLatencyMs] = useState<number | null>(null);
  const reconnectCountRef = useRef<number>(0);

  const socketRef = useRef<WebSocket | null>(null);
  const pingTimestampRef = useRef<number>(0);
  const reconnectTimeoutRef = useRef<number | null>(null);
  const pingIntervalRef = useRef<number | null>(null);
  const onMessageRef = useRef(onMessage);

  onMessageRef.current = onMessage;

  const getWsUrl = useCallback(() => {
    if (url) return url;
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${protocol}//${window.location.host}/ws`;
  }, [url]);

  const connect = useCallback(() => {
    if (socketRef.current && (socketRef.current.readyState === WebSocket.OPEN || socketRef.current.readyState === WebSocket.CONNECTING)) {
      return;
    }

    const wsUrl = getWsUrl();
    try {
      const ws = new WebSocket(wsUrl);
      socketRef.current = ws;

      ws.onopen = () => {
        setIsConnected(true);
        reconnectCountRef.current = 0;

        // Immediate ping for latency calibration
        pingTimestampRef.current = Date.now();
        ws.send(JSON.stringify({ action: 'ping' }));

        // Start heartbeat ping every 15s
        if (pingIntervalRef.current) clearInterval(pingIntervalRef.current);
        pingIntervalRef.current = setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) {
            pingTimestampRef.current = Date.now();
            ws.send(JSON.stringify({ action: 'ping' }));
          }
        }, 15000);
      };

      ws.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data);
          if (payload.type === 'pong') {
            const rtt = Date.now() - pingTimestampRef.current;
            setLatencyMs(rtt);
          } else if (onMessageRef.current) {
            onMessageRef.current(payload as WSMessage);
          }
        } catch {
          // Ignore invalid JSON payloads
        }
      };

      ws.onclose = () => {
        setIsConnected(false);
        if (pingIntervalRef.current) clearInterval(pingIntervalRef.current);

        if (autoReconnect) {
          reconnectCountRef.current += 1;
          const backoff = Math.min(1000 * Math.pow(1.5, reconnectCountRef.current), 10000);
          reconnectTimeoutRef.current = setTimeout(() => {
            connect();
          }, backoff);
        }
      };

      ws.onerror = () => {
        ws.close();
      };
    } catch {
      setIsConnected(false);
    }
  }, [autoReconnect, getWsUrl]);

  useEffect(() => {
    connect();

    return () => {
      if (reconnectTimeoutRef.current) clearTimeout(reconnectTimeoutRef.current);
      if (pingIntervalRef.current) clearInterval(pingIntervalRef.current);
      if (socketRef.current) {
        socketRef.current.onclose = null;
        socketRef.current.close();
        socketRef.current = null;
      }
    };
  }, [connect]);

  const sendMessage = useCallback((msg: object) => {
    if (socketRef.current && socketRef.current.readyState === WebSocket.OPEN) {
      socketRef.current.send(JSON.stringify(msg));
    }
  }, []);

  return { isConnected, latencyMs, reconnectCount: reconnectCountRef.current, sendMessage };
}

import { useState, useEffect, useRef, useCallback } from "react";
import WebSocket from "ws";
import { parseServerMessage } from "../protocol.js";
import type { ServerMessage, ClientMessage } from "../types.js";

interface UseWebSocketReturn {
  connected: boolean;
  send: (msg: ClientMessage) => void;
  lastMessage: ServerMessage | null;
}

const MAX_RECONNECT_DELAY = 5000;
const INITIAL_RECONNECT_DELAY = 500;

export function useWebSocket(url: string): UseWebSocketReturn {
  const [connected, setConnected] = useState(false);
  const [lastMessage, setLastMessage] = useState<ServerMessage | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectDelay = useRef(INITIAL_RECONNECT_DELAY);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const unmounted = useRef(false);

  const connect = useCallback(() => {
    if (unmounted.current) return;

    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.on("open", () => {
      if (unmounted.current) {
        ws.close();
        return;
      }
      setConnected(true);
      reconnectDelay.current = INITIAL_RECONNECT_DELAY;
    });

    ws.on("message", (data: WebSocket.Data) => {
      const raw = typeof data === "string" ? data : data.toString();
      const parsed = parseServerMessage(raw);
      if (parsed) {
        setLastMessage(parsed);
      }
    });

    ws.on("close", () => {
      if (unmounted.current) return;
      setConnected(false);
      scheduleReconnect();
    });

    ws.on("error", () => {
      // Error triggers close, reconnect handled there.
      try {
        ws.close();
      } catch {
        // Ignore close errors.
      }
    });
  }, [url]);

  const scheduleReconnect = useCallback(() => {
    if (unmounted.current) return;
    const delay = reconnectDelay.current;
    reconnectDelay.current = Math.min(delay * 2, MAX_RECONNECT_DELAY);
    reconnectTimer.current = setTimeout(() => {
      connect();
    }, delay);
  }, [connect]);

  useEffect(() => {
    unmounted.current = false;
    connect();

    return () => {
      unmounted.current = true;
      if (reconnectTimer.current) {
        clearTimeout(reconnectTimer.current);
      }
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, [connect]);

  const send = useCallback((msg: ClientMessage) => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(msg));
    }
  }, []);

  return { connected, send, lastMessage };
}

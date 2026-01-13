import { useEffect, useRef, useCallback } from 'react';

export interface SSEEvent {
  type: string;
  [key: string]: any;
}

export interface UseSSEOptions {
  url: string;
  onMessage?: (event: SSEEvent) => void;
  onError?: (error: Event) => void;
  onOpen?: () => void;
  enabled?: boolean;
}

/**
 * React hook for consuming Server-Sent Events (SSE).
 *
 * Automatically manages connection lifecycle with reconnection logic.
 *
 * @example
 * ```tsx
 * useSSE({
 *   url: '/api/podcasts/events',
 *   onMessage: (event) => {
 *     if (event.type === 'episode_status') {
 *       console.log(`Episode ${event.episode_id} is now ${event.status}`);
 *     }
 *   }
 * });
 * ```
 */
export function useSSE({ url, onMessage, onError, onOpen, enabled = true }: UseSSEOptions) {
  const eventSourceRef = useRef<EventSource | null>(null);
  const reconnectTimeoutRef = useRef<NodeJS.Timeout>();
  const reconnectAttemptsRef = useRef(0);
  const maxReconnectAttempts = 5;
  const baseReconnectDelay = 1000; // 1 second

  const connect = useCallback(() => {
    if (!enabled) return;

    // Close existing connection if any
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
    }

    console.log('[SSE] Connecting to', url);

    const eventSource = new EventSource(url);
    eventSourceRef.current = eventSource;

    eventSource.onopen = () => {
      console.log('[SSE] Connected');
      reconnectAttemptsRef.current = 0; // Reset reconnect counter on successful connection
      onOpen?.();
    };

    eventSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        console.log('[SSE] Received:', data);
        onMessage?.(data);
      } catch (error) {
        console.error('[SSE] Failed to parse message:', error);
      }
    };

    eventSource.onerror = (error) => {
      console.error('[SSE] Error:', error);
      onError?.(error);

      // Close the connection
      eventSource.close();
      eventSourceRef.current = null;

      // Attempt to reconnect with exponential backoff
      if (reconnectAttemptsRef.current < maxReconnectAttempts) {
        const delay = baseReconnectDelay * Math.pow(2, reconnectAttemptsRef.current);
        console.log(`[SSE] Reconnecting in ${delay}ms (attempt ${reconnectAttemptsRef.current + 1}/${maxReconnectAttempts})`);

        reconnectTimeoutRef.current = setTimeout(() => {
          reconnectAttemptsRef.current++;
          connect();
        }, delay);
      } else {
        console.error('[SSE] Max reconnection attempts reached');
      }
    };
  }, [url, onMessage, onError, onOpen, enabled]);

  useEffect(() => {
    if (enabled) {
      connect();
    }

    return () => {
      console.log('[SSE] Cleaning up connection');
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
      }
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
        eventSourceRef.current = null;
      }
    };
  }, [connect, enabled]);

  return {
    isConnected: eventSourceRef.current?.readyState === EventSource.OPEN,
  };
}

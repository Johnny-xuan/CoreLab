/**
 * useWsStore — Pinia store that owns the singleton /ws/user connection.
 *
 * Phase 7 follow-up (FU-33). The store:
 * 1. Opens a WebSocket once the user logs in; closes + reopens on
 *    bearer-token change.
 * 2. Implements exponential reconnect (1 s → 30 s cap) with a tiny
 *    "connecting" / "online" / "offline" UI flag the layout can show.
 * 3. After a (re)connect, runs an `?since=<last_seen_iso>` REST
 *    catch-up against /api/v1/notifications so the bell never misses
 *    a row the browser was offline for.
 * 4. Dispatches the 5 server-push frame types (docs/05 §4.3):
 *      notification.new        → push to `notifications` array + bump unread
 *      gpu.live_update         → emit via `onGpuLiveUpdate` listeners
 *      reservation.status_change → emit via `onReservationStatusChange`,
 *        plus narrow lifecycle fan-out via `onReservationCreated` /
 *        `onReservationCancelled` / `onReservationTransition` (Phase H
 *        cross-page sync — payload.change discriminates).
 *      alert.new               → emit via `onAlertNew` listeners
 *      server.status_change    → emit via `onServerStatusChange`
 *
 * Subscribers register / unregister listeners; the store owns
 * fan-out so individual views (ReservationGrid, MyReservations, etc.)
 * stay decoupled from the wire format.
 */

import { defineStore } from 'pinia';
import { computed, ref } from 'vue';

import * as notificationsApi from '@/api/notifications';
import type { NotificationRead } from '@/api/notifications';
import { useAuthStore } from '@/stores/auth';

const LAST_SEEN_KEY = 'corelab.notifications.lastSeenIso';

type ConnectionState = 'offline' | 'connecting' | 'online';

interface ServerEnvelope<P = unknown> {
  type: string;
  id: string;
  ts: string;
  payload: P;
}

type Listener<P> = (payload: P) => void;

function safeGet(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function safeSet(key: string, value: string | null): void {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    // ignore (SSR / sandboxed test).
  }
}

function buildWsUrl(token: string): string {
  // The dev server proxies /api + /ws to caddy; in production both
  // share the same origin.
  if (typeof window === 'undefined') return '';
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
  return `${proto}://${window.location.host}/ws/user?token=${encodeURIComponent(token)}`;
}

export const useWsStore = defineStore('ws', () => {
  const state = ref<ConnectionState>('offline');
  const notifications = ref<NotificationRead[]>([]);
  const unreadCount = ref<number>(0);
  const lastSeenIso = ref<string | null>(safeGet(LAST_SEEN_KEY));

  let ws: WebSocket | null = null;
  let reconnectAttempts = 0;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let pingTimer: ReturnType<typeof setInterval> | null = null;
  let stopped = false;

  // Per-event listener arrays. Bound separately so a single subscriber
  // can listen to multiple events without registering for all of them.
  const reservationStatusListeners: Array<Listener<Record<string, unknown>>> = [];
  const reservationCreatedListeners: Array<Listener<Record<string, unknown>>> = [];
  const reservationCancelledListeners: Array<Listener<Record<string, unknown>>> = [];
  const reservationTransitionListeners: Array<Listener<Record<string, unknown>>> = [];
  const gpuLiveUpdateListeners: Array<Listener<Record<string, unknown>>> = [];
  const serverStatusListeners: Array<Listener<Record<string, unknown>>> = [];
  const alertNewListeners: Array<Listener<Record<string, unknown>>> = [];

  function onReservationStatusChange(cb: Listener<Record<string, unknown>>): () => void {
    reservationStatusListeners.push(cb);
    return () => {
      const i = reservationStatusListeners.indexOf(cb);
      if (i >= 0) reservationStatusListeners.splice(i, 1);
    };
  }

  function onReservationCreated(cb: Listener<Record<string, unknown>>): () => void {
    reservationCreatedListeners.push(cb);
    return () => {
      const i = reservationCreatedListeners.indexOf(cb);
      if (i >= 0) reservationCreatedListeners.splice(i, 1);
    };
  }

  function onReservationCancelled(cb: Listener<Record<string, unknown>>): () => void {
    reservationCancelledListeners.push(cb);
    return () => {
      const i = reservationCancelledListeners.indexOf(cb);
      if (i >= 0) reservationCancelledListeners.splice(i, 1);
    };
  }

  function onReservationTransition(cb: Listener<Record<string, unknown>>): () => void {
    reservationTransitionListeners.push(cb);
    return () => {
      const i = reservationTransitionListeners.indexOf(cb);
      if (i >= 0) reservationTransitionListeners.splice(i, 1);
    };
  }

  function onGpuLiveUpdate(cb: Listener<Record<string, unknown>>): () => void {
    gpuLiveUpdateListeners.push(cb);
    return () => {
      const i = gpuLiveUpdateListeners.indexOf(cb);
      if (i >= 0) gpuLiveUpdateListeners.splice(i, 1);
    };
  }

  function onServerStatusChange(cb: Listener<Record<string, unknown>>): () => void {
    serverStatusListeners.push(cb);
    return () => {
      const i = serverStatusListeners.indexOf(cb);
      if (i >= 0) serverStatusListeners.splice(i, 1);
    };
  }

  function onAlertNew(cb: Listener<Record<string, unknown>>): () => void {
    alertNewListeners.push(cb);
    return () => {
      const i = alertNewListeners.indexOf(cb);
      if (i >= 0) alertNewListeners.splice(i, 1);
    };
  }

  function dispatch(envelope: ServerEnvelope): void {
    switch (envelope.type) {
      case 'notification.new': {
        const row = envelope.payload as NotificationRead;
        notifications.value = [row, ...notifications.value].slice(0, 50);
        if (!row.is_read) unreadCount.value += 1;
        if (row.created_at) {
          lastSeenIso.value = row.created_at;
          safeSet(LAST_SEEN_KEY, row.created_at);
        }
        break;
      }
      case 'reservation.status_change': {
        const payload = envelope.payload as Record<string, unknown>;
        // Always emit on the generic listener for backwards compat
        // (existing PaReserve subscription).
        for (const cb of reservationStatusListeners) cb(payload);
        // Phase H — narrow fan-out by `change` discriminator so grid /
        // floating card / My Reservations can subscribe to only the
        // event class they care about.
        const change = typeof payload.change === 'string' ? payload.change : null;
        if (change === 'created') {
          for (const cb of reservationCreatedListeners) cb(payload);
        } else if (change === 'cancelled') {
          for (const cb of reservationCancelledListeners) cb(payload);
        } else if (change === 'transition') {
          for (const cb of reservationTransitionListeners) cb(payload);
        }
        break;
      }
      case 'gpu.live_update':
        for (const cb of gpuLiveUpdateListeners) {
          cb(envelope.payload as Record<string, unknown>);
        }
        break;
      case 'server.status_change':
        for (const cb of serverStatusListeners) {
          cb(envelope.payload as Record<string, unknown>);
        }
        break;
      case 'alert.new':
        for (const cb of alertNewListeners) {
          cb(envelope.payload as Record<string, unknown>);
        }
        break;
      case 'pong':
        // No-op — ping responses keep the half-open detection alive.
        break;
      default:
        // Future-proof by logging unknowns rather than crashing.
        console.debug('[ws] unknown frame', envelope.type, envelope);
    }
  }

  async function catchUp(): Promise<void> {
    try {
      const resp = await notificationsApi.listNotifications({
        since: lastSeenIso.value ?? undefined,
        limit: 50,
      });
      // ?since means we only got the gap; pre-prepend to existing.
      if (lastSeenIso.value) {
        // De-dup by id.
        const seen = new Set(notifications.value.map((n) => n.id));
        const fresh = resp.items.filter((n) => !seen.has(n.id));
        notifications.value = [...fresh, ...notifications.value].slice(0, 50);
      } else {
        notifications.value = resp.items.slice(0, 50);
      }
      unreadCount.value = resp.unread_count;
      if (resp.items.length > 0 && resp.items[0]) {
        lastSeenIso.value = resp.items[0].created_at;
        safeSet(LAST_SEEN_KEY, resp.items[0].created_at);
      }
    } catch (err) {
      console.warn('[ws] catch-up failed', err);
    }
  }

  function scheduleReconnect(): void {
    if (stopped) return;
    const delay = Math.min(30_000, 1000 * 2 ** Math.min(reconnectAttempts, 5));
    reconnectAttempts += 1;
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      void connect();
    }, delay);
  }

  function startPing(): void {
    if (pingTimer !== null) clearInterval(pingTimer);
    // 30 s — docs/05 §4.5. Sends `ping`; backend echoes `pong` (no-op).
    pingTimer = setInterval(() => {
      if (ws !== null && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'ping', payload: { ts: new Date().toISOString() } }));
      }
    }, 30_000);
  }

  function stopPing(): void {
    if (pingTimer !== null) {
      clearInterval(pingTimer);
      pingTimer = null;
    }
  }

  async function connect(): Promise<void> {
    const auth = useAuthStore();
    if (auth.token === null) return;
    if (ws !== null && ws.readyState <= WebSocket.OPEN) return;
    stopped = false;
    state.value = 'connecting';
    const url = buildWsUrl(auth.token);
    if (url === '') return;
    try {
      ws = new WebSocket(url);
    } catch (err) {
      console.warn('[ws] construct failed', err);
      state.value = 'offline';
      scheduleReconnect();
      return;
    }
    ws.onopen = (): void => {
      state.value = 'online';
      reconnectAttempts = 0;
      startPing();
      void catchUp();
    };
    ws.onmessage = (ev: MessageEvent): void => {
      try {
        const env = JSON.parse(String(ev.data)) as ServerEnvelope;
        dispatch(env);
      } catch (err) {
        console.warn('[ws] bad frame', err);
      }
    };
    ws.onclose = (): void => {
      state.value = 'offline';
      stopPing();
      ws = null;
      if (!stopped) scheduleReconnect();
    };
    ws.onerror = (): void => {
      // onclose will fire too; defer state change there.
    };
  }

  function disconnect(): void {
    stopped = true;
    if (reconnectTimer !== null) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    stopPing();
    if (ws !== null) {
      try {
        ws.close();
      } catch {
        // ignore
      }
      ws = null;
    }
    state.value = 'offline';
  }

  function reset(): void {
    disconnect();
    notifications.value = [];
    unreadCount.value = 0;
    lastSeenIso.value = null;
    safeSet(LAST_SEEN_KEY, null);
    reconnectAttempts = 0;
  }

  function markOneRead(id: number): void {
    let was_unread = false;
    notifications.value = notifications.value.map((n) => {
      if (n.id === id) {
        if (!n.is_read) was_unread = true;
        return { ...n, is_read: true, read_at: new Date().toISOString() };
      }
      return n;
    });
    if (was_unread && unreadCount.value > 0) unreadCount.value -= 1;
  }

  function markAllReadLocal(): void {
    notifications.value = notifications.value.map((n) =>
      n.is_read ? n : { ...n, is_read: true, read_at: new Date().toISOString() },
    );
    unreadCount.value = 0;
  }

  return {
    state: computed(() => state.value),
    notifications: computed(() => notifications.value),
    unreadCount: computed(() => unreadCount.value),
    connect,
    disconnect,
    reset,
    onReservationStatusChange,
    onReservationCreated,
    onReservationCancelled,
    onReservationTransition,
    onGpuLiveUpdate,
    onServerStatusChange,
    onAlertNew,
    markOneRead,
    markAllReadLocal,
    // Test-only access; not exported via index but accessible by name.
    _dispatch: dispatch,
  };
});

/**
 * useWsStore unit tests — verify event dispatch + listener registry
 * without touching the network.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { setActivePinia, createPinia } from 'pinia';

import { useWsStore } from '@/stores/ws';
import type { NotificationRead } from '@/api/notifications';

function makeNotif(overrides: Partial<NotificationRead>): NotificationRead {
  return {
    id: 1,
    type: 'reservation.started',
    severity: 'info',
    title: 'hi',
    body: null,
    payload: { reservation_id: 1 },
    cta_url: null,
    is_read: false,
    read_at: null,
    created_at: new Date().toISOString(),
    ...overrides,
  };
}

interface WsStoreInternal {
  _dispatch: (env: { type: string; id: string; ts: string; payload: unknown }) => void;
}

describe('useWsStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it('starts offline with no notifications', () => {
    const store = useWsStore();
    expect(store.state).toBe('offline');
    expect(store.notifications).toEqual([]);
    expect(store.unreadCount).toBe(0);
  });

  it('dispatch notification.new prepends and bumps unread', () => {
    const store = useWsStore() as unknown as ReturnType<typeof useWsStore> & WsStoreInternal;
    store._dispatch({
      type: 'notification.new',
      id: 'a',
      ts: new Date().toISOString(),
      payload: makeNotif({ id: 1 }),
    });
    store._dispatch({
      type: 'notification.new',
      id: 'b',
      ts: new Date().toISOString(),
      payload: makeNotif({ id: 2, title: 'second' }),
    });
    expect(store.notifications.length).toBe(2);
    expect(store.notifications[0]?.id).toBe(2);
    expect(store.unreadCount).toBe(2);
  });

  it('dispatch reservation.status_change fans out to listeners', () => {
    const store = useWsStore() as unknown as ReturnType<typeof useWsStore> & WsStoreInternal;
    const calls: Array<Record<string, unknown>> = [];
    const unsubscribe = store.onReservationStatusChange((p) => calls.push(p));
    store._dispatch({
      type: 'reservation.status_change',
      id: 'r',
      ts: new Date().toISOString(),
      payload: { reservation_id: 42, status: 'active' },
    });
    expect(calls).toEqual([{ reservation_id: 42, status: 'active' }]);
    unsubscribe();
    store._dispatch({
      type: 'reservation.status_change',
      id: 's',
      ts: new Date().toISOString(),
      payload: { reservation_id: 43, status: 'completed' },
    });
    expect(calls.length).toBe(1);
  });

  it('dispatch alert.new fans out to alert listeners', () => {
    const store = useWsStore() as unknown as ReturnType<typeof useWsStore> & WsStoreInternal;
    const calls: Array<Record<string, unknown>> = [];
    const unsubscribe = store.onAlertNew((p) => calls.push(p));
    store._dispatch({
      type: 'alert.new',
      id: 'a',
      ts: new Date().toISOString(),
      payload: { alert_event_id: 9, severity: 'warn' },
    });
    expect(calls).toEqual([{ alert_event_id: 9, severity: 'warn' }]);
    unsubscribe();
    store._dispatch({
      type: 'alert.new',
      id: 'b',
      ts: new Date().toISOString(),
      payload: { alert_event_id: 10, severity: 'error' },
    });
    expect(calls.length).toBe(1);
  });

  it('markOneRead decrements unreadCount only when the row was unread', () => {
    const store = useWsStore() as unknown as ReturnType<typeof useWsStore> & WsStoreInternal;
    store._dispatch({
      type: 'notification.new',
      id: 'x',
      ts: new Date().toISOString(),
      payload: makeNotif({ id: 7 }),
    });
    expect(store.unreadCount).toBe(1);
    store.markOneRead(7);
    expect(store.unreadCount).toBe(0);
    // Calling again on an already-read row is a no-op.
    store.markOneRead(7);
    expect(store.unreadCount).toBe(0);
  });

  it('markAllReadLocal zeros the counter and flips every row', () => {
    const store = useWsStore() as unknown as ReturnType<typeof useWsStore> & WsStoreInternal;
    for (let i = 0; i < 3; i++) {
      store._dispatch({
        type: 'notification.new',
        id: `n-${i}`,
        ts: new Date().toISOString(),
        payload: makeNotif({ id: i + 1 }),
      });
    }
    expect(store.unreadCount).toBe(3);
    store.markAllReadLocal();
    expect(store.unreadCount).toBe(0);
    expect(store.notifications.every((n) => n.is_read)).toBe(true);
  });
});

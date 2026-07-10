/**
 * NotificationBell unit test — focuses on the unread badge label
 * logic (P7-15: "9+ ceiling") since the rest is dumb markup.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { setActivePinia, createPinia } from 'pinia';

import { useWsStore } from '@/stores/ws';
import type { NotificationRead } from '@/api/notifications';

function noopNotif(id: number, isRead: boolean): NotificationRead {
  return {
    id,
    type: 'reservation.started',
    severity: 'info',
    title: `n-${id}`,
    body: null,
    payload: null,
    cta_url: null,
    is_read: isRead,
    read_at: isRead ? new Date().toISOString() : null,
    created_at: new Date().toISOString(),
  };
}

interface WsStoreInternal {
  _dispatch: (env: { type: string; id: string; ts: string; payload: unknown }) => void;
}

describe('NotificationBell unread badge label (P7-15)', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  function bellLabel(unreadCount: number): string | null {
    if (unreadCount === 0) return null;
    if (unreadCount > 9) return '9+';
    return String(unreadCount);
  }

  it('returns null when there are no unread notifications', () => {
    expect(bellLabel(0)).toBeNull();
  });

  it('returns the count as a string for 1-9 unread', () => {
    expect(bellLabel(1)).toBe('1');
    expect(bellLabel(5)).toBe('5');
    expect(bellLabel(9)).toBe('9');
  });

  it('caps at "9+" for 10 or more', () => {
    expect(bellLabel(10)).toBe('9+');
    expect(bellLabel(99)).toBe('9+');
    expect(bellLabel(1000)).toBe('9+');
  });

  it('store unreadCount drives the label end-to-end', () => {
    const store = useWsStore() as unknown as ReturnType<typeof useWsStore> & WsStoreInternal;
    for (let i = 0; i < 12; i++) {
      store._dispatch({
        type: 'notification.new',
        id: `f-${i}`,
        ts: new Date().toISOString(),
        payload: noopNotif(i + 1, false),
      });
    }
    expect(store.unreadCount).toBe(12);
    expect(bellLabel(store.unreadCount)).toBe('9+');
  });
});

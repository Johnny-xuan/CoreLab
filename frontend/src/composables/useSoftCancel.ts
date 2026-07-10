/**
 * Soft-cancel — a 5-second undo window for any "destructive" call.
 *
 * Pattern, originally inline in PaReserve.vue:
 *   1. consumer asks to cancel reservation #N
 *   2. surface a toast "已取消 #N · 5s 内可撤销"
 *   3. optimistically remove from the local list (caller's responsibility)
 *   4. 5 s later, fire the real API; on failure, caller refetches
 *
 * The window is closed implicitly when the toast finishes — the
 * `onAfterLeave` callback clears any leftover timer (handles the
 * "toast destroyed early by message.destroyAll()" corner case).
 *
 * Caller wires three things:
 *   - `cancel(id, payload)` — the real API call
 *   - `onOptimisticRemove(id)` — local removal hook (e.g. filter list)
 *   - `onFailure(err)` — toast / refetch on API failure
 *
 * NB: naive's useMessage doesn't expose an action button, so the toast
 * is "passive" — a 5 s countdown rather than a clickable "Undo". A real
 * Undo button would need a different toast component.
 */
import { ref } from 'vue';
import { useMessage } from 'naive-ui';

export interface SoftCancelOptions {
  cancel: (id: number, payload: { reason: string }) => Promise<unknown>;
  onOptimisticRemove?: (id: number) => void;
  onFailure?: (err: unknown) => void;
  windowMs?: number;
}

interface PendingCancel {
  reservationId: number;
  timer: ReturnType<typeof setTimeout>;
}

export function useSoftCancel(opts: SoftCancelOptions) {
  const message = useMessage();
  const pending = ref<Map<number, PendingCancel>>(new Map());
  const windowMs = opts.windowMs ?? 5_000;

  function softCancel(reservationId: number): void {
    if (pending.value.has(reservationId)) return;
    const toast = message.success(`已取消 #${reservationId},5 秒内可撤销`, {
      closable: false,
      duration: windowMs,
      onAfterLeave: () => {
        const p = pending.value.get(reservationId);
        if (p === undefined) return;
        clearTimeout(p.timer);
        pending.value.delete(reservationId);
      },
    });
    opts.onOptimisticRemove?.(reservationId);
    const timer = setTimeout(async () => {
      pending.value.delete(reservationId);
      try {
        await opts.cancel(reservationId, { reason: 'user undo timeout' });
        toast.destroy();
      } catch (err) {
        opts.onFailure?.(err);
      }
    }, windowMs);
    pending.value.set(reservationId, { reservationId, timer });
  }

  function clearAllPending(): void {
    for (const p of pending.value.values()) clearTimeout(p.timer);
    pending.value.clear();
  }

  return { softCancel, pending, clearAllPending };
}

/**
 * Conflict preview — POST /reservations/preview every time the user
 * touches the selection, debounced so we don't hammer the backend on
 * fast click sequences.
 *
 * Returns the derived state the grid + bottom panel need:
 *   - `conflictKeys` — set of `${gpu}:${slot}` strings; grid paints
 *     these cells with a red overlay
 *   - `lastTimeChecks` — backend's per-policy verdict; bottom panel
 *     surfaces "would exceed weekly cap" warnings
 *   - `runPreview()` — manual trigger (after a successful submit, for
 *     example, when WS will land but you want a deterministic snapshot)
 *
 * Originally inline in PaReserve.vue.
 */
import { ref, watch, type Ref } from 'vue';

import * as resApi from '@/api/reservations';
import { cellKey, draftToRange, selectionDrafts } from '@/components/reservation/gridUtils';

export interface ConflictPreviewContext {
  selecting: Ref<Set<string>>;
  dayIso: Ref<string>;
  /** Returns null when the workspace entry isn't ready yet. */
  serverId: () => number | null;
  accountLinkId: () => number | null;
  onError?: (err: unknown) => void;
  debounceMs?: number;
}

export function useConflictPreview(ctx: ConflictPreviewContext) {
  const conflictKeys = ref<Set<string>>(new Set());
  const lastTimeChecks = ref<resApi.TimeLimitCheck[]>([]);
  let debounceTimer: ReturnType<typeof setTimeout> | null = null;
  const debounceMs = ctx.debounceMs ?? 300;

  async function runPreview(): Promise<void> {
    const sid = ctx.serverId();
    const linkId = ctx.accountLinkId();
    if (sid === null || linkId === null || ctx.selecting.value.size === 0) {
      conflictKeys.value = new Set();
      lastTimeChecks.value = [];
      return;
    }
    const drafts = selectionDrafts(ctx.selecting.value);
    const items: resApi.PreviewItemInput[] = drafts.map((d) => {
      const range = draftToRange(ctx.dayIso.value, d);
      return {
        server_id: sid,
        gpu_id: d.gpuId,
        start_at: range.startIso,
        end_at: range.endIso,
      };
    });
    try {
      const resp = await resApi.previewConflicts({ items, account_link_id: linkId });
      const conflicting = new Set<string>();
      for (const c of resp.conflicts) {
        const d = drafts[c.input_index];
        if (d === undefined) continue;
        for (let s = d.firstSlot; s <= d.lastSlot; s += 1) {
          conflicting.add(cellKey(d.gpuId, s));
        }
      }
      conflictKeys.value = conflicting;
      lastTimeChecks.value = resp.time_limit_checks;
    } catch (err) {
      ctx.onError?.(err);
    }
  }

  watch(
    () => ctx.selecting.value.size,
    () => {
      if (debounceTimer !== null) clearTimeout(debounceTimer);
      debounceTimer = setTimeout(() => void runPreview(), debounceMs);
    },
  );

  function clear(): void {
    conflictKeys.value = new Set();
    lastTimeChecks.value = [];
  }

  return { conflictKeys, lastTimeChecks, runPreview, clear };
}

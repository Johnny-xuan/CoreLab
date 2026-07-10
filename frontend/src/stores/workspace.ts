/**
 * Workspace store — drives the PA-centric IA (docs/07-ui-design.md §3.1).
 *
 * Each entry corresponds to one of the user's *active* account_links,
 * i.e. one Linux account they can act-as on some server. The currently
 * selected PA id is mirrored into the URL by the router so deep links
 * stay stable across reloads.
 */

import { defineStore } from 'pinia';
import { computed, ref } from 'vue';

import * as accountLinksApi from '@/api/accountLinks';
import * as physicalAccountsApi from '@/api/physicalAccounts';
import type { AccountLinkRead } from '@/api/accountLinks';
import type { PhysicalAccountRead } from '@/api/physicalAccounts';

const CURRENT_KEY = 'corelab.workspace.currentPaId';

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
    // ignore (SSR / sandboxed tests).
  }
}

export interface WorkspaceEntry {
  pa: PhysicalAccountRead;
  link: AccountLinkRead;
}

export const useWorkspaceStore = defineStore('workspace', () => {
  const links = ref<AccountLinkRead[]>([]);
  const pas = ref<Map<number, PhysicalAccountRead>>(new Map());
  const loading = ref(false);
  const currentPaId = ref<number | null>(loadStoredPaId());

  function loadStoredPaId(): number | null {
    const raw = safeGet(CURRENT_KEY);
    if (raw === null) return null;
    const n = Number(raw);
    return Number.isFinite(n) ? n : null;
  }

  const workspaces = computed<WorkspaceEntry[]>(() => {
    const out: WorkspaceEntry[] = [];
    for (const link of links.value) {
      const pa = pas.value.get(link.physical_account_id);
      if (pa !== undefined) out.push({ pa, link });
    }
    return out;
  });

  const current = computed<WorkspaceEntry | null>(() => {
    if (currentPaId.value === null) return null;
    return workspaces.value.find((w) => w.pa.id === currentPaId.value) ?? null;
  });

  async function refresh(): Promise<void> {
    loading.value = true;
    try {
      const myLinks = await accountLinksApi.listMyAccountLinks();
      links.value = myLinks;
      const fresh = new Map<number, PhysicalAccountRead>();
      // Fetch PA details in parallel for active links we don't yet have.
      const ids = Array.from(new Set(myLinks.map((l) => l.physical_account_id)));
      const fetched = await Promise.all(
        ids.map((id) =>
          physicalAccountsApi
            .getPa(id)
            .then<[number, PhysicalAccountRead]>((pa) => [id, pa])
            .catch<[number, null]>(() => [id, null]),
        ),
      );
      for (const [id, pa] of fetched) {
        if (pa !== null) fresh.set(id, pa);
      }
      pas.value = fresh;

      // Reset current selection if the stored PA is no longer linked.
      if (currentPaId.value !== null && !fresh.has(currentPaId.value)) {
        currentPaId.value = null;
        safeSet(CURRENT_KEY, null);
      }

      // Auto-select the first active workspace if the user hasn't picked one.
      // Without this the sidebar's "In this workspace" subsection never
      // appears on a fresh login, even when the user does have a PA.
      if (currentPaId.value === null) {
        const firstActive = myLinks.find((l) => l.is_active && fresh.has(l.physical_account_id));
        if (firstActive !== undefined) {
          currentPaId.value = firstActive.physical_account_id;
          safeSet(CURRENT_KEY, String(firstActive.physical_account_id));
        }
      }
    } finally {
      loading.value = false;
    }
  }

  function setCurrent(paId: number | null): void {
    currentPaId.value = paId;
    safeSet(CURRENT_KEY, paId === null ? null : String(paId));
  }

  function clear(): void {
    links.value = [];
    pas.value = new Map();
    currentPaId.value = null;
    safeSet(CURRENT_KEY, null);
  }

  return { links, pas, workspaces, current, currentPaId, loading, refresh, setCurrent, clear };
});

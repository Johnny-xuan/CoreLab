/**
 * Module-level shared state for the sidebar.
 *
 * Every page view wraps itself in <AppLayout>, so the AppLayout
 * instance — and any local refs inside it — re-mount on every route
 * change. Anything we want to survive navigation has to live up here
 * at module scope.
 *
 * Two pieces of state are hoisted:
 *   1. ``sidebarCollapsed`` — whether the rail is shrunk to icons.
 *      Persisted to localStorage so it also survives full reloads.
 *   2. ``sidebarScrollTop`` — pixel scroll offset of the menu's
 *      scrollable container. Without this, navigating to a deep menu
 *      item scrolls the sidebar back to 0 on every click. We don't
 *      persist this one (it's session-only — preserving scroll across
 *      reloads would be surprising).
 *
 * Note: an earlier iteration also hoisted ``sidebarLevel`` for a
 * Vercel-style sub-sidebar drill. That mode was reverted (the user
 * found it harder than inline-expanded groups), so the sidebar is now
 * a single flat NMenu with section groups.
 */
import { ref, watch } from 'vue';

const COLLAPSED_KEY = 'corelab.sidebar.collapsed';

function readInitialCollapsed(): boolean {
  try {
    return localStorage.getItem(COLLAPSED_KEY) === '1';
  } catch {
    return false;
  }
}

const sidebarCollapsed = ref<boolean>(readInitialCollapsed());
const sidebarScrollTop = ref<number>(0);

watch(sidebarCollapsed, (v) => {
  try {
    localStorage.setItem(COLLAPSED_KEY, v ? '1' : '0');
  } catch {
    // best-effort — quota / private mode fallthrough.
  }
});

export function useSidebarState() {
  return { sidebarCollapsed, sidebarScrollTop };
}

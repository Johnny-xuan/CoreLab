/**
 * Theme preference — three-state (light / dark / auto). "auto" tracks
 * the OS via `prefers-color-scheme` live so the page repaints on
 * macOS / Windows day-night toggles without a refresh.
 *
 * Two side effects, split so each `watch` has one job:
 *   - persist `themePref` to localStorage (preference itself)
 *   - reflect the resolved `isDark` onto `<html data-theme="dark">`
 *     so styles/tokens.css's `[data-theme='dark']` block kicks in
 *
 * `isDark` is exposed for the NConfigProvider in App.vue.
 *
 * Module-level state so every consumer shares one ref tree — see
 * useSidebarState.ts for the same pattern.
 */
import { computed, ref, watch } from 'vue';

export type ThemePref = 'light' | 'dark' | 'auto';

const STORAGE_KEY = 'corelab.theme.pref';

function readInitial(): ThemePref {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    if (v === 'light' || v === 'dark' || v === 'auto') return v;
  } catch {
    // localStorage unavailable — default below.
  }
  return 'auto';
}

const themePref = ref<ThemePref>(readInitial());

// Track OS scheme so "auto" follows it live without a page reload.
const mq =
  typeof window !== 'undefined' && typeof window.matchMedia === 'function'
    ? window.matchMedia('(prefers-color-scheme: dark)')
    : null;

const osDark = ref<boolean>(mq?.matches ?? false);

mq?.addEventListener('change', (e: MediaQueryListEvent) => {
  osDark.value = e.matches;
});

const isDark = computed<boolean>(() => {
  if (themePref.value === 'dark') return true;
  if (themePref.value === 'light') return false;
  return osDark.value;
});

// Persist preference.
watch(themePref, (pref) => {
  try {
    localStorage.setItem(STORAGE_KEY, pref);
  } catch {
    // best-effort — quota / private mode fallthrough.
  }
});

// Reflect resolved theme onto <html data-theme>.
watch(
  isDark,
  (dark) => {
    if (typeof document === 'undefined') return;
    const html = document.documentElement;
    if (dark) html.setAttribute('data-theme', 'dark');
    else html.removeAttribute('data-theme');
  },
  { immediate: true },
);

export function useThemePref() {
  return { themePref, isDark, osDark };
}

<script setup lang="ts">
/**
 * ThemeToggle — single round chip in the topbar that cycles the
 * user's theme preference: Auto → Light → Dark → Auto.
 *
 * Auto = follow `prefers-color-scheme`. The resolved value is read
 * back from useThemePref.isDark so the button title reads e.g.
 * "Auto (Dark)" when the OS is currently dark.
 */
import { computed } from 'vue';
import { Monitor, Moon, Sun } from 'lucide-vue-next';

import { useThemePref } from '@/composables/useThemePref';

const { themePref, isDark } = useThemePref();

const NEXT: Record<'light' | 'dark' | 'auto', 'light' | 'dark' | 'auto'> = {
  light: 'dark',
  dark: 'auto',
  auto: 'light',
};

function cycle(): void {
  themePref.value = NEXT[themePref.value];
}

const icon = computed(() =>
  themePref.value === 'light' ? Sun : themePref.value === 'dark' ? Moon : Monitor,
);
const title = computed(() => {
  if (themePref.value === 'light') return '浅色';
  if (themePref.value === 'dark') return '深色';
  return `自动(${isDark.value ? '深色' : '浅色'})`;
});
</script>

<template>
  <button class="theme-chip" type="button" :title="title" @click="cycle">
    <component :is="icon" :size="14" :stroke-width="1.75" />
  </button>
</template>

<style scoped>
.theme-chip {
  width: 30px;
  height: 30px;
  padding: 0;
  border-radius: var(--radius-full);
  background: var(--c-bg-base);
  color: var(--c-text-secondary);
  border: 1px solid var(--c-border-default);
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  transition:
    background-color 80ms ease,
    color 80ms ease;
}
.theme-chip:hover {
  background: var(--c-bg-sunken);
  color: var(--c-text-primary);
}
</style>

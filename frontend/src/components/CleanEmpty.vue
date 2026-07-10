<script setup lang="ts">
/**
 * CleanEmpty — Vercel-styled empty state.
 *
 * Replaces Naive UI's default NEmpty (cartoony icon + small caption)
 * with a minimal lucide glyph + tight typography that matches the rest
 * of the dashboard chrome (1px borders, mono numbers, near-zero shadow).
 *
 * Usage:
 *   <CleanEmpty title="No requests" description="Pending requests will show up here." />
 *
 * Defaults to PackageOpen; pass any lucide-vue-next component via the
 * `icon` prop to swap it (e.g. MailX for notifications, Inbox for
 * audit logs).
 */
import type { Component } from 'vue';
import { PackageOpen } from 'lucide-vue-next';

withDefaults(
  defineProps<{
    icon?: Component;
    title?: string;
    description?: string;
    compact?: boolean;
  }>(),
  {
    icon: () => PackageOpen,
    title: '',
    description: '',
    compact: false,
  },
);
</script>

<template>
  <div :class="['clean-empty', { 'is-compact': compact }]">
    <span class="ce-icon" aria-hidden="true">
      <component :is="icon" :size="compact ? 18 : 22" :stroke-width="1.5" />
    </span>
    <span v-if="title" class="ce-title">{{ title }}</span>
    <span v-if="description" class="ce-desc">{{ description }}</span>
    <slot />
  </div>
</template>

<style scoped>
.clean-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  padding: var(--space-8) var(--space-4);
  color: var(--c-text-tertiary);
  text-align: center;
}
.clean-empty.is-compact {
  padding: var(--space-4) var(--space-3);
  gap: var(--space-1);
}
.ce-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border-radius: var(--radius-md);
  background: var(--c-bg-sunken);
  border: 1px solid var(--c-border-subtle);
  color: var(--c-text-secondary);
  margin-bottom: var(--space-1);
}
.is-compact .ce-icon {
  width: 28px;
  height: 28px;
  margin-bottom: 0;
}
.ce-title {
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--c-text-primary);
}
.ce-desc {
  font-size: var(--text-xs);
  color: var(--c-text-tertiary);
  line-height: var(--leading-snug);
  max-width: 28rem;
}
</style>

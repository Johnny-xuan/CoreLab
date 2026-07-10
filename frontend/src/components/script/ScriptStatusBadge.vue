<script setup lang="ts">
/**
 * ScriptStatusBadge — compact pill showing the script lifecycle state.
 *
 * One badge per reservation row; reused in the Scripts page, in the
 * MyReservations list (T82), and (optionally) in WS-driven notifications.
 *
 * Visual encoding (kept tight so the badge fits a table cell):
 *   • none      — empty dash, almost invisible (user opted out of cron)
 *   • scheduled — neutral, clock icon (attached, awaiting dispatch)
 *   • running   — accent color, looping loader (live)
 *   • completed — green, check (exit 0)
 *   • failed    — red, X (non-zero exit / agent crash)
 *   • killed    — amber, ban (user cancel or max_runtime kill)
 */
import { computed } from 'vue';
import { Ban, CheckCircle2, Clock, Loader2, Minus, XCircle } from 'lucide-vue-next';
import type { FunctionalComponent } from 'vue';

import type { ScriptUIStatus } from './scriptHelpers';

type IconCmp = FunctionalComponent;

interface Props {
  status: ScriptUIStatus;
  size?: 'sm' | 'md';
  showLabel?: boolean;
}

const props = withDefaults(defineProps<Props>(), {
  size: 'sm',
  showLabel: true,
});

interface Meta {
  label: string;
  tone: 'neutral' | 'muted' | 'accent' | 'success' | 'danger' | 'warning';
  icon: IconCmp;
  spin: boolean;
}

const META: Record<ScriptUIStatus, Meta> = {
  none: { label: '无脚本', tone: 'muted', icon: Minus, spin: false },
  scheduled: { label: '待跑', tone: 'neutral', icon: Clock, spin: false },
  running: { label: '运行中', tone: 'accent', icon: Loader2, spin: true },
  completed: { label: '已完成', tone: 'success', icon: CheckCircle2, spin: false },
  failed: { label: '失败', tone: 'danger', icon: XCircle, spin: false },
  killed: { label: '已终止', tone: 'warning', icon: Ban, spin: false },
};

const meta = computed<Meta>(() => META[props.status]);
const iconSize = computed(() => (props.size === 'md' ? 14 : 11));
</script>

<template>
  <span
    class="badge"
    :class="[`tone-${meta.tone}`, `size-${size}`]"
    :title="meta.label"
    role="status"
  >
    <component
      :is="meta.icon"
      :size="iconSize"
      :stroke-width="2"
      class="icon"
      :class="{ spin: meta.spin }"
    />
    <span v-if="showLabel" class="label">{{ meta.label }}</span>
  </span>
</template>

<style scoped>
.badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 500;
  line-height: 1.4;
  white-space: nowrap;
  border: 1px solid transparent;
}
.badge.size-md {
  padding: 3px 10px;
  font-size: 12px;
  gap: 5px;
}
.badge .icon {
  flex: 0 0 auto;
}
.badge .label {
  letter-spacing: 0.01em;
}

.tone-muted {
  background: transparent;
  color: var(--c-text-tertiary);
}
.tone-neutral {
  background: var(--c-bg-sunken);
  color: var(--c-text-secondary);
  border-color: var(--c-border-subtle);
}
.tone-accent {
  background: color-mix(in oklab, var(--c-accent) 14%, transparent);
  color: var(--c-accent);
  border-color: color-mix(in oklab, var(--c-accent) 30%, transparent);
}
.tone-success {
  background: color-mix(in oklab, var(--c-success, #10b981) 14%, transparent);
  color: var(--c-success, #059669);
  border-color: color-mix(in oklab, var(--c-success, #10b981) 30%, transparent);
}
.tone-danger {
  background: color-mix(in oklab, var(--c-danger, #ef4444) 14%, transparent);
  color: var(--c-danger, #dc2626);
  border-color: color-mix(in oklab, var(--c-danger, #ef4444) 30%, transparent);
}
.tone-warning {
  background: color-mix(in oklab, var(--c-warning, #f59e0b) 14%, transparent);
  color: var(--c-warning, #d97706);
  border-color: color-mix(in oklab, var(--c-warning, #f59e0b) 30%, transparent);
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
.icon.spin {
  animation: spin 1.2s linear infinite;
}
</style>

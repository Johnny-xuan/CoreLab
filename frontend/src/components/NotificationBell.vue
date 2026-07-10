<script setup lang="ts">
/**
 * NotificationBell — topbar bell button + popover list (Phase 7 FU-34).
 *
 * Reads from useWsStore (live push + initial REST catch-up); writes
 * back through notifications API for mark-read / mark-all-read.
 * Unread count is capped at "9+" per docs/07 §3.2 / brief P7-15.
 *
 * Visuals: lucide Bell glyph (1.75 stroke) on a 28×28 hit-target. Popover
 * mirrors Vercel's compact dropdown — 1px subtle border, 8px radius,
 * mono timestamp + severity dot per row, no shadow except a soft drop.
 */

import { computed } from 'vue';
import { useRouter } from 'vue-router';
import { NBadge, NPopover, useMessage } from 'naive-ui';
import { Bell, BellOff } from 'lucide-vue-next';

import CleanEmpty from '@/components/CleanEmpty.vue';
import * as notificationsApi from '@/api/notifications';
import type { NotificationRead } from '@/api/notifications';
import { useWsStore } from '@/stores/ws';

const ws = useWsStore();
const router = useRouter();
const message = useMessage();

const unreadLabel = computed(() => {
  const n = ws.unreadCount;
  if (n === 0) return null;
  if (n > 9) return '9+';
  return String(n);
});

const items = computed<NotificationRead[]>(() => ws.notifications.slice(0, 20));

function timeAgo(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  const secs = Math.floor(ms / 1000);
  if (secs < 60) return `${secs}s`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h`;
  return `${Math.floor(secs / 86400)}d`;
}

async function handleClick(notif: NotificationRead): Promise<void> {
  if (!notif.is_read) {
    try {
      await notificationsApi.markRead(notif.id);
      ws.markOneRead(notif.id);
    } catch {
      // Best-effort — UI stays consistent on next REST catch-up.
    }
  }
  if (notif.cta_url) {
    await router.push(notif.cta_url);
  }
}

async function handleMarkAll(): Promise<void> {
  try {
    const count = await notificationsApi.markAllRead();
    ws.markAllReadLocal();
    if (count > 0) message.success(`已将 ${count} 条标记为已读`);
  } catch {
    message.error('全部标记已读失败');
  }
}

const severityClass = (s: NotificationRead['severity']): string => `severity-${s}`;
</script>

<template>
  <NPopover trigger="click" placement="bottom-end" :width="360" :show-arrow="false" raw>
    <template #trigger>
      <button class="bell-button" type="button" aria-label="通知">
        <NBadge
          v-if="unreadLabel !== null"
          :value="unreadLabel"
          :max="99"
          type="error"
          :offset="[2, -2]"
        >
          <Bell :size="16" :stroke-width="1.75" />
        </NBadge>
        <Bell v-else :size="16" :stroke-width="1.75" />
      </button>
    </template>
    <div class="bell-popover">
      <header class="bell-header">
        <span class="bell-title">通知</span>
        <button
          v-if="ws.unreadCount > 0"
          type="button"
          class="bell-mark-all"
          @click="handleMarkAll"
        >
          全部标记已读
        </button>
      </header>
      <ul v-if="items.length > 0" class="bell-list">
        <li
          v-for="n in items"
          :key="n.id"
          :class="['bell-item', severityClass(n.severity), { unread: !n.is_read }]"
          @click="handleClick(n)"
        >
          <span :class="['bell-dot', severityClass(n.severity)]" aria-hidden="true" />
          <div class="bell-item-body">
            <span class="bell-item-title">{{ n.title }}</span>
            <span v-if="n.body" class="bell-item-desc">{{ n.body }}</span>
            <span class="bell-item-meta">
              <code class="bell-item-type">{{ n.type }}</code>
              <span class="bell-item-sep">·</span>
              <span class="bell-item-time mono tabular">{{ timeAgo(n.created_at) }}前</span>
            </span>
          </div>
        </li>
      </ul>
      <CleanEmpty
        v-else
        :icon="BellOff"
        title="暂无通知"
        description="心跳、告警和关联决定会显示在这里。"
        compact
      />
    </div>
  </NPopover>
</template>

<style scoped>
.bell-button {
  background: transparent;
  border: 1px solid transparent;
  cursor: pointer;
  width: 28px;
  height: 28px;
  border-radius: var(--radius-md);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: var(--c-text-secondary);
  transition:
    background 120ms ease,
    color 120ms ease,
    border-color 120ms ease;
}
.bell-button:hover {
  background: var(--c-bg-sunken);
  color: var(--c-text-primary);
  border-color: var(--c-border-subtle);
}

.bell-popover {
  width: 360px;
  background: var(--c-bg-elevated);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-md);
  overflow: hidden;
}
.bell-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-3) var(--space-4);
  border-bottom: 1px solid var(--c-border-subtle);
  background: var(--c-bg-elevated);
}
.bell-title {
  font-weight: 600;
  font-size: var(--text-sm);
  letter-spacing: var(--tracking-tight);
  color: var(--c-text-primary);
}
.bell-mark-all {
  font-size: var(--text-xs);
  color: var(--c-text-link);
  background: none;
  border: none;
  cursor: pointer;
  padding: 0;
}
.bell-mark-all:hover {
  text-decoration: underline;
  text-underline-offset: 3px;
}

.bell-list {
  list-style: none;
  margin: 0;
  padding: 0;
  max-height: 420px;
  overflow-y: auto;
}
.bell-item {
  display: grid;
  grid-template-columns: 8px 1fr;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  border-bottom: 1px solid var(--c-border-subtle);
  cursor: pointer;
  transition: background 120ms ease;
}
.bell-item:last-child {
  border-bottom: none;
}
.bell-item:hover {
  background: var(--c-bg-sunken);
}
.bell-item.unread {
  background: var(--c-bg-sunken);
}
.bell-item.unread .bell-item-title {
  font-weight: 600;
}

.bell-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-top: 6px;
  background: var(--c-dot-info);
}
.bell-dot.severity-warn {
  background: var(--c-dot-warn);
}
.bell-dot.severity-error {
  background: var(--c-danger);
}

.bell-item-body {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.bell-item-title {
  font-size: var(--text-sm);
  color: var(--c-text-primary);
  line-height: var(--leading-snug);
}
.bell-item-desc {
  font-size: var(--text-xs);
  color: var(--c-text-secondary);
  line-height: var(--leading-snug);
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}
.bell-item-meta {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  margin-top: 2px;
  font-size: var(--text-2xs);
  color: var(--c-text-tertiary);
}
.bell-item-type {
  font-family: var(--font-mono);
  font-size: var(--text-2xs);
  background: transparent;
  padding: 0;
}
.bell-item-sep {
  color: var(--c-border-default);
}
.bell-item-time {
  font-family: var(--font-mono);
}
</style>

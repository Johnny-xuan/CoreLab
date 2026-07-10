<script setup lang="ts">
/**
 * AllReservations — ``/me/all-reservations``.
 *
 * docs/07 §3.3 / §3.1 — the Cross-PA "All Reservations" entry.
 * Lists every reservation owned by the current user across all
 * their physical accounts. Useful when the user holds multiple
 * Linux accounts (different servers) and wants a single
 * timeline.
 *
 * Differs from PaReservations only by data source (listMy vs
 * listForPa) and by adding a "PA" column so the user can tell
 * which Linux account each row belongs to.
 */

import { computed, onMounted, ref } from 'vue';
import { NButton, NTabPane, NTabs, NTag, useDialog, useMessage } from 'naive-ui';
import { extractDetail } from '@/utils/extractDetail';
import { CalendarClock, CalendarRange, RefreshCw, X } from 'lucide-vue-next';

import AppLayout from '@/layouts/AppLayout.vue';
import CleanEmpty from '@/components/CleanEmpty.vue';
import * as resApi from '@/api/reservations';
import { useWorkspaceStore } from '@/stores/workspace';
import { formatDateTime, timeAgo } from '@/utils/timeago';

const message = useMessage();
const dialog = useDialog();
const workspace = useWorkspaceStore();

const rows = ref<resApi.ReservationRead[]>([]);
const loading = ref(false);
const tab = ref<'upcoming' | 'active' | 'history'>('upcoming');

async function refresh(): Promise<void> {
  loading.value = true;
  try {
    rows.value = await resApi.listMyReservations({});
  } catch (err) {
    message.error(extractDetail(err, '加载失败'));
  } finally {
    loading.value = false;
  }
}

onMounted(async () => {
  if (workspace.workspaces.length === 0) {
    await workspace.refresh().catch(() => undefined);
  }
  await refresh();
});

const upcoming = computed(() => rows.value.filter((r) => r.status === 'scheduled'));
const active = computed(() => rows.value.filter((r) => r.status === 'active'));
const history = computed(() =>
  rows.value.filter((r) => ['completed', 'cancelled', 'failed'].includes(r.status)),
);

function paLabel(row: resApi.ReservationRead): string {
  const w = workspace.workspaces.find((ws) => ws.link.id === row.account_link_id);
  if (w === undefined) return `link #${row.account_link_id}`;
  return `${w.pa.linux_username} @ #${w.pa.server_id}`;
}

const groupSize = computed<Map<string, number>>(() => {
  const m = new Map<string, number>();
  for (const r of rows.value) {
    if (r.group_id === null) continue;
    m.set(r.group_id, (m.get(r.group_id) ?? 0) + 1);
  }
  return m;
});
function isBundle(row: resApi.ReservationRead): boolean {
  return row.group_id !== null && (groupSize.value.get(row.group_id) ?? 0) >= 2;
}

/* ── 纯展示 helpers(不触碰任何业务逻辑/原始值) ───────────────── */

/** 捆绑预约的行数(group_id 为 null 时返回 0,仅用于显示)。 */
function bundleCount(row: resApi.ReservationRead): number {
  if (row.group_id === null) return 0;
  return groupSize.value.get(row.group_id) ?? 0;
}

/** 状态 → NTag type(与 v1 的 statusTag 映射一致)。 */
function statusTagType(status: resApi.ReservationStatus): 'info' | 'success' | 'default' | 'error' {
  return status === 'scheduled'
    ? 'info'
    : status === 'active'
      ? 'success'
      : status === 'failed'
        ? 'error'
        : 'default';
}

/** 状态 → 时间轨色条修饰类(进行中=success / 即将开始=accent / 历史=灰)。 */
function railClass(status: resApi.ReservationStatus): string {
  if (status === 'active') return 'is-active';
  if (status === 'scheduled') return 'is-upcoming';
  return 'is-history';
}

/** 人话化的时间补充语(显示一律走 timeago 工具)。 */
function timeHint(row: resApi.ReservationRead): string {
  if (row.status === 'active') return `开始于 ${timeAgo(row.start_at)}`;
  if (row.status === 'scheduled') return `创建于 ${timeAgo(row.created_at)}`;
  if (row.status === 'cancelled') return `取消于 ${timeAgo(row.cancelled_at ?? row.end_at)}`;
  return `结束于 ${timeAgo(row.end_at)}`;
}

/** GPU 显示(Mode 3 纯 cron 行 gpu_id 为 null)。 */
function gpuLabel(row: resApi.ReservationRead): string {
  return row.gpu_id === null ? 'GPU —' : `GPU #${row.gpu_id}`;
}

/** 卡片入场 stagger,封顶 ~6 个之后不再递增。 */
function enterDelay(index: number): string {
  return `${Math.min(index, 6) * 0.05}s`;
}

async function cancelBundle(row: resApi.ReservationRead): Promise<void> {
  if (row.group_id === null) return;
  const gid = row.group_id;
  dialog.warning({
    title: `取消这个 ${groupSize.value.get(gid) ?? 0} 行的捆绑预约?`,
    content: '该捆绑预约中所有仍生效的行都将被取消。',
    positiveText: '取消捆绑预约',
    negativeText: '保留',
    onPositiveClick: async () => {
      try {
        const cancelled = await resApi.cancelGroup(gid, { reason: 'user-cancel-bundle' });
        message.success(`已取消 ${cancelled.length} 行。`);
        await refresh();
      } catch (err) {
        message.error(extractDetail(err, '捆绑预约取消失败'));
      }
    },
  });
}

function cancelOne(row: resApi.ReservationRead): void {
  dialog.warning({
    title: `取消预约 #${row.id}?`,
    content: `这将立即结束该预约。`,
    positiveText: '取消预约',
    negativeText: '保留',
    onPositiveClick: async () => {
      try {
        await resApi.cancelReservation(row.id, { reason: 'user-cancel' });
        message.success('已取消。');
        await refresh();
      } catch (err) {
        message.error(extractDetail(err, '取消失败'));
      }
    },
  });
}
</script>

<template>
  <AppLayout>
    <div class="page">
      <header class="cl-pagebar cl-enter">
        <div class="cl-pagebar-icon">
          <CalendarRange :size="20" :stroke-width="1.75" />
        </div>
        <div class="cl-pagebar-body">
          <h1 class="cl-pagebar-title">
            全部预约
            <span class="cl-pagebar-meta">
              <span class="cl-num mono">{{ rows.length }}</span> 条
            </span>
          </h1>
          <p class="cl-pagebar-sub">涵盖你关联的所有 Linux 账号</p>
        </div>
        <div class="cl-pagebar-actions">
          <NButton size="small" :loading="loading" @click="refresh">
            <template #icon>
              <RefreshCw :size="13" :stroke-width="1.75" />
            </template>
            刷新
          </NButton>
        </div>
      </header>

      <div class="tabs-wrap">
        <NTabs v-model:value="tab" type="line" animated size="small">
          <NTabPane :name="'upcoming'" :tab="`即将开始 (${upcoming.length})`">
            <div v-if="upcoming.length === 0 && !loading" class="empty-wrap cl-enter">
              <CleanEmpty
                :icon="CalendarClock"
                title="暂无即将开始的预约"
                description="尚未开始的已约时段会显示在这里。"
                compact
              />
            </div>
            <div v-else class="res-list" :class="{ 'is-loading': loading }">
              <article
                v-for="(row, i) in upcoming"
                :key="row.id"
                class="res-card cl-enter cl-nudge"
                :style="{ '--cl-delay': enterDelay(i) }"
              >
                <span class="res-rail" :class="railClass(row.status)" aria-hidden="true" />
                <div class="res-main">
                  <div class="res-time">
                    <span class="res-dt mono cl-num">{{ formatDateTime(row.start_at) }}</span>
                    <span class="res-arrow" aria-hidden="true">→</span>
                    <span class="res-dt mono cl-num">{{ formatDateTime(row.end_at) }}</span>
                    <span class="res-hint">{{ timeHint(row) }}</span>
                  </div>
                  <div class="res-meta">
                    <span class="res-id mono cl-num">#{{ row.id }}</span>
                    <span
                      v-if="isBundle(row)"
                      class="bundle-chip"
                      :title="`属于一个 ${bundleCount(row)} 行的捆绑预约`"
                    >
                      ×{{ bundleCount(row) }}
                    </span>
                    <span class="res-sep" aria-hidden="true">·</span>
                    <span class="mono">{{ paLabel(row) }}</span>
                    <span class="res-sep" aria-hidden="true">·</span>
                    <span class="mono cl-num">{{ gpuLabel(row) }}</span>
                    <span class="res-sep" aria-hidden="true">·</span>
                    <NTag v-if="row.gpu_memory_mb === null" size="small" :bordered="false">
                      独占
                    </NTag>
                    <span v-else class="mono cl-num">共享 {{ row.gpu_memory_mb }} MB</span>
                  </div>
                </div>
                <div class="res-side">
                  <NTag size="small" :type="statusTagType(row.status)" :bordered="false">
                    {{ row.status }}
                  </NTag>
                  <div class="res-actions">
                    <NButton size="tiny" quaternary @click="cancelOne(row)">
                      <template #icon>
                        <X :size="12" :stroke-width="2" />
                      </template>
                      取消
                    </NButton>
                    <NButton
                      v-if="isBundle(row)"
                      size="tiny"
                      quaternary
                      title="取消该捆绑预约的所有行"
                      @click="cancelBundle(row)"
                    >
                      取消捆绑预约 (×{{ bundleCount(row) }})
                    </NButton>
                  </div>
                </div>
              </article>
            </div>
          </NTabPane>
          <NTabPane :name="'active'" :tab="`进行中 (${active.length})`">
            <div v-if="active.length === 0 && !loading" class="empty-wrap cl-enter">
              <CleanEmpty
                :icon="CalendarClock"
                title="暂无进行中的预约"
                description="当前正在某块 GPU 上运行的时段会显示在这里。"
                compact
              />
            </div>
            <div v-else class="res-list" :class="{ 'is-loading': loading }">
              <article
                v-for="(row, i) in active"
                :key="row.id"
                class="res-card cl-enter cl-nudge"
                :style="{ '--cl-delay': enterDelay(i) }"
              >
                <span class="res-rail" :class="railClass(row.status)" aria-hidden="true" />
                <div class="res-main">
                  <div class="res-time">
                    <span class="res-dot cl-pulse" aria-hidden="true" />
                    <span class="res-dt mono cl-num">{{ formatDateTime(row.start_at) }}</span>
                    <span class="res-arrow" aria-hidden="true">→</span>
                    <span class="res-dt mono cl-num">{{ formatDateTime(row.end_at) }}</span>
                    <span class="res-hint">{{ timeHint(row) }}</span>
                  </div>
                  <div class="res-meta">
                    <span class="res-id mono cl-num">#{{ row.id }}</span>
                    <span
                      v-if="isBundle(row)"
                      class="bundle-chip"
                      :title="`属于一个 ${bundleCount(row)} 行的捆绑预约`"
                    >
                      ×{{ bundleCount(row) }}
                    </span>
                    <span class="res-sep" aria-hidden="true">·</span>
                    <span class="mono">{{ paLabel(row) }}</span>
                    <span class="res-sep" aria-hidden="true">·</span>
                    <span class="mono cl-num">{{ gpuLabel(row) }}</span>
                    <span class="res-sep" aria-hidden="true">·</span>
                    <NTag v-if="row.gpu_memory_mb === null" size="small" :bordered="false">
                      独占
                    </NTag>
                    <span v-else class="mono cl-num">共享 {{ row.gpu_memory_mb }} MB</span>
                  </div>
                </div>
                <div class="res-side">
                  <NTag size="small" :type="statusTagType(row.status)" :bordered="false">
                    {{ row.status }}
                  </NTag>
                  <div class="res-actions">
                    <NButton size="tiny" quaternary @click="cancelOne(row)">
                      <template #icon>
                        <X :size="12" :stroke-width="2" />
                      </template>
                      取消
                    </NButton>
                    <NButton
                      v-if="isBundle(row)"
                      size="tiny"
                      quaternary
                      title="取消该捆绑预约的所有行"
                      @click="cancelBundle(row)"
                    >
                      取消捆绑预约 (×{{ bundleCount(row) }})
                    </NButton>
                  </div>
                </div>
              </article>
            </div>
          </NTabPane>
          <NTabPane :name="'history'" :tab="`历史 (${history.length})`">
            <div v-if="history.length === 0 && !loading" class="empty-wrap cl-enter">
              <CleanEmpty
                :icon="CalendarClock"
                title="暂无历史记录"
                description="已完成、已取消和失败的预约会归档在这里。"
                compact
              />
            </div>
            <div v-else class="res-list" :class="{ 'is-loading': loading }">
              <article
                v-for="(row, i) in history"
                :key="row.id"
                class="res-card cl-enter cl-nudge"
                :style="{ '--cl-delay': enterDelay(i) }"
              >
                <span class="res-rail" :class="railClass(row.status)" aria-hidden="true" />
                <div class="res-main">
                  <div class="res-time">
                    <span class="res-dt mono cl-num">{{ formatDateTime(row.start_at) }}</span>
                    <span class="res-arrow" aria-hidden="true">→</span>
                    <span class="res-dt mono cl-num">{{ formatDateTime(row.end_at) }}</span>
                    <span class="res-hint">{{ timeHint(row) }}</span>
                  </div>
                  <div class="res-meta">
                    <span class="res-id mono cl-num">#{{ row.id }}</span>
                    <span
                      v-if="isBundle(row)"
                      class="bundle-chip"
                      :title="`属于一个 ${bundleCount(row)} 行的捆绑预约`"
                    >
                      ×{{ bundleCount(row) }}
                    </span>
                    <span class="res-sep" aria-hidden="true">·</span>
                    <span class="mono">{{ paLabel(row) }}</span>
                    <span class="res-sep" aria-hidden="true">·</span>
                    <span class="mono cl-num">{{ gpuLabel(row) }}</span>
                    <span class="res-sep" aria-hidden="true">·</span>
                    <NTag v-if="row.gpu_memory_mb === null" size="small" :bordered="false">
                      独占
                    </NTag>
                    <span v-else class="mono cl-num">共享 {{ row.gpu_memory_mb }} MB</span>
                  </div>
                </div>
                <div class="res-side">
                  <NTag size="small" :type="statusTagType(row.status)" :bordered="false">
                    {{ row.status }}
                  </NTag>
                </div>
              </article>
            </div>
          </NTabPane>
        </NTabs>
      </div>
    </div>
  </AppLayout>
</template>

<style scoped>
.page {
  padding: var(--space-6) var(--space-8);
  max-width: 1200px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
}
.mono {
  font-family: var(--font-mono);
}

/* ── 列表行卡片(设计主角) ──────────────────────────────────── */
.res-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin-top: var(--space-3);
  transition: opacity 0.2s ease;
}
.res-list.is-loading {
  opacity: 0.6;
}
/* 首次加载、还没有任何卡片时:留一块柔和的占位 shimmer。 */
.res-list:empty {
  min-height: 96px;
  border: 1px dashed var(--c-border-subtle);
  border-radius: var(--radius-lg);
  background: linear-gradient(
    100deg,
    transparent 30%,
    color-mix(in srgb, var(--c-text-tertiary) 6%, transparent) 50%,
    transparent 70%
  );
  background-size: 200% 100%;
  animation: cl-sheen 1.6s linear infinite;
}

.res-card {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  padding: var(--space-3) var(--space-4);
  background: var(--c-bg-elevated);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-lg);
  transition:
    border-color 0.15s ease,
    background 0.15s ease;
}
.res-card:hover {
  background: color-mix(in srgb, var(--c-accent) 3%, var(--c-bg-elevated));
  border-color: color-mix(in srgb, var(--c-accent) 55%, var(--c-border-default));
}

/* 左侧 4px 时间轨色条 */
.res-rail {
  flex: none;
  align-self: stretch;
  min-height: 40px;
  width: 4px;
  border-radius: var(--radius-full);
  background: var(--c-border-default);
}
.res-rail.is-active {
  background: var(--c-success);
}
.res-rail.is-upcoming {
  background: var(--c-accent);
}
.res-rail.is-history {
  background: var(--c-border-default);
}

.res-main {
  flex: 1 1 auto;
  min-width: 0;
}
.res-time {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
  font-size: var(--text-sm);
  color: var(--c-text-primary);
}
.res-dt {
  font-size: var(--text-sm);
  letter-spacing: var(--tracking-snug);
}
.res-arrow {
  color: var(--c-text-tertiary);
  font-size: var(--text-xs);
}
.res-hint {
  margin-left: var(--space-1);
  font-size: var(--text-xs);
  color: var(--c-text-tertiary);
}
.res-dot {
  flex: none;
  width: 8px;
  height: 8px;
  border-radius: var(--radius-full);
  background: var(--c-success);
  --cl-pulse-color: color-mix(in srgb, var(--c-success) 45%, transparent);
}
.res-meta {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
  margin-top: var(--space-1);
  font-size: var(--text-xs);
  color: var(--c-text-secondary);
}
.res-id {
  color: var(--c-text-tertiary);
}
.res-sep {
  color: var(--c-text-disabled);
}
.bundle-chip {
  display: inline-flex;
  align-items: center;
  padding: 0 6px;
  height: 16px;
  border-radius: var(--radius-full);
  background: color-mix(in srgb, var(--c-accent) 18%, transparent);
  color: var(--c-accent);
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.02em;
  cursor: help;
}

.res-side {
  flex: none;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: var(--space-2);
}
.res-actions {
  display: flex;
  align-items: center;
  gap: var(--space-1);
}

/* ── 空态:细边框卡片,不悬浮虚空 ──────────────────────────── */
.empty-wrap {
  margin-top: var(--space-3);
  padding: var(--space-6) 0;
  background: var(--c-bg-elevated);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-lg);
}

/* ── 循环动效的 reduced-motion 退化:放慢变柔,不全停 ─────────── */
@media (prefers-reduced-motion: reduce) {
  .res-dot.cl-pulse {
    animation: cl-breathe 7s ease-in-out infinite;
  }
  .res-list:empty {
    animation-duration: 4.5s;
  }
  .res-card {
    transition: none;
  }
  .res-list {
    transition: none;
  }
}
</style>

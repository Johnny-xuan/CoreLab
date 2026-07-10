<script setup lang="ts">
/**
 * MyReservations — ``/me/reservations``.
 *
 * docs/07 §6.1 — three tabs (Upcoming / Active / History) of the
 * current user's reservations across all PAs. Cancel from the row
 * action button; group cancel from the group_id column tooltip.
 *
 * Visual: Vercel-style page (mono IDs / times, status chip colours
 * keyed off ``reservation.status``, Cancel button uses lucide X).
 */

import { computed, h, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import {
  NButton,
  NDataTable,
  NTabPane,
  NTabs,
  NTag,
  useDialog,
  useMessage,
  type DataTableColumns,
} from 'naive-ui';
import { extractDetail } from '@/utils/extractDetail';
import { CalendarClock, RefreshCw, X } from 'lucide-vue-next';

import AppLayout from '@/layouts/AppLayout.vue';
import CleanEmpty from '@/components/CleanEmpty.vue';
import ScriptStatusBadge from '@/components/script/ScriptStatusBadge.vue';
import { deriveScriptUIStatus } from '@/components/script/scriptHelpers';
import * as resApi from '@/api/reservations';
import { useWsStore } from '@/stores/ws';

const message = useMessage();
const dialog = useDialog();
const ws = useWsStore();

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

onMounted(refresh);

// WS push: reservation status flips and script.* notifications refresh
// the table so users see "running → completed" land live.
const unsubReservation = ws.onReservationStatusChange(() => {
  void refresh();
});
const stopNotifWatch = watch(
  () => ws.notifications.length,
  (newLen, oldLen) => {
    if (newLen <= oldLen) return;
    const latest = ws.notifications[0];
    if (latest === undefined) return;
    if (latest.type.startsWith('script.')) void refresh();
  },
);
onBeforeUnmount(() => {
  unsubReservation();
  stopNotifWatch();
});

const upcoming = computed(() => rows.value.filter((r) => r.status === 'scheduled'));
const active = computed(() => rows.value.filter((r) => r.status === 'active'));
const history = computed(() =>
  rows.value.filter((r) => ['completed', 'cancelled', 'failed'].includes(r.status)),
);

function statusTag(status: resApi.ReservationStatus) {
  const type =
    status === 'scheduled'
      ? 'info'
      : status === 'active'
        ? 'success'
        : status === 'completed'
          ? 'default'
          : status === 'cancelled'
            ? 'default'
            : 'error';
  return h(NTag, { size: 'small', type, bordered: false }, () => status);
}

function fmtDateTime(iso: string): string {
  // Geist Mono lines up tabular figures — strip locale clutter.
  const d = new Date(iso);
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  return `${y}-${m}-${day} ${hh}:${mm}`;
}

const baseColumns = computed<DataTableColumns<resApi.ReservationRead>>(() => [
  {
    title: 'ID',
    key: 'id',
    width: 72,
    render: (row) => h('span', { class: 'mono tabular' }, `#${row.id}`),
  },
  {
    title: '服务器',
    key: 'server_id',
    width: 84,
    render: (row) => h('span', { class: 'mono tabular' }, `#${row.server_id}`),
  },
  {
    title: 'GPU',
    key: 'gpu_id',
    width: 70,
    render: (row) => h('span', { class: 'mono tabular' }, `#${row.gpu_id}`),
  },
  {
    title: '开始',
    key: 'start_at',
    width: 156,
    render: (row) => h('span', { class: 'mono tabular muted nowrap' }, fmtDateTime(row.start_at)),
  },
  {
    title: '结束',
    key: 'end_at',
    width: 156,
    render: (row) => h('span', { class: 'mono tabular muted nowrap' }, fmtDateTime(row.end_at)),
  },
  {
    title: '模式',
    key: 'gpu_memory_mb',
    width: 130,
    render: (row) =>
      row.gpu_memory_mb === null
        ? h(NTag, { size: 'small', bordered: false }, () => '独占')
        : h('span', { class: 'mono tabular nowrap' }, `共享 ${row.gpu_memory_mb} MB`),
  },
  {
    title: '脚本',
    key: 'script',
    width: 110,
    render: (row) =>
      h(ScriptStatusBadge, {
        status: deriveScriptUIStatus(row),
        size: 'sm',
        showLabel: true,
      }),
  },
  { title: '状态', key: 'status', width: 110, render: (row) => statusTag(row.status) },
  {
    title: '组',
    key: 'group_id',
    width: 96,
    render: (row) =>
      row.group_id !== null
        ? h('span', { class: 'mono tabular muted' }, row.group_id.slice(0, 8))
        : '—',
  },
]);

const cancellableColumns = computed<DataTableColumns<resApi.ReservationRead>>(() => [
  ...baseColumns.value,
  {
    title: '操作',
    key: 'actions',
    width: 200,
    render: (row) =>
      h('div', { style: 'display:flex;gap:6px;justify-content:flex-end' }, [
        h(
          NButton,
          {
            size: 'tiny',
            quaternary: true,
            onClick: () => cancelOne(row),
          },
          {
            icon: () => h(X, { size: 12, 'stroke-width': 2 }),
            default: () => '取消',
          },
        ),
        row.group_id !== null
          ? h(
              NButton,
              { size: 'tiny', quaternary: true, onClick: () => cancelGroupOf(row) },
              () => '取消整组',
            )
          : null,
      ]),
  },
]);

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

function cancelGroupOf(row: resApi.ReservationRead): void {
  if (row.group_id === null) return;
  const gid = row.group_id;
  dialog.warning({
    title: `取消组 ${gid.slice(0, 8)} 中的所有行?`,
    content: '该组中所有仍生效的行都将被取消。',
    positiveText: '取消整组',
    negativeText: '保留',
    onPositiveClick: async () => {
      try {
        const cancelled = await resApi.cancelGroup(gid, { reason: 'user-cancel-group' });
        message.success(`已取消 ${cancelled.length} 行。`);
        await refresh();
      } catch (err) {
        message.error(extractDetail(err, '整组取消失败'));
      }
    },
  });
}
</script>

<template>
  <AppLayout>
    <div class="page">
      <header class="page-header">
        <div class="page-header-left">
          <h1 class="page-title">我的预约</h1>
          <span class="count-chip mono tabular">{{ rows.length }}</span>
        </div>
        <NButton size="small" :loading="loading" @click="refresh">
          <template #icon>
            <RefreshCw :size="13" :stroke-width="1.75" />
          </template>
          刷新
        </NButton>
      </header>

      <div class="tabs-wrap">
        <NTabs v-model:value="tab" type="line" animated size="small">
          <NTabPane :name="'upcoming'" :tab="`即将开始 (${upcoming.length})`">
            <div v-if="upcoming.length === 0 && !loading" class="empty-wrap">
              <CleanEmpty
                :icon="CalendarClock"
                title="暂无即将开始的预约"
                description="尚未开始的已约时段会显示在这里。"
                compact
              />
            </div>
            <div v-else class="table-wrap">
              <NDataTable
                :columns="cancellableColumns"
                :data="upcoming"
                :loading="loading"
                :bordered="false"
                :single-line="false"
                size="small"
              />
            </div>
          </NTabPane>
          <NTabPane :name="'active'" :tab="`进行中 (${active.length})`">
            <div v-if="active.length === 0 && !loading" class="empty-wrap">
              <CleanEmpty
                :icon="CalendarClock"
                title="暂无进行中的预约"
                description="当前正在某块 GPU 上运行的时段会显示在这里。"
                compact
              />
            </div>
            <div v-else class="table-wrap">
              <NDataTable
                :columns="cancellableColumns"
                :data="active"
                :loading="loading"
                :bordered="false"
                :single-line="false"
                size="small"
              />
            </div>
          </NTabPane>
          <NTabPane :name="'history'" :tab="`历史 (${history.length})`">
            <div v-if="history.length === 0 && !loading" class="empty-wrap">
              <CleanEmpty
                :icon="CalendarClock"
                title="暂无历史记录"
                description="已完成、已取消和失败的预约会归档在这里。"
                compact
              />
            </div>
            <div v-else class="table-wrap">
              <NDataTable
                :columns="baseColumns"
                :data="history"
                :loading="loading"
                :bordered="false"
                :single-line="false"
                size="small"
              />
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
  gap: var(--space-4);
}
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}
.page-header-left {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}
.page-title {
  font-size: var(--text-2xl);
  font-weight: 600;
  letter-spacing: var(--tracking-tight);
  margin: 0;
}
.count-chip {
  display: inline-flex;
  align-items: center;
  padding: 2px var(--space-2);
  height: 22px;
  font-size: var(--text-xs);
  color: var(--c-text-secondary);
  background: var(--c-bg-sunken);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-sm);
}

.tabs-wrap {
  background: var(--c-bg-elevated);
}

.table-wrap {
  background: var(--c-bg-elevated);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-lg);
  overflow: hidden;
  margin-top: var(--space-3);
}
.empty-wrap {
  background: var(--c-bg-elevated);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-lg);
  margin-top: var(--space-3);
}
</style>

<style>
/* Cross-scope: align Naive data table cells with token-driven spacing. */
.page .table-wrap .n-data-table .n-data-table-th,
.page .table-wrap .n-data-table .n-data-table-td {
  font-size: var(--text-sm);
  padding: 10px 12px;
}
.page .table-wrap .n-data-table .n-data-table-th {
  background: var(--c-bg-sunken);
  color: var(--c-text-secondary);
  font-weight: 500;
  border-bottom: 1px solid var(--c-border-subtle);
}
.page .table-wrap .n-data-table .mono {
  font-family: var(--font-mono);
}
.page .table-wrap .n-data-table .muted {
  color: var(--c-text-secondary);
}
</style>

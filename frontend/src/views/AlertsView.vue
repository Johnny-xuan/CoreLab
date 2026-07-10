<script setup lang="ts">
/**
 * AlertsView — lab-scoped alert + compliance violation list (Phase 9 P9-13,
 * docs/07 §3.3 `/lab/alerts`).
 *
 * Read uses Phase 8 `/alert-events` (P8-11). Resolve uses
 * `/alert-events/{id}/resolve` and is gated server-side (lab_admin or
 * active server_admin grant on the row's server).
 */

import { computed, h, onBeforeUnmount, onMounted, ref } from 'vue';
import { useRoute } from 'vue-router';
import {
  NAlert,
  NButton,
  NDataTable,
  NIcon,
  NInput,
  NInputNumber,
  NModal,
  NTag,
  useDialog,
  useMessage,
  type DataTableColumns,
} from 'naive-ui';
import { extractDetail } from '@/utils/extractDetail';
import { RefreshCw, Siren } from 'lucide-vue-next';

import AppLayout from '@/layouts/AppLayout.vue';
import {
  listAlerts,
  resolveAlert,
  killAlertProcess,
  type AlertEventRead,
  type AlertSeverity,
} from '@/api/alerts';
import { useWsStore } from '@/stores/ws';

const route = useRoute();
const message = useMessage();
const dialog = useDialog();
const ws = useWsStore();

/** A still-open alert that names a live process id can be killed by hand. */
function killablePid(row: AlertEventRead): number | null {
  if (row.is_resolved) return null;
  const pid = (row.payload as Record<string, unknown> | null)?.linux_pid;
  return typeof pid === 'number' ? pid : null;
}

const killingId = ref<number | null>(null);

function confirmKill(row: AlertEventRead): void {
  const pid = killablePid(row);
  if (pid === null) return;
  const who = (row.payload as Record<string, unknown> | null)?.linux_username ?? '该进程';
  dialog.warning({
    title: `终止进程 PID ${pid}?`,
    content:
      `将请求 agent 终止 ${who} 在服务器 #${row.server_id} 上的进程(SIGTERM→SIGKILL)。` +
      '需服务器已开启 gpu.kill_process 权限。此操作会记入审计。',
    positiveText: '终止进程',
    negativeText: '取消',
    onPositiveClick: async () => {
      killingId.value = row.id;
      try {
        const r = await killAlertProcess(row.id, null);
        if (r.ok && r.killed) message.success(`已终止 PID ${pid}`);
        else if (r.ok) message.warning(r.mock_warning ?? '进程可能已退出,未确认终止');
        else message.error(r.error ?? '终止失败');
      } catch (err) {
        message.error(extractDetail(err, '终止失败'));
      } finally {
        killingId.value = null;
      }
    },
  });
}

const items = ref<AlertEventRead[]>([]);
const loading = ref(false);

const sinceIso = ref('');
const serverId = ref<number | null>(null);
const limit = ref(50);

const resolveOpen = ref(false);
const resolveRow = ref<AlertEventRead | null>(null);
const resolveNote = ref('');
const resolveBusy = ref(false);

async function reload(): Promise<void> {
  loading.value = true;
  try {
    items.value = await listAlerts({
      since: sinceIso.value.trim() || null,
      server_id: serverId.value,
      limit: limit.value,
    });
  } catch (err) {
    message.error(extractDetail(err, '加载告警失败'));
  } finally {
    loading.value = false;
  }
}

function severityType(s: AlertSeverity): 'info' | 'warning' | 'error' {
  if (s === 'info') return 'info';
  if (s === 'warn') return 'warning';
  return 'error';
}

// ── Pagebar summary — pure display, drives the inline counts in the meta.
const totalCount = computed(() => items.value.length);
const resolvedCount = computed(() => items.value.filter((r) => r.is_resolved).length);
const unresolvedCount = computed(() => items.value.filter((r) => !r.is_resolved).length);
// Any unresolved row at error/critical severity escalates the tone to danger.
const hasCritical = computed(() =>
  items.value.some((r) => !r.is_resolved && (r.severity === 'error' || r.severity === 'critical')),
);
// Only the 40px icon block tints warning/danger while something is open —
// the bar itself stays neutral.
const alerting = computed(() => unresolvedCount.value > 0);
const iconToneClass = computed(() =>
  alerting.value ? (hasCritical.value ? 'icon-danger' : 'icon-warning') : '',
);

// Severity dot tint — small dot beside the severity tag in the table.
function severityDotClass(s: AlertSeverity): string {
  if (s === 'info') return 'sev-dot sev-info';
  if (s === 'warn') return 'sev-dot sev-warn';
  if (s === 'error') return 'sev-dot sev-error';
  return 'sev-dot sev-critical';
}

function openResolve(row: AlertEventRead): void {
  resolveRow.value = row;
  resolveNote.value = '';
  resolveOpen.value = true;
}

async function submitResolve(): Promise<void> {
  if (!resolveRow.value) return;
  resolveBusy.value = true;
  try {
    const updated = await resolveAlert(resolveRow.value.id, resolveNote.value.trim() || null);
    const idx = items.value.findIndex((r) => r.id === updated.id);
    if (idx >= 0) items.value[idx] = updated;
    message.success('已标记为解决');
    resolveOpen.value = false;
  } catch (err) {
    message.error(extractDetail(err, '解决失败'));
  } finally {
    resolveBusy.value = false;
  }
}

const columns = computed<DataTableColumns<AlertEventRead>>(() => [
  {
    title: '时间',
    key: 'created_at',
    width: 180,
    render: (row) => h('span', { class: 'mono tabular muted' }, row.created_at ?? '—'),
  },
  {
    title: '严重度',
    key: 'severity',
    width: 116,
    render: (row) =>
      h('span', { class: 'sev-cell' }, [
        h('span', {
          class: [severityDotClass(row.severity), row.is_resolved ? '' : 'cl-pulse'],
          'aria-hidden': 'true',
        }),
        h(
          NTag,
          { type: severityType(row.severity), size: 'small', bordered: false },
          { default: () => row.severity },
        ),
      ]),
  },
  {
    title: '事件类型',
    key: 'event_type',
    width: 240,
    render: (row) => h('code', { class: 'alert-type' }, row.event_type),
  },
  {
    title: '服务器',
    key: 'server_id',
    width: 90,
    render: (row) => h('span', { class: 'mono tabular muted' }, `#${row.server_id}`),
  },
  {
    title: 'GPU',
    key: 'gpu_id',
    width: 80,
    render: (row) =>
      h('span', { class: 'mono tabular muted' }, row.gpu_id !== null ? `#${row.gpu_id}` : '—'),
  },
  {
    title: '状态',
    key: 'is_resolved',
    width: 110,
    render: (row) =>
      row.is_resolved
        ? h(
            NTag,
            { type: 'success', size: 'small', bordered: false },
            { default: () => 'resolved' },
          )
        : h(NTag, { type: 'warning', size: 'small', bordered: false }, { default: () => 'open' }),
  },
  {
    title: '',
    key: 'action',
    width: 160,
    render: (row) => {
      if (row.is_resolved) return '—';
      const actions = [];
      if (killablePid(row) !== null) {
        actions.push(
          h(
            NButton,
            {
              size: 'tiny',
              tertiary: true,
              type: 'error',
              loading: killingId.value === row.id,
              onClick: () => confirmKill(row),
            },
            { default: () => '终止进程' },
          ),
        );
      }
      actions.push(
        h(
          NButton,
          { size: 'tiny', tertiary: true, onClick: () => openResolve(row) },
          { default: () => '解决 →' },
        ),
      );
      return h('div', { class: 'row-actions' }, actions);
    },
  },
]);

// Row decoration only — gives each table row the shared hover-nudge and a
// faint left accent on still-open alerts. Purely visual; no data touched.
function rowProps(row: AlertEventRead): Record<string, string> {
  return {
    class: `alert-row ${row.is_resolved ? 'row-resolved' : 'row-open'}`,
  };
}

let unsubscribeAlert: (() => void) | null = null;

onMounted(() => {
  const q = route.query;
  if (q.server_id) {
    const n = Number(q.server_id);
    if (!Number.isNaN(n) && n > 0) serverId.value = n;
  }
  if (typeof q.since === 'string') sinceIso.value = q.since;
  unsubscribeAlert = ws.onAlertNew(() => {
    void reload();
  });
  void reload();
});
onBeforeUnmount(() => {
  unsubscribeAlert?.();
  unsubscribeAlert = null;
});
</script>

<template>
  <AppLayout>
    <div class="page">
      <header class="cl-pagebar cl-enter">
        <div class="cl-pagebar-icon" :class="iconToneClass">
          <Siren :size="20" :stroke-width="1.75" />
        </div>
        <div class="cl-pagebar-body">
          <h1 class="cl-pagebar-title">
            Lab 告警
            <span class="cl-pagebar-meta">
              <template v-if="unresolvedCount">
                <span class="bar-count bar-open" :class="{ 'bar-danger': hasCritical }">
                  <span class="open-dot cl-pulse" aria-hidden="true" />
                  <span class="cl-num">{{ unresolvedCount }}</span> 未解决
                </span>
                <span class="meta-dot" aria-hidden="true"></span>
              </template>
              <span class="bar-count bar-resolved">
                <span class="cl-num">{{ resolvedCount }}</span> 已解决
              </span>
              <span class="meta-dot" aria-hidden="true"></span>
              <span class="bar-count"
                >共 <span class="cl-num">{{ totalCount }}</span> 条</span
              >
            </span>
          </h1>
          <p class="cl-pagebar-sub">
            汇总自 agent 触发的事件与合规违规。解决告警需要 lab_admin
            权限,或在该行对应服务器上拥有管理员授权。
          </p>
        </div>
        <div class="cl-pagebar-actions">
          <NButton size="small" :loading="loading" @click="reload">
            <template #icon>
              <NIcon><RefreshCw :size="14" :stroke-width="2" /></NIcon>
            </template>
            刷新
          </NButton>
        </div>
      </header>

      <section class="filter-bar cl-enter" style="--cl-delay: 0.06s">
        <div class="filter-group">
          <label>起始时间(ISO8601)</label>
          <NInput v-model:value="sinceIso" placeholder="2026-06-01T00:00:00Z" clearable />
        </div>
        <div class="filter-group">
          <label>服务器 id</label>
          <NInputNumber v-model:value="serverId" :min="1" placeholder="留空 = 整个 Lab" clearable />
        </div>
        <div class="filter-group">
          <label>数量上限</label>
          <NInputNumber v-model:value="limit" :min="1" :max="200" />
        </div>
        <div class="filter-actions">
          <NButton type="primary" @click="reload">
            <template #icon>
              <RefreshCw :size="14" :stroke-width="2" />
            </template>
            刷新
          </NButton>
        </div>
      </section>

      <NAlert
        v-if="!loading && items.length === 0"
        type="default"
        :show-icon="false"
        class="cl-enter"
        style="--cl-delay: 0.12s"
      >
        暂无告警。
      </NAlert>

      <div v-else class="table-wrap cl-enter" style="--cl-delay: 0.12s">
        <NDataTable
          :columns="columns"
          :data="items"
          :loading="loading"
          :bordered="false"
          size="small"
          :row-key="(row: AlertEventRead) => row.id"
          :row-props="rowProps"
        />
      </div>

      <NModal
        v-model:show="resolveOpen"
        preset="dialog"
        title="解决告警"
        :positive-text="resolveBusy ? '处理中…' : '确认解决'"
        negative-text="取消"
        :positive-button-props="{ disabled: resolveBusy }"
        @positive-click="submitResolve"
        @negative-click="resolveOpen = false"
      >
        <div v-if="resolveRow" class="resolve-body">
          <p>
            告警 <code>{{ resolveRow.event_type }}</code> #{{ resolveRow.id }}
          </p>
          <NInput
            v-model:value="resolveNote"
            type="textarea"
            placeholder="可选:简要说明处理方式(不超过 500 字)"
            :maxlength="500"
            show-count
            :autosize="{ minRows: 3, maxRows: 6 }"
          />
        </div>
      </NModal>
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

/* ── pagebar extras(共享 .cl-pagebar-meta 里的内联计数) ──────────────
   本页保留的身份信号:只有 40px 图标块按未解决最高严重度染色。 */
.cl-pagebar-icon.icon-warning {
  color: var(--c-warning);
  background: color-mix(in srgb, var(--c-warning) 10%, transparent);
  border-color: color-mix(in srgb, var(--c-warning) 28%, transparent);
}
.cl-pagebar-icon.icon-danger {
  color: var(--c-danger);
  background: color-mix(in srgb, var(--c-danger) 10%, transparent);
  border-color: color-mix(in srgb, var(--c-danger) 28%, transparent);
}
.bar-count {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  white-space: nowrap;
}
.meta-dot {
  flex: none;
  width: 3px;
  height: 3px;
  border-radius: var(--radius-full);
  background: var(--c-border-strong);
}
.bar-open {
  color: var(--c-warning);
}
.bar-open.bar-danger {
  color: var(--c-danger);
}
.open-dot {
  flex: none;
  width: 6px;
  height: 6px;
  border-radius: var(--radius-full);
  background: var(--c-warning);
  --cl-pulse-color: color-mix(in srgb, var(--c-warning) 45%, transparent);
}
.bar-danger .open-dot {
  background: var(--c-danger);
  --cl-pulse-color: color-mix(in srgb, var(--c-danger) 45%, transparent);
}
.bar-resolved {
  color: var(--c-success);
}

.filter-bar {
  margin-bottom: var(--space-4);
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: var(--space-3);
  align-items: end;
  padding: var(--space-4);
  background: var(--c-bg-elevated);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-lg);
}
.filter-group {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.filter-group label {
  font-size: var(--text-xs);
  color: var(--c-text-secondary);
  font-weight: 500;
}
.filter-actions {
  grid-column: 1 / -1;
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
}

.table-wrap {
  background: var(--c-bg-elevated);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-lg);
  overflow: hidden;
}

.alert-type {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  background: var(--c-bg-sunken);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-sm);
  padding: 1px 6px;
}

/* Severity cell — a tinted status dot ahead of the severity tag. The dot on
   still-open rows breathes (.cl-pulse, added in the render fn). */
.sev-cell {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
}
.sev-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
  display: inline-block;
}
.sev-info {
  background: var(--c-info);
  --cl-pulse-color: color-mix(in srgb, var(--c-info) 50%, transparent);
}
.sev-warn {
  background: var(--c-warning);
  --cl-pulse-color: color-mix(in srgb, var(--c-warning) 50%, transparent);
}
.sev-error {
  background: var(--c-danger);
  --cl-pulse-color: color-mix(in srgb, var(--c-danger) 50%, transparent);
}
.sev-critical {
  background: var(--c-danger);
  --cl-pulse-color: color-mix(in srgb, var(--c-danger) 55%, transparent);
}

.resolve-body {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}
</style>

<style>
.table-wrap .n-data-table .n-data-table-th,
.table-wrap .n-data-table .n-data-table-td {
  font-size: var(--text-sm);
  padding: 10px 12px;
}
.table-wrap .n-data-table .n-data-table-th {
  background: var(--c-bg-sunken);
  color: var(--c-text-secondary);
  font-weight: 500;
  border-bottom: 1px solid var(--c-border-subtle);
}
.table-wrap .n-data-table .mono {
  font-family: var(--font-mono);
}
.table-wrap .n-data-table .muted {
  color: var(--c-text-secondary);
}
/* Action cell — kill + resolve sit on one row, kill first (it's the
   weightier action) right-aligned with the resolve affordance. */
.table-wrap .n-data-table .row-actions {
  display: flex;
  gap: 6px;
  justify-content: flex-end;
  align-items: center;
}

/* Row hover-nudge + state accent. Rows slide a touch and lift their
   background on hover; still-open rows carry a faint left warning bar so
   the eye lands on what needs attention. Purely decorative. */
.table-wrap .n-data-table .alert-row td {
  transition:
    background 0.15s ease,
    transform 0.15s ease;
}
.table-wrap .n-data-table .alert-row:hover td {
  background: var(--c-bg-sunken);
  transform: translateX(3px);
}
.table-wrap .n-data-table .alert-row.row-open td:first-child {
  box-shadow: inset 2px 0 0 0 var(--c-warning);
}
.table-wrap .n-data-table .alert-row.row-resolved td {
  color: var(--c-text-secondary);
}

@media (prefers-reduced-motion: reduce) {
  .table-wrap .n-data-table .alert-row:hover td {
    transform: none;
  }
}
</style>

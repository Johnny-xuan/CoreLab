<script setup lang="ts">
/**
 * PaReservations — ``/me/accounts/:pa_id/reservations``.
 *
 * docs/07 §3.3 — the "My Reservations" entry inside the
 * "In this workspace" sidebar section. Same UI shell as
 * MyReservations.vue but scoped to one PA via
 * /me/accounts/:pa_id/reservations.
 *
 * Highlight: when navigated with ``?highlight=N`` (set by
 * PaReserve.vue after a successful POST), the matching row
 * flashes for ~1.5s so the user can see the new entry.
 *
 * 设计 v2(C 型 名册/收件):紧凑 .cl-pagebar 页头,列表行改为
 * 带状态轨的横向卡片;数据获取 / 取消 / 跳转逻辑与 v1 完全一致。
 */

import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { NButton, NModal, NTabPane, NTabs, NTag, useDialog, useMessage } from 'naive-ui';
import { extractDetail } from '@/utils/extractDetail';
import { CalendarClock, FileCode, RefreshCw, X } from 'lucide-vue-next';

import AppLayout from '@/layouts/AppLayout.vue';
import CleanEmpty from '@/components/CleanEmpty.vue';
import ScriptStatusBadge from '@/components/script/ScriptStatusBadge.vue';
import ScriptEditor, { type ScriptEditorValue } from '@/components/script/ScriptEditor.vue';
import { deriveScriptUIStatus } from '@/components/script/scriptHelpers';
import * as resApi from '@/api/reservations';
import { timeAgo, formatDateTime } from '@/utils/timeago';
import { useWorkspaceStore } from '@/stores/workspace';
import { useWsStore } from '@/stores/ws';

const route = useRoute();
const router = useRouter();
const message = useMessage();
const dialog = useDialog();
const workspace = useWorkspaceStore();
const ws = useWsStore();

const paId = computed(() => Number(route.params.pa_id));
const entry = computed(() => workspace.workspaces.find((w) => w.pa.id === paId.value) ?? null);

const rows = ref<resApi.ReservationRead[]>([]);
const loading = ref(false);
const tab = ref<'upcoming' | 'active' | 'history'>('upcoming');
const highlightId = ref<number | null>(null);

async function refresh(): Promise<void> {
  if (Number.isNaN(paId.value)) return;
  loading.value = true;
  try {
    rows.value = await resApi.listReservationsForPa(paId.value, {});
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
  // Pick up ?highlight= query param so the new row flashes.
  const raw = route.query.highlight;
  if (typeof raw === 'string') {
    const id = Number(raw);
    if (Number.isFinite(id)) {
      highlightId.value = id;
      // Auto-jump to the tab that actually contains the row.
      await nextTick();
      const row = rows.value.find((r) => r.id === id);
      if (row !== undefined) {
        if (row.status === 'scheduled') tab.value = 'upcoming';
        else if (row.status === 'active') tab.value = 'active';
        else tab.value = 'history';
      }
      // Strip the highlight param so refresh / back nav doesn't re-flash.
      setTimeout(() => {
        highlightId.value = null;
        router.replace({ query: { ...route.query, highlight: undefined } });
      }, 1800);
    }
  }
});

watch(paId, refresh);

// WS push: status flips + script.* notifications refresh the table so
// users see "running → completed" land without a manual reload.
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

/** 纯展示 — tab 面板描述(标签 / 数据 / 空态文案与 v1 一字不差)。 */
interface PaneView {
  key: 'upcoming' | 'active' | 'history';
  label: string;
  rows: resApi.ReservationRead[];
  cancellable: boolean;
  emptyTitle: string;
  emptyDesc: string;
}
const panes = computed<PaneView[]>(() => [
  {
    key: 'upcoming',
    label: `即将开始 (${upcoming.value.length})`,
    rows: upcoming.value,
    cancellable: true,
    emptyTitle: '暂无即将开始的预约',
    emptyDesc: '尚未开始的已约时段会显示在这里。',
  },
  {
    key: 'active',
    label: `进行中 (${active.value.length})`,
    rows: active.value,
    cancellable: true,
    emptyTitle: '暂无进行中的预约',
    emptyDesc: '当前正在某块 GPU 上运行的时段会显示在这里。',
  },
  {
    key: 'history',
    label: `历史 (${history.value.length})`,
    rows: history.value,
    cancellable: false,
    emptyTitle: '暂无历史记录',
    emptyDesc: '已完成、已取消和失败的预约会归档在这里。',
  },
]);

/** 状态徽章颜色映射 — 与 v1 statusTag 完全一致的语义。 */
function statusTagType(status: resApi.ReservationStatus): 'info' | 'success' | 'default' | 'error' {
  return status === 'scheduled'
    ? 'info'
    : status === 'active'
      ? 'success'
      : status === 'completed'
        ? 'default'
        : status === 'cancelled'
          ? 'default'
          : 'error';
}

// Count rows per group_id so we know which rows belong to multi-row
// bundles (Phase H — only those rows get a "bundle" indicator + a
// "Cancel bundle" action). Single-row "groups" are noise.
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

/** 纯展示 — 捆绑行数(模板里避免非空断言)。 */
function bundleCount(row: resApi.ReservationRead): number {
  if (row.group_id === null) return 0;
  return groupSize.value.get(row.group_id) ?? 0;
}

/** 纯展示 — GPU 标签(Mode 3 纯脚本任务无 GPU)。 */
function gpuLabel(row: resApi.ReservationRead): string {
  return row.gpu_id === null ? '无 GPU' : `GPU #${row.gpu_id}`;
}

/** 纯展示 — 模式标签,与 v1 列语义一致。 */
function modeLabel(row: resApi.ReservationRead): string {
  return row.gpu_memory_mb === null ? '独占' : `共享 ${row.gpu_memory_mb} MB`;
}

/** 纯展示 — 时段时长(start→end 差值,仅用于显示)。 */
function durationLabel(row: resApi.ReservationRead): string {
  const ms = new Date(row.end_at).getTime() - new Date(row.start_at).getTime();
  if (!Number.isFinite(ms) || ms <= 0) return '—';
  const mins = Math.round(ms / 60000);
  if (mins < 60) return `${mins} 分钟`;
  const hours = Math.floor(mins / 60);
  const rest = mins % 60;
  return rest === 0 ? `${hours} 小时` : `${hours} 小时 ${rest} 分钟`;
}

const paLabel = computed(() => {
  if (entry.value === null) return '—';
  return `${entry.value.pa.linux_username} @ 服务器 #${entry.value.pa.server_id}`;
});

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

async function gotoReserve(): Promise<void> {
  await router.push({ name: 'pa-reserve', params: { pa_id: paId.value } });
}

async function gotoScripts(reservationId: number): Promise<void> {
  await router.push({
    name: 'pa-scripts',
    params: { pa_id: paId.value },
    hash: `#res-${reservationId}`,
  });
}

// ── Add-a-script-after-the-fact ──────────────────────────────────────
// A reservation booked without a script can gain one while it's still
// 'scheduled' (the backend PATCH only edits scheduled rows). Reuse the
// same ScriptEditor + modifyReservation flow the Scripts page uses, so
// the entry point lives right here on the reservation list.
const editorOpen = ref(false);
const editorTarget = ref<resApi.ReservationRead | null>(null);
const editorValue = ref<ScriptEditorValue>({
  script: '',
  scriptScheduledStartAt: null,
  scriptMaxRuntimeSeconds: null,
  shareScript: false,
});
const editorSubmitting = ref(false);

function openEditor(row: resApi.ReservationRead): void {
  editorTarget.value = row;
  editorValue.value = {
    script: row.script ?? '',
    scriptScheduledStartAt: row.script_scheduled_start_at,
    scriptMaxRuntimeSeconds: row.script_max_runtime_seconds,
    shareScript: false,
  };
  editorOpen.value = true;
}

function closeEditor(): void {
  editorOpen.value = false;
  editorTarget.value = null;
}

async function submitEditor(): Promise<void> {
  if (editorTarget.value === null) return;
  editorSubmitting.value = true;
  try {
    await resApi.modifyReservation(editorTarget.value.id, {
      script: editorValue.value.script,
      script_scheduled_start_at: editorValue.value.scriptScheduledStartAt,
      script_max_runtime_seconds: editorValue.value.scriptMaxRuntimeSeconds,
    });
    message.success('脚本已添加');
    closeEditor();
    await refresh();
  } catch (err) {
    message.error(extractDetail(err, '添加失败'));
  } finally {
    editorSubmitting.value = false;
  }
}
</script>

<template>
  <AppLayout>
    <div class="page">
      <header class="cl-pagebar cl-enter">
        <div class="cl-pagebar-icon">
          <CalendarClock :size="20" :stroke-width="1.75" />
        </div>
        <div class="cl-pagebar-body">
          <h1 class="cl-pagebar-title">
            我的预约
            <span class="cl-pagebar-meta">
              <span class="meta-mono cl-num">{{ rows.length }}</span>
              <span>条</span>
              <span class="meta-sep" aria-hidden="true">·</span>
              <span class="meta-mono">{{ paLabel }}</span>
            </span>
          </h1>
          <p class="cl-pagebar-sub">该账号下的 GPU 预约时段,按状态分组。</p>
        </div>
        <div class="cl-pagebar-actions">
          <NButton size="small" type="primary" @click="gotoReserve">
            <template #icon>
              <CalendarClock :size="13" :stroke-width="1.75" />
            </template>
            新建预约
          </NButton>
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
          <NTabPane v-for="p in panes" :key="p.key" :name="p.key" :tab="p.label">
            <div v-if="p.rows.length === 0 && !loading" class="empty-wrap">
              <CleanEmpty
                :icon="CalendarClock"
                :title="p.emptyTitle"
                :description="p.emptyDesc"
                compact
              />
            </div>
            <ul v-else class="res-list" :class="{ 'is-loading': loading }">
              <li
                v-for="(r, i) in p.rows"
                :key="r.id"
                class="res-card cl-enter cl-nudge"
                :class="[`is-${r.status}`, { 'row-flash': r.id === highlightId }]"
                :style="{ '--cl-delay': `${Math.min(i * 0.04, 0.32)}s` }"
              >
                <span class="res-rail" aria-hidden="true"></span>
                <div class="res-main">
                  <div class="res-time">
                    <span
                      v-if="r.status === 'active'"
                      class="res-dot cl-pulse"
                      aria-hidden="true"
                    ></span>
                    <span class="cl-num">{{ formatDateTime(r.start_at) }}</span>
                    <span class="res-arrow" aria-hidden="true">→</span>
                    <span class="cl-num">{{ formatDateTime(r.end_at) }}</span>
                  </div>
                  <div class="res-meta">
                    <span class="meta-mono cl-num">#{{ r.id }}</span>
                    <span
                      v-if="isBundle(r)"
                      class="bundle-chip"
                      :title="`属于一个 ${bundleCount(r)} 行的捆绑预约 (group_id ${r.group_id})`"
                    >
                      ×{{ bundleCount(r) }}
                    </span>
                    <span class="meta-sep" aria-hidden="true">·</span>
                    <span class="meta-mono">{{ gpuLabel(r) }}</span>
                    <span class="meta-sep" aria-hidden="true">·</span>
                    <span>{{ modeLabel(r) }}</span>
                    <span class="meta-sep" aria-hidden="true">·</span>
                    <span>时长 {{ durationLabel(r) }}</span>
                    <span class="meta-sep" aria-hidden="true">·</span>
                    <span>创建于 {{ timeAgo(r.created_at) }}</span>
                    <template v-if="r.cancelled_at !== null">
                      <span class="meta-sep" aria-hidden="true">·</span>
                      <span>取消于 {{ timeAgo(r.cancelled_at) }}</span>
                    </template>
                  </div>
                </div>
                <div class="res-side">
                  <button
                    v-if="deriveScriptUIStatus(r) !== 'none'"
                    class="script-link"
                    type="button"
                    title="在「脚本管理」中查看 / 编辑"
                    @click.stop="gotoScripts(r.id)"
                  >
                    <ScriptStatusBadge :status="deriveScriptUIStatus(r)" size="sm" show-label />
                  </button>
                  <button
                    v-else-if="r.status === 'scheduled'"
                    class="script-link add-script"
                    type="button"
                    title="为这个预约补一个开始后自动运行的脚本"
                    @click.stop="openEditor(r)"
                  >
                    <FileCode :size="13" :stroke-width="2" />
                    添加脚本
                  </button>
                  <ScriptStatusBadge v-else status="none" size="sm" show-label />
                  <NTag size="small" :type="statusTagType(r.status)" :bordered="false">
                    {{ r.status }}
                  </NTag>
                  <template v-if="p.cancellable">
                    <NButton size="tiny" quaternary @click="cancelOne(r)">
                      <template #icon>
                        <X :size="12" :stroke-width="2" />
                      </template>
                      取消
                    </NButton>
                    <NButton
                      v-if="isBundle(r)"
                      size="tiny"
                      quaternary
                      :title="`取消该捆绑预约的全部 ${bundleCount(r)} 行`"
                      @click="cancelGroupOf(r)"
                    >
                      取消捆绑预约 (×{{ bundleCount(r) }})
                    </NButton>
                  </template>
                </div>
              </li>
            </ul>
          </NTabPane>
        </NTabs>
      </div>
    </div>

    <NModal
      v-model:show="editorOpen"
      preset="card"
      :title="editorTarget ? `添加脚本 · 预约 #${editorTarget.id}` : '添加脚本'"
      :style="{ width: '720px', maxWidth: '94vw' }"
      :mask-closable="!editorSubmitting"
    >
      <template v-if="editorTarget !== null">
        <div class="editor-meta">
          <span class="meta-mono cl-num">
            {{ formatDateTime(editorTarget.start_at) }} → {{ formatDateTime(editorTarget.end_at) }}
          </span>
          <span class="editor-hint">脚本会在预约开始后(或你指定的时间)自动运行</span>
        </div>
        <ScriptEditor
          v-model:value="editorValue"
          :window-start-at="editorTarget.start_at"
          :window-end-at="editorTarget.end_at"
          :show-share-script="false"
          embedded
        />
        <div class="editor-foot">
          <NButton size="small" :disabled="editorSubmitting" @click="closeEditor"> 取消 </NButton>
          <NButton
            size="small"
            type="primary"
            :loading="editorSubmitting"
            :disabled="editorSubmitting || editorValue.script.trim().length === 0"
            @click="submitEditor"
          >
            保存
          </NButton>
        </div>
      </template>
    </NModal>
  </AppLayout>
</template>

<style scoped>
/* ─── 页头 meta ─────────────────────────────────────────────── */
.meta-mono {
  font-family: var(--font-mono);
}
.meta-sep {
  color: var(--c-text-disabled);
}

/* ─── 列表行 = 主角:横向卡片 + 4px 状态轨 ─────────────────── */
.res-list {
  list-style: none;
  margin: var(--space-3) 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  transition: opacity 0.15s ease;
}
.res-list.is-loading {
  opacity: 0.55;
  pointer-events: none;
}

.res-card {
  position: relative;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2) var(--space-4);
  padding: var(--space-3) var(--space-4);
  padding-left: calc(var(--space-4) + 4px);
  background: var(--c-bg-elevated);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-lg);
  overflow: hidden;
}
/* hover 微高亮(cl-nudge 已带 3px 平移) */
.res-card:hover {
  border-color: var(--c-border-default);
  background: color-mix(in srgb, var(--c-accent) 2%, var(--c-bg-elevated));
}

.res-rail {
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 4px;
  background: var(--c-border-strong);
}
.res-card.is-scheduled .res-rail {
  background: var(--c-accent);
}
.res-card.is-active .res-rail {
  background: var(--c-success);
}
.res-card.is-failed .res-rail {
  background: var(--c-danger);
}
.res-card.is-completed .res-rail,
.res-card.is-cancelled .res-rail {
  background: var(--c-border-strong);
}

.res-main {
  flex: 1 1 auto;
  min-width: 240px;
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.res-time {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-2);
  font-family: var(--font-mono);
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--c-text-primary);
}
.res-arrow {
  color: var(--c-text-tertiary);
  font-weight: 400;
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
  flex-wrap: wrap;
  gap: var(--space-1) var(--space-2);
  font-size: var(--text-xs);
  color: var(--c-text-tertiary);
}

.res-side {
  flex: none;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: var(--space-2);
  margin-left: auto;
}

/* 脚本徽章点击区(原 :deep() 表格单元,现为模板内原生按钮) */
.script-link {
  display: inline-flex;
  align-items: center;
  background: transparent;
  border: 0;
  padding: 0;
  margin: 0;
  cursor: pointer;
  font: inherit;
  color: inherit;
}
.script-link:hover {
  opacity: 0.85;
}

/* "添加脚本" — a quiet dashed affordance so a script-less scheduled
   reservation reads as "you can attach one here" without competing
   with the real status badges. */
.add-script {
  gap: 4px;
  padding: 2px 8px;
  border: 1px dashed var(--c-border-default);
  border-radius: var(--radius-full);
  font-size: var(--text-xs);
  color: var(--c-text-secondary);
  transition:
    border-color 0.15s ease,
    color 0.15s ease,
    background 0.15s ease;
}
.add-script:hover {
  opacity: 1;
  border-color: var(--c-accent);
  color: var(--c-accent);
  background: color-mix(in oklab, var(--c-accent) 8%, transparent);
}

/* ── Add-script modal ─────────────────────────────────────────── */
.editor-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-2) var(--space-3);
  margin-bottom: var(--space-4);
}
.editor-hint {
  font-size: var(--text-xs);
  color: var(--c-text-tertiary);
}
.editor-foot {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
  margin-top: var(--space-4);
}

.bundle-chip {
  display: inline-flex;
  align-items: center;
  padding: 0 6px;
  height: 16px;
  border-radius: var(--radius-full);
  background: color-mix(in oklab, var(--c-accent) 18%, transparent);
  color: var(--c-accent);
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.02em;
  cursor: help;
}

/* 空态:带边框卡片包住 motif 图标 + 文案 */
.empty-wrap {
  background: var(--c-bg-elevated);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-lg);
  margin-top: var(--space-3);
}

/* ?highlight=N 新行闪烁(1.5s 余晖,与 v1 时长一致) */
@keyframes pa-row-flash {
  from {
    background-color: var(--c-accent-soft);
  }
  to {
    background-color: var(--c-bg-elevated);
  }
}
.res-card.row-flash {
  animation: pa-row-flash 1800ms ease-out;
}

@media (prefers-reduced-motion: reduce) {
  .res-card.row-flash {
    animation: none;
  }
  .res-list {
    transition: none;
  }
}
</style>

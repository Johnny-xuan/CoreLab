<script setup lang="ts">
/**
 * MyScripts — `/me/accounts/:pa_id/scripts`.
 *
 * Phase H.1 — the cron-script management page split out of My Reservations.
 * Lists every reservation in this PA that has a script attached, grouped
 * into three sections: 待跑 (status='scheduled') / 运行中 (active or
 * script_status='running') / 历史 (terminal).
 *
 * Edit lives here (PATCH /reservations/:id with the T90 fields). Logs have
 * a two-layer contract: the platform keeps a bounded recent-output tail for
 * quick inspection, while the complete file remains on the agent host.
 *
 * v2 — list archetype (C) with a terminal motif: compact `.cl-pagebar`
 * header, the script entries carry the visual weight (mono glyph, status
 * rail, timeago). Visual-only refresh; data flow untouched.
 */
import { computed, h, nextTick, onMounted, onBeforeUnmount, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { NButton, NModal, NTabPane, NTabs, useDialog, useMessage } from 'naive-ui';
import { Pencil, RefreshCw, SquareTerminal, Terminal as TerminalIcon, X } from 'lucide-vue-next';

import AppLayout from '@/layouts/AppLayout.vue';
import ScriptDetailsCard from '@/components/script/ScriptDetailsCard.vue';
import ScriptEditor, { type ScriptEditorValue } from '@/components/script/ScriptEditor.vue';
import ScriptStatusBadge from '@/components/script/ScriptStatusBadge.vue';
import {
  deriveScriptUIStatus,
  formatLocalDateTimeShort,
  type ScriptUIStatus,
} from '@/components/script/scriptHelpers';
import * as resApi from '@/api/reservations';
import { getServer, type ServerRead } from '@/api/servers';
import { extractDetail } from '@/utils/extractDetail';
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
const linuxUsername = computed(() => entry.value?.pa.linux_username ?? null);

const server = ref<ServerRead | null>(null);
const rows = ref<resApi.ReservationRead[]>([]);
const loading = ref(false);
const tab = ref<'waiting' | 'running' | 'history'>('waiting');

async function refresh(): Promise<void> {
  if (Number.isNaN(paId.value)) return;
  loading.value = true;
  try {
    rows.value = await resApi.listReservationsForPa(paId.value, {});
    if (entry.value !== null && server.value === null) {
      try {
        server.value = await getServer(entry.value.pa.server_id);
      } catch {
        server.value = null; // hostname is best-effort; SSH command will use server_id fallback
      }
    }
  } catch (err) {
    message.error(extractDetail(err, '加载预约失败'));
  } finally {
    loading.value = false;
  }
}

onMounted(async () => {
  if (workspace.workspaces.length === 0) {
    await workspace.refresh().catch(() => undefined);
  }
  await refresh();
  // Anchor scroll: ?hash=#res-<id> → scrollIntoView on that card.
  await nextTick();
  const h = route.hash;
  if (h.startsWith('#res-')) {
    const el = document.getElementById(h.slice(1));
    if (el !== null) {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' });
      el.classList.add('card-flash');
      setTimeout(() => el.classList.remove('card-flash'), 1800);
    }
  }
});
watch(paId, refresh);

// WS push: any reservation status flip OR a script.* notification for this
// user's rows re-runs the fetch. Cheap (single REST call) and avoids partial
// state drift across the three tabs.
const unsubReservation = ws.onReservationStatusChange(() => {
  void refresh();
});
const stopNotifWatch = watch(
  () => ws.notifications.length,
  (newLen, oldLen) => {
    if (newLen <= oldLen) return;
    const latest = ws.notifications[0];
    if (latest === undefined) return;
    if (latest.type.startsWith('script.') || latest.type.startsWith('reservation.')) {
      void refresh();
    }
  },
);
onBeforeUnmount(() => {
  unsubReservation();
  stopNotifWatch();
});

const withScript = computed<resApi.ReservationRead[]>(() =>
  rows.value.filter((r) => r.script !== null),
);

function uiStatusOf(r: resApi.ReservationRead): ScriptUIStatus {
  return deriveScriptUIStatus(r);
}

const waiting = computed<resApi.ReservationRead[]>(() =>
  withScript.value.filter((r) => uiStatusOf(r) === 'scheduled' && r.status === 'scheduled'),
);
const running = computed<resApi.ReservationRead[]>(() =>
  withScript.value.filter((r) => uiStatusOf(r) === 'running' || r.status === 'active'),
);
const history = computed<resApi.ReservationRead[]>(() =>
  withScript.value.filter((r) => ['completed', 'failed', 'killed'].includes(uiStatusOf(r))),
);

const hostname = computed<string | null>(() => server.value?.hostname ?? null);

// ─── edit modal ───────────────────────────────────────────────────────

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
    shareScript: false, // single-reservation edit — meaningless here
  };
  editorOpen.value = true;
}

function closeEditor(): void {
  editorOpen.value = false;
  editorTarget.value = null;
}

async function submitEdit(): Promise<void> {
  if (editorTarget.value === null) return;
  editorSubmitting.value = true;
  try {
    await resApi.modifyReservation(editorTarget.value.id, {
      script: editorValue.value.script,
      script_scheduled_start_at: editorValue.value.scriptScheduledStartAt,
      script_max_runtime_seconds: editorValue.value.scriptMaxRuntimeSeconds,
    });
    message.success('脚本已更新');
    closeEditor();
    await refresh();
  } catch (err) {
    message.error(extractDetail(err, '更新失败'));
  } finally {
    editorSubmitting.value = false;
  }
}

// ─── cancel ────────────────────────────────────────────────────────────

function confirmCancel(row: resApi.ReservationRead): void {
  const running = uiStatusOf(row) === 'running';
  dialog.warning({
    title: running ? '终止运行中的脚本?' : '取消该预约?',
    content: running
      ? '后端会向 agent 发 SIGTERM,等脚本退出后将预约置为 cancelled。'
      : '将取消整段预约;若脚本尚未启动也一并取消。',
    positiveText: running ? '终止脚本' : '取消预约',
    negativeText: '保留',
    onPositiveClick: async () => {
      try {
        await resApi.cancelReservation(row.id, {
          reason: running ? 'user-cancel-script' : 'user-cancel',
        });
        message.success(running ? '终止请求已发出' : '已取消');
        await refresh();
      } catch (err) {
        message.error(extractDetail(err, '操作失败'));
      }
    },
  });
}

// ─── clone ─────────────────────────────────────────────────────────────

async function cloneToReserve(row: resApi.ReservationRead): Promise<void> {
  // Pass the script body via query so PaReserve can prefill its
  // advanced section. Full prefill (script body in URL) is bounded by
  // the same 4KB script cap and acceptable for click-through use.
  await router.push({
    name: 'pa-reserve',
    params: { pa_id: paId.value },
    query: {
      clone_from: String(row.id),
      script: row.script ?? '',
    },
  });
}

function timeRangeText(row: resApi.ReservationRead): string {
  return `${formatLocalDateTimeShort(row.start_at)} → ${formatLocalDateTimeShort(row.end_at)}`;
}

// ─── display-only helpers (v2) ─────────────────────────────────────────

/** Relative "how fresh" line for the entry footer: running → started-at,
 * terminal → finished-at, scheduled → created-at. Pure presentation. */
function recencyOf(row: resApi.ReservationRead): { text: string; exact: string } | null {
  const ui = uiStatusOf(row);
  if (ui === 'running' && row.script_started_at !== null) {
    return {
      text: `启动于 ${timeAgo(row.script_started_at)}`,
      exact: formatDateTime(row.script_started_at),
    };
  }
  if (['completed', 'failed', 'killed'].includes(ui) && row.script_finished_at !== null) {
    return {
      text: `结束于 ${timeAgo(row.script_finished_at)}`,
      exact: formatDateTime(row.script_finished_at),
    };
  }
  if (ui === 'scheduled') {
    return { text: `创建于 ${timeAgo(row.created_at)}`, exact: formatDateTime(row.created_at) };
  }
  return null;
}

function renderCard(row: resApi.ReservationRead, idx: number) {
  const ui = uiStatusOf(row);
  const recency = recencyOf(row);
  return h(
    'article',
    {
      id: `res-${row.id}`,
      class: ['script-card', 'cl-enter', `is-${ui}`],
      style: { '--cl-delay': `${Math.min(idx, 10) * 0.045}s` },
      key: row.id,
    },
    [
      h('header', { class: 'card-head' }, [
        h('div', { class: 'card-head-left' }, [
          h('span', { class: 'term-glyph mono', 'aria-hidden': 'true' }, '>_'),
          h('span', { class: 'res-id mono' }, `#${row.id}`),
          h(
            'span',
            { class: 'res-gpu mono muted' },
            row.gpu_id === null ? '无 GPU(纯定时)' : `GPU #${row.gpu_id}`,
          ),
        ]),
        h('div', { class: 'card-head-right' }, [
          ui === 'running' ? h('span', { class: 'term-spin', 'aria-hidden': 'true' }) : null,
          h('span', { class: 'res-range mono muted', title: '预约时段' }, timeRangeText(row)),
        ]),
      ]),
      h(ScriptDetailsCard, {
        reservation: row,
        linuxUsername: linuxUsername.value,
        hostname: hostname.value,
      }),
      h('footer', { class: 'card-foot' }, [
        h('div', { class: 'foot-left' }, [
          ui === 'running'
            ? h('span', { class: 'live-dot cl-pulse', 'aria-hidden': 'true' })
            : null,
          recency !== null
            ? h('span', { class: 'foot-when mono', title: recency.exact }, recency.text)
            : null,
        ]),
        h('div', { class: 'foot-actions' }, [
          ui === 'scheduled'
            ? h(
                NButton,
                {
                  size: 'small',
                  quaternary: true,
                  onClick: () => openEditor(row),
                },
                {
                  icon: () => h(Pencil, { size: 12, 'stroke-width': 2 }),
                  default: () => '编辑脚本',
                },
              )
            : null,
          ['completed', 'failed', 'killed'].includes(ui)
            ? h(
                NButton,
                {
                  size: 'small',
                  quaternary: true,
                  onClick: () => cloneToReserve(row),
                },
                {
                  icon: () => h(TerminalIcon, { size: 12, 'stroke-width': 2 }),
                  default: () => '克隆到新预约',
                },
              )
            : null,
          row.status === 'scheduled' || row.status === 'active'
            ? h(
                NButton,
                {
                  size: 'small',
                  quaternary: true,
                  onClick: () => confirmCancel(row),
                },
                {
                  icon: () => h(X, { size: 12, 'stroke-width': 2 }),
                  default: () => (ui === 'running' ? '终止脚本' : '取消预约'),
                },
              )
            : null,
        ]),
      ]),
    ],
  );
}

const paLabel = computed(() => {
  if (entry.value === null) return '—';
  const host = hostname.value ?? `server #${entry.value.pa.server_id}`;
  return `${entry.value.pa.linux_username} @ ${host}`;
});
</script>

<template>
  <AppLayout>
    <div class="page">
      <header class="cl-pagebar cl-enter">
        <div class="cl-pagebar-icon">
          <SquareTerminal :size="20" :stroke-width="1.75" />
        </div>
        <div class="cl-pagebar-body">
          <h1 class="cl-pagebar-title">
            脚本
            <span class="cl-pagebar-meta">
              <span class="bar-count"
                ><span class="cl-num">{{ withScript.length }}</span> 个</span
              >
              <span class="meta-dot" aria-hidden="true"></span>
              <span class="bar-host">{{ paLabel }}</span>
            </span>
          </h1>
          <p class="cl-pagebar-sub">
            预约到点自动执行;平台可查看最近输出,完整日志保留在 agent 主机。
          </p>
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
          <NTabPane :name="'waiting'" :tab="`待跑 (${waiting.length})`">
            <div v-if="waiting.length === 0 && !loading" class="empty-card cl-enter">
              <div class="empty-icon">
                <TerminalIcon :size="20" :stroke-width="1.5" />
              </div>
              <p class="empty-title">还没有已配置等待执行的脚本</p>
              <p class="empty-sub">在新建预约的『高级』里配置脚本,预约开始时自动执行。</p>
            </div>
            <div v-else class="card-grid">
              <template v-for="(row, idx) in waiting" :key="row.id">
                <component :is="renderCard(row, idx)" />
              </template>
            </div>
          </NTabPane>
          <NTabPane :name="'running'" :tab="`运行中 (${running.length})`">
            <div v-if="running.length === 0 && !loading" class="empty-card cl-enter">
              <div class="empty-icon">
                <TerminalIcon :size="20" :stroke-width="1.5" />
              </div>
              <p class="empty-title">当前没有运行中的脚本</p>
              <p class="empty-sub">待跑脚本到点启动后会出现在这里,并实时显示运行时长。</p>
            </div>
            <div v-else class="card-grid">
              <template v-for="(row, idx) in running" :key="row.id">
                <component :is="renderCard(row, idx)" />
              </template>
            </div>
          </NTabPane>
          <NTabPane :name="'history'" :tab="`历史 (${history.length})`">
            <div v-if="history.length === 0 && !loading" class="empty-card cl-enter">
              <div class="empty-icon">
                <TerminalIcon :size="20" :stroke-width="1.5" />
              </div>
              <p class="empty-title">历史脚本(已完成 / 失败 / 终止)会在这里</p>
              <p class="empty-sub">跑完的脚本会保留退出码、最近输出与主机日志路径。</p>
            </div>
            <div v-else class="card-grid">
              <template v-for="(row, idx) in history" :key="row.id">
                <component :is="renderCard(row, idx)" />
              </template>
            </div>
          </NTabPane>
        </NTabs>
      </div>
    </div>

    <NModal
      v-model:show="editorOpen"
      preset="card"
      :title="editorTarget ? `编辑脚本 #${editorTarget.id}` : '编辑脚本'"
      :style="{ width: '720px', maxWidth: '94vw' }"
      :mask-closable="!editorSubmitting"
    >
      <template v-if="editorTarget !== null">
        <div class="editor-meta">
          <ScriptStatusBadge :status="uiStatusOf(editorTarget)" size="md" />
          <span class="meta-range mono">{{ timeRangeText(editorTarget) }}</span>
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
            @click="submitEdit"
          >
            保存
          </NButton>
        </div>
      </template>
    </NModal>
  </AppLayout>
</template>

<style scoped>
.page {
  max-width: 1280px;
  margin: 0 auto;
  padding: var(--space-6) var(--space-8);
  display: flex;
  flex-direction: column;
}

/* ─── pagebar extras (count + host in the shared .cl-pagebar-meta) ─── */
.bar-count {
  display: inline-flex;
  align-items: baseline;
  gap: 3px;
  white-space: nowrap;
}
.meta-dot {
  flex: none;
  width: 3px;
  height: 3px;
  border-radius: var(--radius-full);
  background: var(--c-border-strong);
}
.bar-host {
  font-family: var(--font-mono, ui-monospace, monospace);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tabs-wrap {
  background: transparent;
}

/* ─── empty state: a bordered card, not a floating void ─── */
.empty-card {
  margin-top: var(--space-3);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-1);
  padding: var(--space-10) var(--space-6);
  text-align: center;
  background: var(--c-bg-elevated);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-lg);
}
.empty-icon {
  width: 40px;
  height: 40px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--c-bg-sunken);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-md);
  color: var(--c-text-tertiary);
  margin-bottom: var(--space-2);
}
.empty-title {
  margin: 0;
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--c-text-secondary);
}
.empty-sub {
  margin: 0;
  font-size: var(--text-xs);
  color: var(--c-text-tertiary);
}

.card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(380px, 1fr));
  gap: var(--space-3);
  margin-top: var(--space-3);
}

/* ─── script entry — the protagonist. Terminal motif: mono glyph,
       status rail on the left edge, flattened details, timeago foot. ─── */
:deep(.script-card) {
  --rail: transparent;
  --glyph: var(--c-text-tertiary);
  position: relative;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-4);
  background: var(--c-bg-elevated);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-lg);
  transition:
    border-color 150ms ease,
    box-shadow 150ms ease;
  scroll-margin-top: 72px;
}
:deep(.script-card)::before {
  content: '';
  position: absolute;
  left: -1px;
  top: 10px;
  bottom: 10px;
  width: 3px;
  border-radius: var(--radius-full);
  background: var(--rail);
}
:deep(.script-card:hover) {
  border-color: var(--c-border-default);
  box-shadow: var(--shadow-sm);
}
:deep(.script-card.card-flash) {
  box-shadow: 0 0 0 2px var(--c-accent-soft, color-mix(in oklab, var(--c-accent) 30%, transparent));
}

/* status → rail + glyph tint (running=success, failed=danger, waiting=info) */
:deep(.script-card.is-scheduled) {
  --rail: var(--c-info);
  --glyph: var(--c-info);
}
:deep(.script-card.is-running) {
  --rail: var(--c-success);
  --glyph: var(--c-success);
}
:deep(.script-card.is-failed) {
  --rail: var(--c-danger);
  --glyph: var(--c-danger);
}
:deep(.script-card.is-killed) {
  --rail: var(--c-warning);
  --glyph: var(--c-warning);
}
:deep(.script-card.is-completed) {
  --rail: color-mix(in srgb, var(--c-success) 38%, transparent);
}

:deep(.script-card > .card-head) {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
}
:deep(.card-head-left) {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
}
:deep(.card-head-right) {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
}
:deep(.term-glyph) {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: -0.5px;
  color: var(--glyph);
  background: var(--c-bg-sunken);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-sm);
  user-select: none;
}
:deep(.res-id) {
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--c-text-primary);
}
:deep(.res-gpu),
:deep(.res-range) {
  font-size: 11px;
  white-space: nowrap;
}
:deep(.muted) {
  color: var(--c-text-tertiary);
}
:deep(.mono) {
  font-family: var(--font-mono, ui-monospace, monospace);
}

/* tiny working ring for running entries (cl-spin from main.css) */
:deep(.term-spin) {
  flex: none;
  width: 11px;
  height: 11px;
  border-radius: var(--radius-full);
  border: 1.5px solid color-mix(in srgb, var(--c-success) 28%, transparent);
  border-top-color: var(--c-success);
  animation: cl-spin 0.9s linear infinite;
}

/* the inner ScriptDetailsCard flattens into the entry — one card, one
   border; its mono code block reads as the terminal pane. (appearance only) */
:deep(.script-card > .card) {
  background: transparent;
  border: none;
  border-radius: 0;
  padding: 0;
}

:deep(.script-card > .card-foot) {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  padding-top: var(--space-2);
  border-top: 1px dashed var(--c-border-subtle);
}
:deep(.foot-left) {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
}
:deep(.foot-when) {
  font-size: 11px;
  color: var(--c-text-tertiary);
  white-space: nowrap;
}
:deep(.live-dot) {
  flex: none;
  width: 6px;
  height: 6px;
  border-radius: var(--radius-full);
  background: var(--c-success);
  --cl-pulse-color: color-mix(in srgb, var(--c-success) 45%, transparent);
}
:deep(.foot-actions) {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-1);
}

@media (prefers-reduced-motion: reduce) {
  :deep(.term-spin) {
    animation: none;
  }
}

.editor-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  margin-bottom: var(--space-3);
}
.meta-range {
  font-size: var(--text-xs);
  color: var(--c-text-tertiary);
}
.editor-foot {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
  margin-top: var(--space-4);
  padding-top: var(--space-3);
  border-top: 1px solid var(--c-border-subtle);
}
.mono {
  font-family: var(--font-mono, ui-monospace, monospace);
}
</style>

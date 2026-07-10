<script setup lang="ts">
/**
 * PaReserve — Phase H v0.3 entry for ``/me/accounts/:pa_id/reserve``.
 *
 * Layout (phase-h-reservation-redesign.md):
 *   • Calendar popover date picker — capped at MAX_ADVANCE_DAYS ahead
 *   • GPU column paginator (default 7 columns, ◀ N/M ▶)
 *   • Time × GPU grid (1-hour slots, 24 rows; fixed-width columns)
 *   • Bottom panel (mode + memory slider) appears when selection > 0
 *   • Submit stays on page — toast, clear selection, WS patches surfaces
 *   • 5-second undo on cancel (single + group)
 */

import { computed, onMounted, onUnmounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { NAlert, NButton, NDatePicker, NSpin, NTabPane, NTabs, useMessage } from 'naive-ui';
import { CalendarPlus, ChevronLeft, ChevronRight } from 'lucide-vue-next';
import { extractDetail } from '@/utils/extractDetail';

import AppLayout from '@/layouts/AppLayout.vue';
import ReservationGrid, { type GridColumn } from '@/components/reservation/ReservationGrid.vue';
import ReservationLegend from '@/components/reservation/ReservationLegend.vue';
import BottomPanel, {
  type BottomPanelDraft,
  type BottomPanelPayload,
} from '@/components/reservation/BottomPanel.vue';
import SmartRecommendPanel from '@/components/reservation/SmartRecommendPanel.vue';
import CronTaskPanel from '@/components/reservation/CronTaskPanel.vue';
import {
  buildGrid,
  dayBounds,
  draftToRange,
  selectionDrafts,
  selectionForcesShared,
  selectionGpuHours,
  sharedSelectionMemoryCapMb,
} from '@/components/reservation/gridUtils';
import * as resApi from '@/api/reservations';
import { getServer, listGpus, type GpuRead, type ServerRead } from '@/api/servers';
import { useAuthStore } from '@/stores/auth';
import { useWorkspaceStore } from '@/stores/workspace';
import { useWsStore } from '@/stores/ws';
import { useConflictPreview } from '@/composables/useConflictPreview';
import { useSoftCancel } from '@/composables/useSoftCancel';

const route = useRoute();
const router = useRouter();
const message = useMessage();
const auth = useAuthStore();
const ws = useWorkspaceStore();
const wsHub = useWsStore();

const paId = computed(() => Number(route.params.pa_id));
const entry = computed(() => ws.workspaces.find((w) => w.pa.id === paId.value) ?? null);

// ─── Date picker (capped at MAX_ADVANCE_DAYS ahead) ─────────────────
const MAX_ADVANCE_DAYS = 3;

function isoFromTs(ts: number): string {
  const d = new Date(ts);
  const y = d.getFullYear();
  const m = (d.getMonth() + 1).toString().padStart(2, '0');
  const dd = d.getDate().toString().padStart(2, '0');
  return `${y}-${m}-${dd}`;
}
function tsFromIso(iso: string): number {
  return new Date(`${iso}T00:00:00`).getTime();
}

const dayIso = ref(isoFromTs(Date.now()));
const dayTs = computed<number>({
  get: () => tsFromIso(dayIso.value),
  set: (v: number) => {
    dayIso.value = isoFromTs(v);
  },
});

function isDateDisabled(ts: number): boolean {
  const day = new Date(ts);
  day.setHours(0, 0, 0, 0);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const diffDays = Math.round((day.getTime() - today.getTime()) / 86400_000);
  return diffDays < 0 || diffDays >= MAX_ADVANCE_DAYS;
}

// ─── Server + GPU loading ───────────────────────────────────────────
const server = ref<ServerRead | null>(null);
const allGpus = ref<GpuRead[]>([]);
const loading = ref(false);

async function loadServerSide(): Promise<void> {
  if (entry.value === null) return;
  loading.value = true;
  try {
    server.value = await getServer(entry.value.pa.server_id);
    allGpus.value = await listGpus(entry.value.pa.server_id);
  } catch (err) {
    message.error(extractDetail(err, '加载服务器失败'));
  } finally {
    loading.value = false;
  }
}

const GPUS_PER_PAGE = 7;
const gpuPage = ref(0);
const gpuPageCount = computed(() => Math.max(1, Math.ceil(allGpus.value.length / GPUS_PER_PAGE)));
const visibleGpus = computed(() =>
  allGpus.value.slice(gpuPage.value * GPUS_PER_PAGE, (gpuPage.value + 1) * GPUS_PER_PAGE),
);
// Pad to GPUS_PER_PAGE so every page renders the same 7-wide grid —
// a single GPU on the last page stays 1/7 wide instead of stretching.
const columns = computed<GridColumn[]>(() => {
  if (server.value === null) return [];
  const out: GridColumn[] = visibleGpus.value.map((g) => ({ gpu: g, server: server.value! }));
  while (out.length < GPUS_PER_PAGE) {
    out.push({ placeholder: true, key: `ph-${out.length}` });
  }
  return out;
});
const gpuLabels = computed(() => {
  const out: Record<number, string> = {};
  for (const g of allGpus.value) out[g.id] = `GPU ${g.gpu_index}`;
  return out;
});

// ─── Reservations + selecting state ─────────────────────────────────
const reservations = ref<resApi.ReservationRead[]>([]);
const selecting = ref<Set<string>>(new Set());
const submitting = ref(false);
const {
  conflictKeys,
  lastTimeChecks,
  runPreview,
  clear: clearPreview,
} = useConflictPreview({
  selecting,
  dayIso,
  serverId: () => entry.value?.pa.server_id ?? null,
  accountLinkId: () => entry.value?.link.id ?? null,
  onError: (err) => message.error(extractDetail(err, '冲突预览失败')),
});

async function loadReservations(): Promise<void> {
  if (server.value === null) return;
  const { start } = dayBounds(dayIso.value);
  const end = new Date(start.getTime() + 86400_000);
  try {
    reservations.value = await resApi.listReservations({
      server_id: server.value.id,
      starts_after: start.toISOString(),
      ends_before: end.toISOString(),
      status_in: ['scheduled', 'active'],
    });
  } catch (err) {
    message.error(extractDetail(err, '加载预约失败'));
  }
}

// ─── Selection-derived view models ──────────────────────────────────
const gpuTotalMemoryMb = computed<Record<number, number>>(() => {
  const out: Record<number, number> = {};
  for (const g of allGpus.value) out[g.id] = g.memory_total_mb ?? 0;
  return out;
});
const currentGrid = computed(() =>
  buildGrid({
    dayIso: dayIso.value,
    gpuIds: visibleGpus.value.map((g) => g.id),
    reservations: reservations.value,
    selecting: selecting.value,
    currentUserId: auth.user?.id ?? -1,
    gpuTotalMemoryMb: gpuTotalMemoryMb.value,
  }),
);
const sharedForced = computed(() => selectionForcesShared(selecting.value, currentGrid.value));
const sharedMemoryCapMb = computed(() => {
  const cap = sharedSelectionMemoryCapMb(selecting.value, currentGrid.value);
  if (cap !== null && cap > 0) return cap;
  // Fall back to first selected gpu's total
  const firstKey = selecting.value.values().next().value;
  if (firstKey !== undefined) {
    const [g] = firstKey.split(':').map(Number);
    if (g !== undefined) return gpuTotalMemoryMb.value[g] ?? 24576;
  }
  return 24576;
});

const totalSelectedHours = computed(() => selectionGpuHours(selecting.value));

const draftRows = computed<BottomPanelDraft[]>(() => {
  return selectionDrafts(selecting.value).map((d) => {
    const r = draftToRange(dayIso.value, d);
    return {
      gpuId: d.gpuId,
      startIso: r.startIso,
      endIso: r.endIso,
      gpuLabel: gpuLabels.value[d.gpuId] ?? `GPU ${d.gpuId}`,
    };
  });
});

// ─── Submit (no redirect) ───────────────────────────────────────────
async function submit(payload: BottomPanelPayload): Promise<void> {
  if (entry.value === null) return;
  submitting.value = true;
  try {
    const items: resApi.ReservationItemInput[] = selectionDrafts(selecting.value).map((d) => {
      const r = draftToRange(dayIso.value, d);
      return {
        server_id: entry.value!.pa.server_id,
        gpu_id: d.gpuId,
        start_at: r.startIso,
        end_at: r.endIso,
        account_link_id: entry.value!.link.id,
        gpu_memory_mb: payload.mode === 'shared' ? payload.gpuMemoryMb : null,
        gpu_compute_share_pct: null,
      };
    });
    const resp = await resApi.createReservationsForPa(paId.value, {
      items,
      script: payload.script,
      script_scheduled_start_at: payload.scriptScheduledStartAt,
      script_max_runtime_seconds: payload.scriptMaxRuntimeSeconds,
      share_script: payload.shareScript,
    });
    message.success(`✓ 已约 ${resp.reservations.length} 段 GPU`);
    selecting.value = new Set();
    clearPreview();
    // WS will patch surfaces; do an immediate fetch for the user who
    // started the action (rare race where WS lands first is fine).
    await loadReservations();
  } catch (err) {
    message.error(extractDetail(err, '预约失败'));
    await loadReservations();
    await runPreview();
  } finally {
    submitting.value = false;
  }
}

// ─── Cancel with 5-second undo ──────────────────────────────────────
const { softCancel, clearAllPending } = useSoftCancel({
  cancel: (id, payload) => resApi.cancelReservation(id, payload),
  onOptimisticRemove: (id) => {
    reservations.value = reservations.value.filter((r) => r.id !== id);
  },
  onFailure: async (err) => {
    message.error(extractDetail(err, '后端取消失败'));
    await loadReservations();
  },
});

// ─── WS subscriptions: cross-page sync ──────────────────────────────
const unsubs: Array<() => void> = [];
onMounted(async () => {
  if (ws.workspaces.length === 0) await ws.refresh();
  await loadServerSide();
  await loadReservations();
  const reloadIfMatch = (payload: unknown) => {
    const sid = (payload as { server_id?: number }).server_id;
    if (sid === undefined || sid === server.value?.id) void loadReservations();
  };
  unsubs.push(
    wsHub.onReservationCreated(reloadIfMatch),
    wsHub.onReservationCancelled(reloadIfMatch),
    wsHub.onReservationTransition(reloadIfMatch),
  );
});

onUnmounted(() => {
  for (const u of unsubs) u();
  unsubs.length = 0;
  clearAllPending();
});

watch(dayIso, async () => {
  await loadReservations();
  selecting.value = new Set();
  clearPreview();
});
watch(
  () => entry.value?.pa.id,
  async () => {
    await loadServerSide();
    await loadReservations();
    selecting.value = new Set();
  },
);

// ─── Failure-mode banners ───────────────────────────────────────────
const noPa = computed(() => !ws.loading && ws.workspaces.length > 0 && entry.value === null);
const linkRevoked = computed(() => entry.value !== null && !entry.value.link.is_active);
const serverOffline = computed(() => server.value !== null && server.value.status !== 'online');

function gotoMyReservations(): void {
  void router.push({ name: 'pa-reservations', params: { pa_id: paId.value } });
}

const wouldExceedTimeLimit = computed(() => lastTimeChecks.value.some((c) => c.would_exceed));
const hasBlockingConflicts = computed(
  () => conflictKeys.value.size > 0 || wouldExceedTimeLimit.value,
);

// ─── Mode tabs: manual grid (Mode 1) / smart recommend (Mode 2) / cron (Mode 3)
// All three are flavors of "reservation" — the page is one Reserve concept
// with three modes, not three separate features. See feedback-prune-false-complexity
// for the broader principle (single entity, mode is just a field combo).
const mode = ref<'manual' | 'recommend' | 'cron'>('manual');
</script>

<template>
  <AppLayout>
    <div class="page">
      <header class="cl-pagebar cl-enter">
        <div class="cl-pagebar-icon">
          <CalendarPlus :size="20" :stroke-width="1.75" />
        </div>
        <div class="cl-pagebar-body">
          <h1 class="cl-pagebar-title">
            预约 GPU
            <span class="cl-pagebar-meta">
              <code>{{ entry?.pa.linux_username ?? '—' }}</code>
              <span class="meta-sep">@</span>
              <code>{{ server?.hostname ?? '—' }}</code>
            </span>
          </h1>
        </div>
        <div class="cl-pagebar-actions">
          <NButton size="small" tertiary @click="gotoMyReservations">我的预约</NButton>
        </div>
      </header>

      <NAlert v-if="noPa" type="warning" show-icon style="margin-bottom: var(--space-3)">
        当前账号未绑定 Linux 用户,或绑定已被吊销。
        <template #header>无可用工作区</template>
        <NButton size="small" @click="router.push({ name: 'claim-account' })">
          认领 Linux 账号
        </NButton>
      </NAlert>
      <NAlert v-else-if="linkRevoked" type="error" show-icon style="margin-bottom: var(--space-3)">
        <code>{{ entry?.pa.linux_username }}</code> 的 account_link 已吊销,无法新建预约。
        <template #header>关联已吊销</template>
      </NAlert>
      <NAlert
        v-else-if="serverOffline && mode === 'manual'"
        type="warning"
        show-icon
        style="margin-bottom: var(--space-3)"
      >
        服务器 <code>{{ server?.hostname }}</code> 当前 <strong>{{ server?.status }}</strong> ——
        agent 无心跳。可看不可约。
        <template #header>服务器离线</template>
      </NAlert>

      <div class="work-card cl-enter" style="--cl-delay: 0.06s">
        <NTabs
          v-model:value="mode"
          type="line"
          size="medium"
          class="mode-tabs"
          display-directive="if"
        >
          <NTabPane name="manual" tab="自己挑卡">
            <!-- Calendar popover date picker — capped at MAX_ADVANCE_DAYS -->
            <div class="date-row">
              <span class="date-label">日期</span>
              <NDatePicker
                v-model:value="dayTs"
                type="date"
                :is-date-disabled="isDateDisabled"
                :clearable="false"
                size="small"
                format="yyyy-MM-dd"
                style="width: 140px"
              />
              <span class="date-hint">最多约未来 {{ MAX_ADVANCE_DAYS }} 天</span>
            </div>

            <NSpin :show="loading">
              <div class="grid-area">
                <ReservationLegend />

                <!-- GPU paginator -->
                <div v-if="allGpus.length > GPUS_PER_PAGE" class="gpu-paginator">
                  <NButton
                    quaternary
                    size="tiny"
                    :disabled="gpuPage === 0"
                    @click="gpuPage = Math.max(0, gpuPage - 1)"
                  >
                    <ChevronLeft :size="12" :stroke-width="2" />
                  </NButton>
                  <span class="page-label mono">
                    {{ gpuPage + 1 }} / {{ gpuPageCount }} (GPU {{ gpuPage * GPUS_PER_PAGE }}–{{
                      Math.min(allGpus.length, (gpuPage + 1) * GPUS_PER_PAGE) - 1
                    }})
                  </span>
                  <NButton
                    quaternary
                    size="tiny"
                    :disabled="gpuPage >= gpuPageCount - 1"
                    @click="gpuPage = Math.min(gpuPageCount - 1, gpuPage + 1)"
                  >
                    <ChevronRight :size="12" :stroke-width="2" />
                  </NButton>
                </div>

                <ReservationGrid
                  v-if="server !== null && entry !== null && allGpus.length > 0"
                  :day-iso="dayIso"
                  :columns="columns"
                  :reservations="reservations"
                  :current-user-id="auth.user?.id ?? -1"
                  :selecting="selecting"
                  :conflict-keys="conflictKeys"
                  :server-offline="serverOffline"
                  @update:selecting="(v: Set<string>) => (selecting = v)"
                  @cancel-reservation="softCancel"
                />
                <NAlert
                  v-else-if="!loading && !noPa && !linkRevoked && allGpus.length === 0"
                  type="warning"
                  show-icon
                >
                  这台服务器还没注册任何 GPU。
                </NAlert>
              </div>

              <BottomPanel
                :drafts="draftRows"
                :total-gpu-hours="totalSelectedHours"
                :shared-forced="sharedForced"
                :shared-memory-cap-mb="sharedMemoryCapMb"
                :submitting="submitting"
                :has-conflicts="hasBlockingConflicts"
                @clear="
                  selecting = new Set();
                  conflictKeys = new Set();
                "
                @confirm="submit"
              />
            </NSpin>
          </NTabPane>

          <NTabPane name="recommend" tab="智能推荐">
            <SmartRecommendPanel :pa-id="paId" :account-link-id="entry?.link.id ?? null" />
          </NTabPane>

          <NTabPane name="cron" tab="Cron 定时">
            <CronTaskPanel
              :pa-id="paId"
              :server-id="entry?.pa.server_id ?? null"
              :account-link-id="entry?.link.id ?? null"
              :linux-username="entry?.pa.linux_username ?? null"
              :hostname="server?.hostname ?? null"
            />
          </NTabPane>
        </NTabs>
      </div>
    </div>
  </AppLayout>
</template>

<style scoped>
/* Page container — limit width so the whole main column (everything to
 * the right of the sidebar) reads as a centered, balanced workspace
 * rather than a wide field with elements drifting around. 1120px is the
 * sweet spot for a 7-col GPU grid (~700px wide) + breathing room. */
.page {
  padding: var(--space-6);
  max-width: 70rem;
  margin: 0 auto;
}
/* Pagebar meta — `alice @ gpu-a100-01` as mono chips. Layout / icon /
 * title typography all come from the shared .cl-pagebar primitives in
 * main.css; only the chip detail is local. */
.cl-pagebar-meta code {
  background: var(--c-bg-sunken);
  padding: 1px var(--space-2);
  border-radius: var(--radius-sm);
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  color: var(--c-text-primary);
}
.cl-pagebar-meta .meta-sep {
  color: var(--c-text-tertiary);
  opacity: 0.6;
}

/* The elevated card frames the tabbed workspace so the tab strip reads
 * as part of the panel below it instead of a thin underline floating
 * above unrelated content. The default border-subtle is nearly invisible
 * in dark mode — use border-default + a soft shadow so the card edge is
 * legible without being heavy. */
.work-card {
  background: var(--c-bg-elevated);
  border: 1px solid var(--c-border-default);
  border-radius: var(--radius-lg);
  padding: var(--space-5) var(--space-6);
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
}

.date-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-2) var(--space-3);
  margin-top: var(--space-2);
}
.date-label {
  font-size: var(--text-xs);
  color: var(--c-text-secondary);
  font-weight: 500;
}
.date-hint {
  font-size: var(--text-xs);
  color: var(--c-text-tertiary);
}

.grid-area {
  position: relative;
  display: flex;
  flex-direction: column;
  /* Inside the elevated card we re-center the grid — the card itself is
   * already a focused, narrow column, and centering inside it keeps the
   * grid symmetric with the legend / paginator above. */
  align-items: center;
  gap: var(--space-2);
}

.gpu-paginator {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-bottom: var(--space-1);
}
.page-label {
  font-size: var(--text-xs);
  color: var(--c-text-tertiary);
}

/* ── v2 纯视觉打磨(:deep,零逻辑/零 DOM 改动)─────────────────────
 * 只动子组件既有元素的观感:不新增可命中元素、不遮挡格子。
 * 格子背景色一律不碰(--c-slot-* / color-mix 原样)。 */

/* 图例色块:统一 1px 边框 + 圆角 token(原为 border-subtle + 硬编码 3px)。 */
.grid-area :deep(.legend .sw) {
  border: 1px solid var(--c-border-default);
  border-radius: var(--radius-sm);
}

/* 可点格子 hover:1px 品牌蓝描边,内缩避免压邻格。仅 CSS :hover,
 * outline 不参与布局、不拦截事件;不可点状态(他人/过期/离线)不给,
 * 以免发出错误的"可选"信号。 */
.grid-area :deep(.cell-idle:not(.cell-server-offline):hover),
.grid-area :deep(.cell-selecting:not(.cell-server-offline):hover),
.grid-area :deep(.cell-shared-remaining:not(.cell-server-offline):hover) {
  outline: 1px solid var(--c-accent);
  outline-offset: -1px;
}

/* 「现在」红线:左端加一颗 5px 圆点,锚住时间轴。
 * 装饰性伪元素,显式 pointer-events:none(父级 .now-line 本身也是)。 */
.grid-area :deep(.now-line::before) {
  content: '';
  position: absolute;
  left: -2.5px;
  top: -1.75px;
  width: 5px;
  height: 5px;
  border-radius: var(--radius-full);
  background: var(--c-danger);
  pointer-events: none;
}

/* 表头 GPU 标签:字重 500→600、主色→次级灰,与宿主机行层级拉开;
 * 占位列(.gpu-head-placeholder)保持原有的弱化样式不受影响。 */
.grid-area :deep(.gpu-head:not(.gpu-head-placeholder) .gpu-label) {
  font-weight: 600;
  color: var(--c-text-secondary);
}

/* 提交按钮区域 hover 质感:轻微上浮 + 细影,不碰禁用/加载态。 */
.work-card :deep(.panel-foot .n-button) {
  transition:
    transform 0.16s ease,
    box-shadow 0.16s ease;
}
.work-card :deep(.panel-foot .n-button:not(.n-button--disabled):hover) {
  transform: translateY(-1px);
  box-shadow: var(--shadow-sm);
}

@media (prefers-reduced-motion: reduce) {
  .work-card :deep(.panel-foot .n-button:not(.n-button--disabled):hover) {
    transform: none;
  }
}
</style>

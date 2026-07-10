<script setup lang="ts">
/**
 * GpuUsage — Phase L L-3 entity-detail page for one GPU.
 *
 * Path: `/manage/server/:server_id/gpu/:gpu_index`.
 * Visible to server_admin (own scope) and lab_admin (any).
 *
 * Three data fetches on mount (parallel):
 *   - 7d usage  (drives THIS WEEK card + USAGE BY USER)
 *   - 24h timeline (drives RIGHT NOW + TODAY + TIMELINE list)
 *   - recent scripts (drives RECENT SCRIPTS list)
 */

import { computed, h, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import {
  NAlert,
  NButton,
  NCard,
  NDataTable,
  NEmpty,
  NIcon,
  NSpin,
  type DataTableColumns,
} from 'naive-ui';
import { Activity, ArrowLeft, BarChart3, Clock, Cpu, Users } from 'lucide-vue-next';

import AppLayout from '@/layouts/AppLayout.vue';
import { listGpus, type GpuRead } from '@/api/servers';
import {
  getGpuRecentScripts,
  getGpuTimeline,
  getGpuUsage,
  type GpuScriptItem,
  type GpuTimelineItem,
  type GpuUsageByUser,
  type GpuUsageResponse,
  type GpuTimelineResponse,
  type GpuScriptsResponse,
} from '@/api/gpus';
import { extractDetail } from '@/utils/extractDetail';

const route = useRoute();
const router = useRouter();

const serverId = computed(() => Number(route.params.server_id));
const gpuIndex = computed(() => Number(route.params.gpu_index));

const gpu = ref<GpuRead | null>(null);
const usage7d = ref<GpuUsageResponse | null>(null);
const usageToday = ref<GpuUsageResponse | null>(null);
const timeline = ref<GpuTimelineResponse | null>(null);
const scripts = ref<GpuScriptsResponse | null>(null);

const loading = ref(false);
const loadError = ref<string | null>(null);

async function loadAll(): Promise<void> {
  loading.value = true;
  loadError.value = null;
  try {
    const gpus = await listGpus(serverId.value);
    const found = gpus.find((g) => g.gpu_index === gpuIndex.value);
    if (!found) {
      loadError.value = `在服务器 #${serverId.value} 上找不到索引为 ${gpuIndex.value} 的 GPU`;
      return;
    }
    gpu.value = found;

    const [u7, uToday, tl, sc] = await Promise.all([
      getGpuUsage(found.id, '7d'),
      getGpuUsage(found.id, 'today'),
      getGpuTimeline(found.id, 24),
      getGpuRecentScripts(found.id, 20).catch(() => ({ items: [] }) as GpuScriptsResponse),
    ]);
    usage7d.value = u7;
    usageToday.value = uToday;
    timeline.value = tl;
    scripts.value = sc;
  } catch (err) {
    loadError.value = extractDetail(err, 'GPU 用量加载失败');
  } finally {
    loading.value = false;
  }
}

onMounted(loadAll);
watch([serverId, gpuIndex], loadAll);

function goBack(): void {
  if (window.history.length > 1) router.back();
  else router.push({ name: 'manage-server', params: { server_id: serverId.value } });
}

function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function fmtTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleString(undefined, { hour: '2-digit', minute: '2-digit' });
}

function fmtMinutes(m: number): string {
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  const r = m % 60;
  return r === 0 ? `${h}h` : `${h}h ${r}m`;
}

// THIS WEEK total + distinct users:
const byUserMax = computed(() => {
  if (!usage7d.value?.by_user?.length) return 1;
  return Math.max(...usage7d.value.by_user.map((u) => u.hours), 1);
});

// ── Hero summary (presentational only — derived from already-fetched data).
const heroTitle = computed(() => (gpu.value ? `GPU ${gpu.value.gpu_index} 用量` : 'GPU 用量'));
const heroSubtitle = computed(() => {
  const g = gpu.value;
  if (!g) return '算力使用量与运行情况概览';
  const parts: string[] = [g.model ?? '未知型号'];
  if (g.memory_total_mb) parts.push(`${(g.memory_total_mb / 1024).toFixed(0)} GB`);
  parts.push(`服务器 #${g.server_id}`);
  return parts.join(' · ');
});
const weekHours = computed(() => usage7d.value?.total_hours ?? null);
const weekUsers = computed(() => usage7d.value?.distinct_users ?? null);
const isBusyNow = computed(() => Boolean(usage7d.value?.now));
const busyNowUser = computed(() => usage7d.value?.now?.username ?? null);

const timelineColumns = computed<DataTableColumns<GpuTimelineItem>>(() => [
  {
    title: '开始',
    key: 'start_at',
    width: 90,
    render: (row) => h('span', { class: 'mono dim' }, fmtTime(row.start_at)),
  },
  {
    title: '结束',
    key: 'end_at',
    width: 90,
    render: (row) => h('span', { class: 'mono dim' }, fmtTime(row.end_at)),
  },
  {
    title: '用户',
    key: 'username',
    render: (row) => h('span', { class: 'mono' }, row.username),
  },
  {
    title: '状态',
    key: 'status',
    width: 110,
    render: (row) =>
      h('span', { class: 'status-cell' }, [
        h('span', {
          class: row.status === 'active' ? 'dot dot-ok' : 'dot dot-mute',
        }),
        h('span', { class: 'status-text' }, row.status),
      ]),
  },
  {
    title: '预约号',
    key: 'res',
    width: 70,
    render: (row) => h('span', { class: 'mono dim' }, `#${row.reservation_id}`),
  },
]);

const scriptColumns = computed<DataTableColumns<GpuScriptItem>>(() => [
  {
    title: '预约号',
    key: 'res',
    width: 70,
    render: (row) => h('span', { class: 'mono dim' }, `#${row.reservation_id}`),
  },
  {
    title: '用户',
    key: 'username',
    width: 120,
    render: (row) => h('span', { class: 'mono' }, row.username),
  },
  {
    title: '脚本',
    key: 'script',
    render: (row) => h('span', { class: 'mono dim script-cell' }, row.script_first_line || '(空)'),
  },
  {
    title: '开始于',
    key: 'started_at',
    width: 130,
    render: (row) => h('span', { class: 'mono dim' }, fmtDateTime(row.started_at ?? row.start_at)),
  },
  {
    title: '状态',
    key: 'status',
    width: 130,
    render: (row) => {
      const st = row.status ?? 'pending';
      const cls =
        st === 'running'
          ? 'dot dot-ok'
          : st === 'completed'
            ? 'dot dot-mute'
            : st === 'failed' || st === 'killed'
              ? 'dot dot-bad'
              : 'dot dot-mute';
      const tail = row.exit_code !== null ? ` (${row.exit_code})` : '';
      return h('span', { class: 'status-cell' }, [
        h('span', { class: cls }),
        h('span', { class: 'status-text' }, `${st}${tail}`),
      ]);
    },
  },
]);

const usageRowBar = (u: GpuUsageByUser) =>
  h('div', { class: 'gpu-row' }, [
    h('span', { class: 'gpu-row-label mono' }, u.username),
    h('span', { class: 'gpu-row-hours mono' }, `${u.hours.toFixed(1)}h`),
    h('span', { class: 'gpu-row-bar' }, [
      h('span', {
        class: 'gpu-row-bar-fill',
        style: { width: `${(u.hours / byUserMax.value) * 100}%` },
      }),
    ]),
  ]);
</script>

<template>
  <AppLayout>
    <div class="console">
      <div class="topnav">
        <NButton text size="small" @click="goBack">
          <template #icon
            ><NIcon><ArrowLeft :size="14" /></NIcon
          ></template>
          返回
        </NButton>
      </div>

      <NSpin v-if="loading && !gpu" size="small" />
      <NAlert v-else-if="loadError" type="error" :title="loadError" />

      <template v-else-if="gpu">
        <!-- PAGE BAR — shared compact header (replaces the boxed hero) -->
        <header class="cl-pagebar cl-enter">
          <div class="cl-pagebar-icon" aria-hidden="true">
            <NIcon :size="20"><Activity :size="20" /></NIcon>
          </div>
          <div class="cl-pagebar-body">
            <h1 class="cl-pagebar-title">
              {{ heroTitle }}
              <span class="cl-pagebar-meta">
                <span v-if="gpu.compute_capability" class="meta-item mono"
                  >CC {{ gpu.compute_capability }}</span
                >
                <span class="meta-item" :class="isBusyNow ? 'meta-live' : 'meta-idle'">
                  <span class="meta-dot" :class="{ 'cl-pulse': isBusyNow }" />
                  <template v-if="isBusyNow">运行中 · {{ busyNowUser }}</template>
                  <template v-else>当前空闲</template>
                </span>
                <span class="meta-item">
                  <NIcon :size="11"><Cpu /></NIcon>
                  <span class="meta-num">{{
                    weekHours !== null ? weekHours.toFixed(1) : '—'
                  }}</span>
                  GPU·h（近 7 天）
                </span>
                <span class="meta-item">
                  <NIcon :size="11"><Users /></NIcon>
                  <span class="meta-num">{{ weekUsers ?? '—' }}</span> 位用户
                </span>
                <span class="meta-item">
                  <NIcon :size="11"><Clock /></NIcon>近 7 天窗口
                </span>
              </span>
            </h1>
            <p class="cl-pagebar-sub">{{ heroSubtitle }}</p>
          </div>
        </header>

        <!-- STAT ROW -->
        <div class="stat-row">
          <div class="stat-card cl-enter cl-lift" style="--cl-delay: 0.04s">
            <div class="stat-label">此刻</div>
            <template v-if="usage7d?.now">
              <div class="stat-value mono">{{ usage7d.now.username }}</div>
              <div class="stat-sub">
                <span class="status-cell">
                  <span class="dot dot-ok" />
                  <span class="status-text"
                    >运行中 · 已 {{ fmtMinutes(usage7d.now.minutes_in) }}</span
                  >
                </span>
              </div>
            </template>
            <template v-else>
              <div class="stat-value dim mono">空闲</div>
              <div class="stat-sub dim">当前无生效预约</div>
            </template>
          </div>

          <div class="stat-card cl-enter cl-lift" style="--cl-delay: 0.1s">
            <div class="stat-label">今日</div>
            <div class="stat-value mono">
              {{ usageToday ? usageToday.total_hours.toFixed(1) : '—' }}h
            </div>
            <div class="stat-sub dim">
              {{ usageToday ? `占用 ${usageToday.busy_pct.toFixed(0)}%` : '' }}
            </div>
          </div>

          <div class="stat-card cl-enter cl-lift" style="--cl-delay: 0.16s">
            <div class="stat-label">本周</div>
            <div class="stat-value mono">{{ usage7d ? usage7d.total_hours.toFixed(1) : '—' }}h</div>
            <div class="stat-sub dim">
              {{ usage7d ? `${usage7d.distinct_users} 位用户` : '' }}
            </div>
          </div>
        </div>

        <!-- TIMELINE -->
        <NCard size="small" :bordered="false" class="cool-card cl-enter" style="--cl-delay: 0.2s">
          <div class="section-title">
            时间线
            <span class="dim">(未来 24 小时)</span>
          </div>
          <NEmpty
            v-if="!timeline || timeline.items.length === 0"
            description="未来 24 小时内暂无预约"
          />
          <NDataTable
            v-else
            :columns="timelineColumns"
            :data="timeline.items"
            :row-key="(r) => r.reservation_id"
            size="small"
            :bordered="false"
          />
        </NCard>

        <!-- USAGE BY USER -->
        <NCard size="small" :bordered="false" class="cool-card cl-enter" style="--cl-delay: 0.26s">
          <div class="section-title">
            <NIcon :size="12" class="section-icon"><BarChart3 /></NIcon>
            按用户用量(近 7 天)
          </div>
          <NEmpty
            v-if="!usage7d || usage7d.by_user.length === 0"
            description="近 7 天内无 GPU 用量"
          />
          <div v-else class="gpu-rank">
            <component :is="usageRowBar" v-for="u in usage7d.by_user" :key="u.user_id" />
          </div>
        </NCard>

        <!-- RECENT SCRIPTS -->
        <NCard size="small" :bordered="false" class="cool-card cl-enter" style="--cl-delay: 0.32s">
          <div class="section-title">
            近期脚本
            <span class="dim">(最近 {{ scripts?.items.length ?? 0 }} 条)</span>
          </div>
          <NEmpty
            v-if="!scripts || scripts.items.length === 0"
            description="该 GPU 近期没有运行过脚本"
          />
          <NDataTable
            v-else
            :columns="scriptColumns"
            :data="scripts.items"
            :row-key="(r) => r.reservation_id"
            size="small"
            :bordered="false"
          />
        </NCard>
      </template>
    </div>
  </AppLayout>
</template>

<style scoped>
.console {
  padding: var(--space-5) var(--space-6);
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  color: var(--c-text-primary);
  max-width: 980px;
  margin: 0 auto; /* was missing → page hugged the left edge */
  width: 100%;
}

.topnav {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: 12px;
}

/* ── Page bar — local additions on top of the shared .cl-pagebar ──── */
/* Several meta entries follow the title, so let both lines wrap. */
.cl-pagebar-title {
  flex-wrap: wrap;
}
.cl-pagebar-meta {
  flex-wrap: wrap;
  row-gap: var(--space-1);
}
.meta-item {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  white-space: nowrap;
}
.meta-item + .meta-item {
  padding-left: var(--space-2);
  border-left: 1px solid var(--c-border-subtle);
}
.meta-num {
  font-weight: 600;
  color: var(--c-text-primary);
  font-variant-numeric: tabular-nums;
}
.meta-dot {
  width: 6px;
  height: 6px;
  border-radius: var(--radius-full);
  background: var(--c-text-disabled);
}
.meta-live {
  color: var(--c-success);
}
.meta-live .meta-dot {
  background: var(--c-success);
  --cl-pulse-color: color-mix(in srgb, var(--c-success) 50%, transparent);
}
.meta-idle .meta-dot {
  background: var(--c-text-disabled);
}

.stat-row {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--space-2);
}
.stat-card {
  background: var(--c-bg-sunken);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-md);
  padding: var(--space-3) var(--space-4);
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.stat-label {
  font-size: 11px;
  letter-spacing: 0.08em;
  color: var(--c-text-tertiary);
  font-weight: 500;
}
.stat-value {
  font-size: 22px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  line-height: 1.2;
}
.stat-sub {
  font-size: 11px;
}

.cool-card {
  background: var(--c-bg-sunken) !important;
  border: 1px solid var(--c-border-subtle) !important;
}
.cool-card :deep(.n-card__content) {
  padding: var(--space-3) var(--space-4);
}
.section-title {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: 11px;
  letter-spacing: 0.08em;
  color: var(--c-text-tertiary);
  font-weight: 600;
  margin-bottom: var(--space-3);
  text-transform: uppercase;
}
.section-icon {
  color: var(--c-accent);
}

.gpu-rank {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 12px;
}
.gpu-row {
  display: grid;
  grid-template-columns: 140px 60px 1fr;
  gap: var(--space-3);
  align-items: center;
  padding: 2px var(--space-2);
  margin: 0 calc(-1 * var(--space-2));
  border-radius: var(--radius-sm);
  transition:
    background 0.15s ease,
    transform 0.15s ease;
}
.gpu-row:hover {
  background: var(--c-bg-elevated);
  transform: translateX(3px); /* echoes the global .cl-nudge vocabulary */
}
.gpu-row-label {
  color: var(--c-text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.gpu-row-hours {
  text-align: right;
  font-variant-numeric: tabular-nums;
}
.gpu-row-bar {
  height: 8px;
  background: var(--c-border-subtle);
  border-radius: var(--radius-full);
  overflow: hidden;
  position: relative;
}
.gpu-row-bar-fill {
  display: block;
  height: 100%;
  border-radius: var(--radius-full);
  background: linear-gradient(
    90deg,
    color-mix(in srgb, var(--c-accent) 78%, transparent),
    var(--c-accent)
  );
  /* Grow into the inline-set width via scaleX, so the data value stays
     untouched. transform-origin keeps it anchored to the left edge. */
  transform-origin: left center;
  animation: bar-grow 0.7s cubic-bezier(0.22, 1, 0.36, 1) both;
}
@keyframes bar-grow {
  from {
    transform: scaleX(0);
  }
  to {
    transform: scaleX(1);
  }
}

.status-cell {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  display: inline-block;
}
.dot-ok {
  background: var(--c-success);
}
.dot-warn {
  background: var(--c-warning);
}
.dot-bad {
  background: var(--c-danger);
}
.dot-mute {
  background: var(--c-text-disabled);
}
.status-text {
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}

.mono {
  font-family: var(--font-mono);
}
.dim {
  color: var(--c-text-tertiary);
}
.script-cell {
  display: inline-block;
  max-width: 360px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* Honour reduced-motion: bars snap straight to their final width.
   (.cl-pulse on the live dot is already disabled globally.) */
@media (prefers-reduced-motion: reduce) {
  .gpu-row-bar-fill {
    animation: none;
    transform: none;
  }
  .gpu-row:hover {
    transform: none;
  }
}
</style>

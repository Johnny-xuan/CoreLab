<script setup lang="ts">
/**
 * ServerOverview — Phase L L-4 浓缩面板,嵌进 ManageServer 第一个 tab.
 *
 * 三块:status / per-GPU 24h 占用 / recent server events。所有数据并发拉,
 * 没有就空状态。气质跟 L-1 / L-3 一致 — sunken card / mono / 6px 状态点。
 */

import { computed, h, onMounted, ref, watch } from 'vue';
import { useRouter } from 'vue-router';
import { NCard, NDataTable, NEmpty, NSpin, useMessage, type DataTableColumns } from 'naive-ui';

import type { GpuRead, ServerRead } from '@/api/servers';
import { getGpuTimeline, getGpuUsage, type GpuUsageResponse } from '@/api/gpus';
import { listAuditLogs, type AuditLogRead } from '@/api/auditLogs';
import { extractDetail } from '@/utils/extractDetail';

const props = defineProps<{
  serverId: number;
  server: ServerRead | null;
  gpus: GpuRead[];
}>();

const router = useRouter();
const message = useMessage();

interface GpuRowAgg {
  gpu_id: number;
  gpu_index: number;
  total_hours_today: number;
  busy_pct: number;
  now_user: string | null;
  top_user: string | null;
  top_hours: number;
}

const gpuRows = ref<GpuRowAgg[]>([]);
const auditRows = ref<AuditLogRead[]>([]);
const loading = ref(false);

async function loadAll(): Promise<void> {
  if (!props.gpus.length) {
    gpuRows.value = [];
  }
  loading.value = true;
  try {
    const usagePromises: Promise<GpuUsageResponse | null>[] = [];
    const timelinePromises: Promise<unknown>[] = [];
    for (const g of props.gpus) {
      usagePromises.push(getGpuUsage(g.id, 'today').catch(() => null));
      timelinePromises.push(getGpuTimeline(g.id, 24).catch(() => null));
    }
    const usageResults = await Promise.all(usagePromises);
    // timelines fetched for "now_user" if usage.now is null but reservation is just starting
    await Promise.all(timelinePromises);

    gpuRows.value = props.gpus.map((g, i) => {
      const u = usageResults[i];
      const top = u?.by_user?.[0];
      return {
        gpu_id: g.id,
        gpu_index: g.gpu_index,
        total_hours_today: u?.total_hours ?? 0,
        busy_pct: u?.busy_pct ?? 0,
        now_user: u?.now?.username ?? null,
        top_user: top?.username ?? null,
        top_hours: top?.hours ?? 0,
      };
    });

    const auditResp = await listAuditLogs({
      target_server_id: props.serverId,
      page: 1,
      size: 10,
    }).catch((err) => {
      message.warning(extractDetail(err, '加载近期事件失败'));
      return { items: [], total: 0, page: 1, size: 10, total_pages: 0 };
    });
    auditRows.value = auditResp.items;
  } finally {
    loading.value = false;
  }
}

onMounted(loadAll);
watch(() => [props.serverId, props.gpus.length], loadAll);

const activeUsersCount = computed(() => {
  const names = new Set<string>();
  for (const r of gpuRows.value) {
    if (r.now_user) names.add(r.now_user);
  }
  return names.size;
});

const busyGpus = computed(() => gpuRows.value.filter((r) => r.now_user !== null).length);

const heartbeatStatus = computed(() => {
  if (!props.server) return { label: '未知', cls: 'dot-mute', sub: '' };
  const last = props.server.last_heartbeat_at;
  if (!last) return { label: '离线', cls: 'dot-bad', sub: '从未上线' };
  const ago = Math.floor((Date.now() - new Date(last).getTime()) / 1000);
  if (ago < 60) return { label: '在线', cls: 'dot-ok', sub: `${ago} 秒前心跳` };
  if (ago < 300) return { label: '在线', cls: 'dot-ok', sub: `${Math.floor(ago / 60)} 分钟前心跳` };
  // 纯展示换算:分钟数太大时人话化(>120 分钟 → N 小时前,>48 小时 → N 天前)
  const mins = Math.floor(ago / 60);
  const staleSub =
    mins > 48 * 60
      ? `${Math.floor(mins / 1440)} 天前心跳`
      : mins > 120
        ? `${Math.floor(mins / 60)} 小时前心跳`
        : `${mins} 分钟前心跳`;
  return { label: '陈旧', cls: 'dot-warn', sub: staleSub };
});

const gpuBarMax = computed(() => {
  return Math.max(...gpuRows.value.map((r) => r.total_hours_today), 1);
});

function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleString(undefined, {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

const auditColumns = computed<DataTableColumns<AuditLogRead>>(() => [
  {
    title: '时间',
    key: 'created_at',
    width: 130,
    render: (row) => h('span', { class: 'mono dim' }, fmtDateTime(row.created_at)),
  },
  {
    title: '操作',
    key: 'action',
    render: (row) => h('span', { class: 'mono' }, row.action),
  },
  {
    title: '操作者',
    key: 'actor',
    width: 140,
    render: (row) => h('span', { class: 'mono dim' }, row.actor?.username ?? '—'),
  },
  {
    title: '结果',
    key: 'result',
    width: 80,
    render: (row) =>
      h('span', { class: 'status-cell' }, [
        h('span', {
          class:
            row.result === 'ok'
              ? 'dot dot-ok'
              : row.result === 'denied'
                ? 'dot dot-warn'
                : 'dot dot-bad',
        }),
        h('span', { class: 'status-text' }, row.result),
      ]),
  },
]);

function openGpu(gpu_index: number): void {
  router.push({
    name: 'manage-server-gpu',
    params: { server_id: props.serverId, gpu_index },
  });
}
</script>

<template>
  <div class="overview">
    <NSpin v-if="loading && gpuRows.length === 0" size="small" />

    <!-- 3 STAT CARDS -->
    <div class="stat-row">
      <div class="stat-card">
        <div class="stat-label">状态</div>
        <div class="stat-value">
          <span class="status-cell">
            <span :class="['dot', heartbeatStatus.cls]" />
            <span class="status-text" style="font-size: 16px">{{ heartbeatStatus.label }}</span>
          </span>
        </div>
        <div class="stat-sub dim">{{ heartbeatStatus.sub }}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">活跃用户</div>
        <div class="stat-value mono">{{ activeUsersCount }}</div>
        <div class="stat-sub dim">正在运行</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">GPU 占用</div>
        <div class="stat-value mono">{{ busyGpus }} / {{ gpus.length }}</div>
        <div class="stat-sub dim">当前在用</div>
      </div>
    </div>

    <!-- PER-GPU 24h -->
    <NCard size="small" :bordered="false" class="cool-card">
      <div class="section-title">GPU 用量(今日)</div>
      <NEmpty v-if="!gpuRows.length" description="未注册任何 GPU" />
      <div v-else class="gpu-rank">
        <div
          v-for="row in gpuRows"
          :key="row.gpu_id"
          class="gpu-row"
          @click="openGpu(row.gpu_index)"
        >
          <span class="gpu-row-label mono">GPU {{ row.gpu_index }}</span>
          <span class="gpu-row-hours mono">{{ row.total_hours_today.toFixed(1) }}h</span>
          <span class="gpu-row-bar">
            <span
              class="gpu-row-bar-fill"
              :style="{ width: `${(row.total_hours_today / gpuBarMax) * 100}%` }"
            />
          </span>
          <span class="gpu-row-now mono dim">
            <template v-if="row.now_user">
              <span class="status-cell">
                <span class="dot dot-ok" />
                <span class="status-text">{{ row.now_user }} 使用中</span>
              </span>
            </template>
            <template v-else-if="row.top_user"> 最多:{{ row.top_user }} </template>
            <template v-else>空闲</template>
          </span>
        </div>
      </div>
    </NCard>

    <!-- RECENT EVENTS -->
    <NCard size="small" :bordered="false" class="cool-card">
      <div class="section-title">近期事件(本服务器)</div>
      <NEmpty v-if="!auditRows.length" description="审计日志中没有本服务器的事件" />
      <NDataTable
        v-else
        :columns="auditColumns"
        :data="auditRows"
        :row-key="(r) => r.id"
        size="small"
        :bordered="false"
      />
    </NCard>
  </div>
</template>

<style scoped>
.overview {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
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
  font-size: 11px;
  letter-spacing: 0.08em;
  color: var(--c-text-tertiary);
  font-weight: 600;
  margin-bottom: var(--space-3);
  text-transform: uppercase;
}

.gpu-rank {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 12px;
}
.gpu-row {
  display: grid;
  grid-template-columns: 70px 60px 1fr 180px;
  gap: var(--space-3);
  align-items: center;
  cursor: pointer;
  padding: 2px 4px;
  border-radius: 4px;
}
.gpu-row:hover {
  background: var(--c-border-subtle);
}
.gpu-row-hours {
  text-align: right;
  font-variant-numeric: tabular-nums;
}
.gpu-row-bar {
  height: 8px;
  background: var(--c-border-subtle);
  border-radius: 2px;
  overflow: hidden;
  position: relative;
}
.gpu-row-bar-fill {
  display: block;
  height: 100%;
  background: var(--c-accent);
}
.gpu-row-now {
  text-align: right;
  font-size: 11px;
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
</style>

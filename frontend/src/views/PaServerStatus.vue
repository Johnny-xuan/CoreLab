<script setup lang="ts">
/**
 * PaServerStatus — ``/me/accounts/:pa_id/server`` — Phase H rich view.
 *
 * docs/07 §3.3 / §4 — "Server Status" entry inside the "In this
 * workspace" sidebar section. Surfaces the server hosting this PA so
 * the user can see GPU live utilization + their own running processes
 * (filtered by the PA's linux_username) — the answer to "is my script
 * actually running right now?".
 *
 * Live data:
 *   • 5 s polling for GPUs + server status (same cadence as ServerDetail).
 *   • Polls only while tab is visible (visibility-aware).
 */

import { computed, onMounted, onUnmounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { NButton, NSpin, NTag } from 'naive-ui';
import { Cpu, ExternalLink, HeartPulse, RefreshCw, ServerCog, Terminal } from 'lucide-vue-next';

import AppLayout from '@/layouts/AppLayout.vue';
import CleanEmpty from '@/components/CleanEmpty.vue';
import { useWorkspaceStore } from '@/stores/workspace';
import { getServer, listGpus, type GpuRead, type ServerRead } from '@/api/servers';

const route = useRoute();
const router = useRouter();
const workspace = useWorkspaceStore();

const paId = computed(() => Number(route.params.pa_id));
const entry = computed(() => workspace.workspaces.find((w) => w.pa.id === paId.value) ?? null);

const server = ref<ServerRead | null>(null);
const gpus = ref<GpuRead[]>([]);
const loading = ref(false);
const refreshing = ref(false);
let pollTimer: ReturnType<typeof setInterval> | null = null;

async function refresh(silent = false): Promise<void> {
  if (entry.value === null) return;
  if (!silent) loading.value = true;
  refreshing.value = true;
  try {
    server.value = await getServer(entry.value.pa.server_id);
    gpus.value = await listGpus(entry.value.pa.server_id);
  } catch {
    // silent
  } finally {
    loading.value = false;
    refreshing.value = false;
  }
}

function startPoll(): void {
  if (pollTimer !== null) return;
  pollTimer = setInterval(() => {
    if (document.visibilityState === 'visible') void refresh(true);
  }, 5_000);
}
function stopPoll(): void {
  if (pollTimer !== null) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

// 刷新过一次之后才允许判定"工作区不存在"——瞬时失败不该让页面永远空白。
const wsReady = ref(false);

onMounted(async () => {
  if (workspace.workspaces.length === 0) {
    await workspace.refresh().catch(() => undefined);
  }
  wsReady.value = true;
  await refresh();
  startPoll();
});

onUnmounted(() => {
  stopPoll();
});

watch(
  () => entry.value?.pa.id,
  async () => {
    await refresh();
  },
);

const isOnline = computed(() => server.value?.status === 'online');
const serverChipType = computed<'default' | 'success' | 'error' | 'warning'>(() => {
  if (server.value === null) return 'default';
  if (server.value.status === 'online') return 'success';
  if (server.value.status === 'pending') return 'warning';
  return 'error';
});

interface MyProcess {
  gpuIndex: number;
  gpuId: number;
  pid: number;
  memMb: number;
}
const myProcesses = computed<MyProcess[]>(() => {
  const username = entry.value?.pa.linux_username;
  if (!username) return [];
  const out: MyProcess[] = [];
  for (const g of gpus.value) {
    const snap = g.process_snapshot ?? [];
    for (const p of snap) {
      if (p.linux_username === username) {
        out.push({
          gpuIndex: g.gpu_index,
          gpuId: g.id,
          pid: p.pid,
          memMb: p.memory_mb,
        });
      }
    }
  }
  return out.sort((a, b) => a.gpuIndex - b.gpuIndex || a.pid - b.pid);
});

function memPct(g: GpuRead): number {
  if (g.memory_total_mb === null || g.memory_total_mb === 0) return 0;
  return Math.min(100, Math.round(((g.memory_used_mb ?? 0) / g.memory_total_mb) * 100));
}
function utilTone(g: GpuRead): string {
  const u = g.util_pct ?? 0;
  if (u > 90) return 'tone-danger';
  if (u > 60) return 'tone-warn';
  return 'tone-info';
}

async function gotoServerDetail(): Promise<void> {
  if (server.value === null) return;
  await router.push({ name: 'server-detail', params: { id: server.value.id } });
}

const updatedNow = computed(() => {
  let latest = '—';
  for (const g of gpus.value) {
    const t = g.last_updated_at;
    if (t !== null && (latest === '—' || t > latest)) latest = t;
  }
  return latest === '—' ? '—' : latest.slice(11, 19);
});

// ── Display-only helpers (pagebar meta / GPU status dots) ────────────
// Pure presentation derived from already-fetched state. No new fetches.

/** A GPU counts as "busy" if it reports utilization or hosts processes. */
function gpuBusy(g: GpuRead): boolean {
  return (g.util_pct ?? 0) > 0 || (g.process_snapshot?.length ?? 0) > 0;
}

const busyGpuCount = computed(() => gpus.value.filter((g) => gpuBusy(g)).length);
const idleGpuCount = computed(() => gpus.value.length - busyGpuCount.value);

const avgUtil = computed<number | null>(() => {
  if (gpus.value.length === 0) return null;
  let sum = 0;
  for (const g of gpus.value) sum += g.util_pct ?? 0;
  return Math.round(sum / gpus.value.length);
});

const totalMemPct = computed<number | null>(() => {
  let used = 0;
  let total = 0;
  for (const g of gpus.value) {
    used += g.memory_used_mb ?? 0;
    total += g.memory_total_mb ?? 0;
  }
  if (total === 0) return null;
  return Math.min(100, Math.round((used / total) * 100));
});
</script>

<template>
  <AppLayout>
    <div class="page">
      <NSpin v-if="entry === null && !wsReady" />
      <div v-else-if="entry === null" class="ws-missing cl-enter">
        <h2>未找到这个工作区</h2>
        <p>该 Linux 账号可能尚未关联到你的用户、已被解除,或链接里的编号已过期 (PA #{{ paId }})。</p>
        <div class="ws-missing-actions">
          <NButton type="primary" @click="router.push({ name: 'claim-account' })">
            关联 Linux 账号
          </NButton>
          <NButton quaternary @click="router.push({ name: 'dashboard' })">回到仪表盘</NButton>
        </div>
      </div>
      <template v-else>
        <!-- ── 紧凑页头(共享 .cl-pagebar)──────────────────────────── -->
        <header class="cl-pagebar cl-enter">
          <div class="cl-pagebar-icon" :class="{ online: isOnline }">
            <HeartPulse :size="20" :stroke-width="1.75" />
            <span v-if="isOnline" class="status-dot cl-pulse" aria-hidden="true" />
          </div>
          <div class="cl-pagebar-body">
            <h1 class="cl-pagebar-title">
              服务器状态
              <span v-if="server" class="server-chip mono">{{ server.hostname }}</span>
              <NTag :type="serverChipType" size="small" :bordered="false">
                {{ server ? server.status : '—' }}
              </NTag>
              <span class="cl-pagebar-meta">
                <span
                  ><span class="meta-num cl-num">{{ gpus.length }}</span> 块 GPU</span
                >
                <template v-if="busyGpuCount > 0">
                  <span class="meta-sep" aria-hidden="true">·</span>
                  <span class="meta-ok">
                    <span class="meta-dot cl-pulse" aria-hidden="true" />
                    <span class="meta-num cl-num">{{ busyGpuCount }}</span> 块运行中
                  </span>
                </template>
                <template v-if="idleGpuCount > 0">
                  <span class="meta-sep" aria-hidden="true">·</span>
                  <span
                    ><span class="meta-num cl-num">{{ idleGpuCount }}</span> 块空闲</span
                  >
                </template>
                <template v-if="avgUtil !== null">
                  <span class="meta-sep" aria-hidden="true">·</span>
                  <span
                    >平均利用率 <span class="meta-num cl-num">{{ avgUtil }}%</span></span
                  >
                </template>
                <template v-if="totalMemPct !== null">
                  <span class="meta-sep" aria-hidden="true">·</span>
                  <span
                    >显存占用 <span class="meta-num cl-num">{{ totalMemPct }}%</span></span
                  >
                </template>
                <span class="meta-sep" aria-hidden="true">·</span>
                <span
                  ><span class="meta-num cl-num">{{ myProcesses.length }}</span> 个我的进程</span
                >
              </span>
            </h1>
            <p class="cl-pagebar-sub">
              这台服务器的实时心跳 —— GPU 占用与你的进程,每 5 秒自动刷新
              <span class="muted-stamp mono tabular">
                · 更新于 {{ updatedNow }}<span v-if="refreshing"> · 实时</span>
              </span>
            </p>
          </div>
          <div class="cl-pagebar-actions">
            <NButton size="small" :disabled="server === null" @click="gotoServerDetail">
              <template #icon>
                <ExternalLink :size="13" :stroke-width="1.75" />
              </template>
              打开服务器详情
            </NButton>
            <NButton size="small" :loading="loading" @click="refresh(false)">
              <template #icon>
                <RefreshCw :size="13" :stroke-width="1.75" />
              </template>
              刷新
            </NButton>
          </div>
        </header>

        <NSpin :show="loading">
          <div v-if="server !== null" class="content">
            <!-- ── GPU live cards ───────────────────────────────────────── -->
            <section class="block cl-enter" style="--cl-delay: 0.08s">
              <header class="block-head">
                <Cpu :size="14" :stroke-width="1.75" />
                <span class="block-title">GPU ({{ gpus.length }})</span>
                <span v-if="!isOnline" class="block-warn">agent 离线 — 数值可能已过期</span>
              </header>
              <div v-if="gpus.length === 0" class="empty-wrap">
                <CleanEmpty
                  :icon="Cpu"
                  title="尚未上报任何 GPU"
                  description="agent 采集到第一份 nvidia-smi 样本后,会填充这个列表。"
                  compact
                />
              </div>
              <div v-else class="gpu-grid">
                <article
                  v-for="(g, i) in gpus"
                  :key="g.id"
                  class="gpu-card cl-enter cl-lift"
                  :style="{ '--cl-delay': `${0.12 + i * 0.05}s` }"
                >
                  <header class="gpu-card-head">
                    <div class="gpu-id">
                      <span class="gpu-index mono tabular">#{{ g.gpu_index }}</span>
                      <span class="gpu-model">{{ g.model ?? '未知 GPU' }}</span>
                    </div>
                    <span class="gpu-state" :class="gpuBusy(g) ? 'gpu-state-on' : 'gpu-state-off'">
                      <span class="gpu-dot" :class="{ 'cl-pulse': gpuBusy(g) }" />
                      {{ gpuBusy(g) ? '运行中' : '空闲' }}
                    </span>
                  </header>
                  <div class="stat-strip">
                    <div class="stat">
                      <span class="stat-label">利用率</span>
                      <span :class="['stat-value', 'mono', 'tabular', utilTone(g)]">
                        {{ g.util_pct ?? '—' }}<span class="stat-unit">%</span>
                      </span>
                    </div>
                    <div class="stat">
                      <span class="stat-label">显存</span>
                      <span class="stat-value mono tabular">
                        {{ g.memory_used_mb ?? '—' }}
                        <span class="stat-unit">/ {{ g.memory_total_mb ?? '—' }} MB</span>
                      </span>
                    </div>
                    <div class="stat">
                      <span class="stat-label">温度</span>
                      <span class="stat-value mono tabular">
                        {{ g.temperature_c ?? '—' }}<span class="stat-unit">°C</span>
                      </span>
                    </div>
                  </div>
                  <div
                    class="mem-bar"
                    role="progressbar"
                    :aria-valuenow="memPct(g)"
                    aria-valuemin="0"
                    aria-valuemax="100"
                  >
                    <div class="mem-bar-fill" :style="{ width: memPct(g) + '%' }" />
                  </div>
                </article>
              </div>
            </section>

            <!-- ── My processes ────────────────────────────────────────── -->
            <section class="block cl-enter" style="--cl-delay: 0.16s">
              <header class="block-head">
                <Terminal :size="14" :stroke-width="1.75" />
                <span class="block-title"> 我的进程 ({{ myProcesses.length }}) </span>
                <span v-if="entry" class="block-subtle mono"
                  >身份 {{ entry.pa.linux_username }}</span
                >
              </header>
              <div v-if="myProcesses.length === 0" class="empty-wrap">
                <CleanEmpty
                  :icon="Terminal"
                  title="没有正在跑的进程"
                  description="一旦 agent 报告这个 Linux 账号下有 GPU 进程,这里会出现 PID + 显存占用。"
                  compact
                />
              </div>
              <table v-else class="proc-table">
                <thead>
                  <tr>
                    <th>GPU</th>
                    <th>PID</th>
                    <th>显存 (MB)</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="p in myProcesses" :key="`${p.gpuId}-${p.pid}`">
                    <td class="mono tabular">#{{ p.gpuIndex }}</td>
                    <td class="mono tabular">{{ p.pid }}</td>
                    <td class="mono tabular">{{ p.memMb }}</td>
                  </tr>
                </tbody>
              </table>
            </section>
          </div>
          <CleanEmpty
            v-else-if="!loading"
            :icon="ServerCog"
            title="服务器未加载"
            description="请从侧边栏选择一个工作区,或点击刷新。"
          />
        </NSpin>
      </template>
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

/* ── 紧凑页头(共享 .cl-pagebar,本页只做心跳点缀)──────────────── */
.cl-pagebar-title,
.cl-pagebar-meta {
  flex-wrap: wrap;
}
/* 图标块:online 时边框/图标染 success,接替旧 hero 的"活着"信号 */
.cl-pagebar-icon {
  position: relative;
  transition:
    color 0.2s ease,
    background 0.2s ease,
    border-color 0.2s ease;
}
.cl-pagebar-icon.online {
  color: var(--c-success);
  background: color-mix(in srgb, var(--c-success) 9%, transparent);
  border-color: color-mix(in srgb, var(--c-success) 30%, transparent);
}
/* 右上角 6px 小绿点 —— online 时 cl-pulse 呼吸 */
.status-dot {
  position: absolute;
  top: -2px;
  right: -2px;
  width: 6px;
  height: 6px;
  border-radius: var(--radius-full);
  background: var(--c-success);
  --cl-pulse-color: color-mix(in srgb, var(--c-success) 45%, transparent);
}
.server-chip {
  font-size: var(--text-xs);
  padding: 2px var(--space-2);
  background: var(--c-bg-elevated);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-sm);
  color: var(--c-text-secondary);
}
.muted-stamp {
  color: var(--c-text-tertiary);
  font-size: var(--c-text-2xs);
}
.meta-num {
  font-weight: 600;
  color: var(--c-text-secondary);
}
.meta-sep {
  color: var(--c-text-disabled);
}
.meta-ok {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: var(--c-success);
}
.meta-dot {
  width: 6px;
  height: 6px;
  border-radius: var(--radius-full);
  background: var(--c-success);
  --cl-pulse-color: color-mix(in srgb, var(--c-success) 50%, transparent);
}

/* ── Content blocks ────────────────────────────────────────────────── */
.content {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}
.block {
  background: var(--c-bg-elevated);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-lg);
  padding: var(--space-4) var(--space-5);
}
.block-head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-bottom: var(--space-3);
  color: var(--c-text-secondary);
}
.block-title {
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--c-text-primary);
}
.block-subtle {
  font-size: var(--text-xs);
  color: var(--c-text-tertiary);
  margin-left: auto;
  font-weight: 400;
}
.block-warn {
  margin-left: auto;
  font-size: var(--c-text-2xs);
  color: var(--c-warning);
}
.empty-wrap {
  padding: var(--space-3) 0;
}

/* ── GPU cards ─────────────────────────────────────────────────────── */
.gpu-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: var(--space-3);
}
.gpu-card {
  background: var(--c-bg-canvas);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-md);
  padding: var(--space-3);
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
.gpu-card:hover {
  border-color: var(--c-border-default);
}
.gpu-card-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-2);
}
.gpu-id {
  display: flex;
  gap: var(--space-2);
  align-items: baseline;
  min-width: 0;
}
.gpu-index {
  color: var(--c-text-tertiary);
  font-size: var(--c-text-2xs);
}
.gpu-model {
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--c-text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.gpu-state {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: var(--c-text-2xs);
  flex-shrink: 0;
}
.gpu-state-on {
  color: var(--c-success);
}
.gpu-state-off {
  color: var(--c-text-tertiary);
}
.gpu-dot {
  width: 7px;
  height: 7px;
  border-radius: var(--radius-full);
  align-self: center;
}
.gpu-state-on .gpu-dot {
  background: var(--c-success);
  --cl-pulse-color: color-mix(in srgb, var(--c-success) 50%, transparent);
}
.gpu-state-off .gpu-dot {
  background: var(--c-border-default);
}

.stat-strip {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--space-2);
  padding: var(--space-2) 0;
}
.stat {
  display: flex;
  flex-direction: column;
  gap: 1px;
}
.stat-label {
  font-size: 10px;
  color: var(--c-text-tertiary);
  text-transform: uppercase;
  letter-spacing: var(--tracking-caps);
}
.stat-value {
  font-size: var(--text-sm);
  color: var(--c-text-primary);
  font-weight: 600;
}
.stat-unit {
  font-size: 10px;
  color: var(--c-text-tertiary);
  font-weight: 400;
  margin-left: 2px;
}
.tone-info {
  color: var(--c-accent);
}
.tone-warn {
  color: var(--c-warning);
}
.tone-danger {
  color: var(--c-danger);
}

/* memory bar — soft width transition + a faint sheen sweeping the fill */
.mem-bar {
  height: 6px;
  background: var(--c-bg-sunken);
  border-radius: var(--radius-full);
  overflow: hidden;
}
.mem-bar-fill {
  position: relative;
  height: 100%;
  background: linear-gradient(
    90deg,
    color-mix(in oklab, var(--c-accent) 70%, transparent),
    var(--c-accent)
  );
  transition: width 450ms cubic-bezier(0.22, 1, 0.36, 1);
}
.mem-bar-fill::after {
  content: '';
  position: absolute;
  inset: 0;
  background: linear-gradient(
    105deg,
    transparent 35%,
    color-mix(in srgb, var(--c-text-inverse) 40%, transparent) 50%,
    transparent 65%
  );
  background-size: 200% 100%;
  background-repeat: no-repeat;
  animation: cl-sheen 3.6s ease-in-out infinite;
}

/* ── Process table ─────────────────────────────────────────────────── */
.proc-table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--text-sm);
}
.proc-table thead th {
  text-align: left;
  font-weight: 500;
  color: var(--c-text-tertiary);
  font-size: var(--c-text-2xs);
  text-transform: uppercase;
  letter-spacing: var(--tracking-caps);
  padding: 6px var(--space-2);
  border-bottom: 1px solid var(--c-border-subtle);
}
.proc-table tbody tr {
  transition: background 0.15s ease;
}
.proc-table tbody tr:hover {
  background: var(--c-bg-sunken);
}
.proc-table tbody td {
  padding: 6px var(--space-2);
  border-bottom: 1px solid var(--c-border-subtle);
  color: var(--c-text-primary);
}
.proc-table tbody tr:last-child td {
  border-bottom: 0;
}

/* Reduced motion: mem-bar 的宽度过渡与 sheen 停止。
   (cl-enter / cl-pulse / cl-lift 在全局规则里已降级。) */
@media (prefers-reduced-motion: reduce) {
  .mem-bar-fill {
    transition: none;
  }
  .mem-bar-fill::after {
    animation: none;
  }
}

/* 工作区缺失的原地状态(与 PaWorkspace 同款) */
.ws-missing {
  max-width: 480px;
  margin: var(--space-16) auto;
  padding: var(--space-8);
  text-align: center;
  border: 1px dashed var(--c-border-default);
  border-radius: var(--radius-lg);
  background: var(--c-bg-elevated);
}
.ws-missing h2 {
  margin: 0 0 var(--space-3);
  font-size: var(--text-lg);
  font-weight: 600;
  color: var(--c-text-primary);
}
.ws-missing p {
  margin: 0 0 var(--space-5);
  font-size: var(--text-sm);
  color: var(--c-text-secondary);
}
.ws-missing-actions {
  display: flex;
  justify-content: center;
  gap: var(--space-3);
}
</style>

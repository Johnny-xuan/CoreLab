<script setup lang="ts">
/**
 * MyUsage — /me/usage page (Phase 7 FU-35).
 *
 * Month picker + quick-stat cards + by_server + by_pa tables. Data
 * comes from GET /api/v1/usage/me?month=YYYY-MM (Phase 7 C4).
 *
 * Visual (v2, B 型监控页): cl-pagebar 紧凑页头 + .cl-stat 大 tabular
 * 数字 + 表格行内水平用量条(纯展示),数据本身当主角。
 */

import { computed, h, onMounted, ref, watch } from 'vue';
import { NAlert, NCard, NDataTable, NDatePicker, NSpin, useMessage } from 'naive-ui';
import type { DataTableColumns } from 'naive-ui';
import { BarChart3, ServerCog, Users2 } from 'lucide-vue-next';

import AppLayout from '@/layouts/AppLayout.vue';
import CleanEmpty from '@/components/CleanEmpty.vue';
import * as usageApi from '@/api/usage';
import type { UsageByPa, UsageByServer, UsageResponse } from '@/api/usage';

const message = useMessage();

function fmtMonth(d: Date): string {
  const y = d.getFullYear();
  const m = (d.getMonth() + 1).toString().padStart(2, '0');
  return `${y}-${m}`;
}

const today = new Date();
const monthValue = ref<number>(Date.UTC(today.getFullYear(), today.getMonth(), 1));
const data = ref<UsageResponse | null>(null);
const loading = ref(false);
const err = ref<string | null>(null);

const monthIso = computed(() => fmtMonth(new Date(monthValue.value)));

async function load(): Promise<void> {
  loading.value = true;
  err.value = null;
  try {
    data.value = await usageApi.getMyUsage(monthIso.value);
  } catch {
    err.value = '用量数据加载失败';
    message.error('用量数据加载失败');
  } finally {
    loading.value = false;
  }
}

onMounted(load);
watch(monthIso, load);

/* ── 纯展示 computed:行内用量条的归一化基准(各表的最大时长) ── */
const maxServerHours = computed(() =>
  (data.value?.by_server ?? []).reduce((max, row) => Math.max(max, row.hours), 0),
);
const maxPaHours = computed(() =>
  (data.value?.by_pa ?? []).reduce((max, row) => Math.max(max, row.hours), 0),
);

/** 时长单元格:水平用量条(宽度 = 行时长 / 最大时长)+ tabular 数字。纯展示。 */
function renderHoursCell(hours: number, max: number) {
  const pct = max > 0 ? Math.max((hours / max) * 100, 3) : 0;
  return h('div', { class: 'usage-cell' }, [
    h('div', { class: 'usage-track', 'aria-hidden': 'true' }, [
      h('div', { class: 'usage-fill', style: { width: `${pct}%` } }),
    ]),
    h('span', { class: 'mono tabular usage-num' }, hours.toFixed(2)),
  ]);
}

const serverCols = computed<DataTableColumns<UsageByServer>>(() => [
  {
    title: '服务器',
    key: 'hostname',
    render: (row) => h('span', { class: 'mono' }, row.hostname),
  },
  {
    title: '服务器 ID',
    key: 'server_id',
    width: 110,
    render: (row) => `#${row.server_id}`,
  },
  {
    title: '时长',
    key: 'hours',
    width: 190,
    render: (row) => renderHoursCell(row.hours, maxServerHours.value),
  },
]);

const paCols = computed<DataTableColumns<UsageByPa>>(() => [
  {
    title: 'Linux 用户',
    key: 'linux_username',
    render: (row) => h('span', { class: 'mono' }, row.linux_username),
  },
  {
    title: '服务器',
    key: 'hostname',
    render: (row) => h('span', { class: 'mono' }, row.hostname),
  },
  {
    title: 'PA ID',
    key: 'pa_id',
    width: 90,
    render: (row) => `#${row.pa_id}`,
  },
  {
    title: '时长',
    key: 'hours',
    width: 190,
    render: (row) => renderHoursCell(row.hours, maxPaHours.value),
  },
]);
</script>

<template>
  <AppLayout>
    <section class="page">
      <header class="cl-pagebar cl-enter">
        <div class="cl-pagebar-icon">
          <BarChart3 :size="20" :stroke-width="1.75" />
        </div>
        <div class="cl-pagebar-body">
          <h1 class="cl-pagebar-title">我的 GPU 用量</h1>
          <p class="cl-pagebar-sub">按月统计你的 GPU 时长、预约与治理事件。</p>
        </div>
        <div class="cl-pagebar-actions">
          <NDatePicker
            v-model:value="monthValue"
            type="month"
            format="yyyy-MM"
            :clearable="false"
            class="month-picker"
          />
        </div>
      </header>

      <NSpin :show="loading">
        <NAlert v-if="err !== null" type="error" :title="err" class="usage-alert" />
        <template v-else-if="data !== null">
          <section class="stat-grid">
            <article class="stat cl-lift cl-enter" style="--cl-delay: 0.05s">
              <span class="stat-label">GPU 总时长</span>
              <span
                v-if="data.gpu_hours_used > 0"
                class="cl-stat tabular cl-enter"
                style="--cl-delay: 0.12s"
              >
                {{ data.gpu_hours_used.toFixed(1) }}<span class="cl-stat-unit">h</span>
              </span>
              <span v-else class="cl-stat tabular stat-zero">
                0<span class="cl-stat-unit">h</span>
              </span>
            </article>
            <article class="stat cl-lift cl-enter" style="--cl-delay: 0.1s">
              <span class="stat-label">完成率</span>
              <span
                v-if="data.reservation_count > 0"
                class="cl-stat tabular cl-enter"
                style="--cl-delay: 0.17s"
              >
                {{ (data.completion_rate * 100).toFixed(1) }}<span class="cl-stat-unit">%</span>
              </span>
              <span v-else class="cl-stat stat-zero">—</span>
            </article>
            <article class="stat cl-lift cl-enter" style="--cl-delay: 0.15s">
              <span class="stat-label">预约次数</span>
              <span
                v-if="data.reservation_count > 0"
                class="cl-stat tabular cl-enter"
                style="--cl-delay: 0.22s"
              >
                {{ data.reservation_count }}<span class="cl-stat-unit">次</span>
              </span>
              <span v-else class="cl-stat tabular stat-zero">
                0<span class="cl-stat-unit">次</span>
              </span>
            </article>
            <article class="stat cl-lift cl-enter" style="--cl-delay: 0.2s">
              <span class="stat-label">收到的告警</span>
              <span
                v-if="data.alerts_received > 0"
                class="cl-stat tabular stat-warn cl-enter"
                style="--cl-delay: 0.27s"
              >
                {{ data.alerts_received }}<span class="cl-stat-unit">次</span>
              </span>
              <span v-else class="cl-stat tabular stat-zero">
                0<span class="cl-stat-unit">次</span>
              </span>
            </article>
            <article class="stat cl-lift cl-enter" style="--cl-delay: 0.25s">
              <span class="stat-label">违规关联</span>
              <span
                v-if="data.compliance_violations > 0"
                class="cl-stat tabular stat-danger cl-enter"
                style="--cl-delay: 0.32s"
              >
                {{ data.compliance_violations }}<span class="cl-stat-unit">次</span>
              </span>
              <span v-else class="cl-stat tabular stat-zero">
                0<span class="cl-stat-unit">次</span>
              </span>
            </article>
          </section>

          <section class="tables">
            <NCard :bordered="true" class="card cl-enter" style="--cl-delay: 0.25s">
              <template #header>
                <div class="card-head">
                  <span class="card-icon">
                    <ServerCog :size="14" :stroke-width="1.75" />
                  </span>
                  <span class="card-title">按服务器</span>
                </div>
              </template>
              <div v-if="data.by_server.length === 0" class="empty-inner">
                <CleanEmpty
                  :icon="ServerCog"
                  title="本月暂无数据"
                  description="使用了多台 server 时,这里会按 hostname 汇总。"
                  compact
                />
              </div>
              <div v-else class="table-wrap">
                <NDataTable
                  :columns="serverCols"
                  :data="data.by_server"
                  :bordered="false"
                  size="small"
                />
              </div>
            </NCard>

            <NCard :bordered="true" class="card cl-enter" style="--cl-delay: 0.3s">
              <template #header>
                <div class="card-head">
                  <span class="card-icon">
                    <Users2 :size="14" :stroke-width="1.75" />
                  </span>
                  <span class="card-title">按 Linux 账号</span>
                </div>
              </template>
              <div v-if="data.by_pa.length === 0" class="empty-inner">
                <CleanEmpty
                  :icon="Users2"
                  title="本月暂无数据"
                  description="多个 PA 上跑过预约时,这里按 Linux 用户聚合。"
                  compact
                />
              </div>
              <div v-else class="table-wrap">
                <NDataTable :columns="paCols" :data="data.by_pa" :bordered="false" size="small" />
              </div>
            </NCard>
          </section>
        </template>
      </NSpin>
    </section>
  </AppLayout>
</template>

<style scoped>
.page {
  padding: var(--space-6) var(--space-8);
  max-width: 1200px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}
/* .page 的 flex gap 已提供页头与正文间距,去掉 pagebar 自带的下边距避免叠加。 */
.page > .cl-pagebar {
  margin-bottom: 0;
}
.month-picker {
  width: 160px;
}
.usage-alert {
  margin-top: var(--space-2);
}

.stat-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: var(--space-3);
}
.stat {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-4) var(--space-5);
  background: var(--c-bg-elevated);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-lg);
}
.stat-label {
  font-size: var(--text-xs);
  color: var(--c-text-tertiary);
  text-transform: uppercase;
  letter-spacing: var(--tracking-caps);
}
/* 真实 0 值:灰显,弱化存在感。 */
.stat-zero {
  color: var(--c-text-tertiary);
  font-weight: 500;
}
/* 告警 > 0 时数值染警示色。 */
.stat-warn {
  color: var(--c-warning);
}
.stat-danger {
  color: var(--c-danger);
}

.tables {
  margin-top: var(--space-4);
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-4);
}
@media (max-width: 880px) {
  .tables {
    grid-template-columns: 1fr;
  }
}

.card {
  background: var(--c-bg-elevated);
}
.card :deep(.n-card__content) {
  padding: 0;
}
.card :deep(.n-card-header) {
  padding: var(--space-3) var(--space-4);
  border-bottom: 1px solid var(--c-border-subtle);
}
.card-head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}
.card-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: var(--radius-sm);
  background: var(--c-bg-sunken);
  border: 1px solid var(--c-border-subtle);
  color: var(--c-text-secondary);
}
.card-title {
  font-size: var(--text-sm);
  font-weight: 600;
  letter-spacing: var(--tracking-tight);
  color: var(--c-text-primary);
}
.table-wrap {
  border-top: none;
}
.empty-inner {
  margin: var(--space-3);
  padding: var(--space-2);
  border: 1px dashed var(--c-border-default);
  border-radius: var(--radius-md);
}
</style>

<style>
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

/* 行内用量条 — 由列 render 函数生成(无 scope id),故放在非 scoped 块,
   并以 .page .table-wrap 限定作用范围。 */
.page .table-wrap .usage-cell {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}
.page .table-wrap .usage-track {
  flex: 1 1 auto;
  height: 6px;
  border-radius: var(--radius-full);
  background: color-mix(in srgb, var(--c-accent) 8%, transparent);
  overflow: hidden;
}
.page .table-wrap .usage-fill {
  height: 100%;
  border-radius: var(--radius-full);
  background: color-mix(in srgb, var(--c-accent) 65%, transparent);
  transition: width 0.4s ease;
}
@media (prefers-reduced-motion: reduce) {
  .page .table-wrap .usage-fill {
    transition: none;
  }
}
.page .table-wrap .usage-num {
  flex: none;
  min-width: 52px;
  text-align: right;
  color: var(--c-text-primary);
}
</style>

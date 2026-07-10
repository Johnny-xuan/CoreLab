<script setup lang="ts">
/**
 * AdminEnrollmentTokens — lab_admin lifecycle view.
 *
 * Shows every enrollment token ever issued for this lab with a derived
 * status (unused / used / expired). Filter pills + a refresh action.
 * Regenerate happens from the corresponding server's detail page (the
 * snippet is only meaningful in that context).
 */

import { computed, h, onMounted, ref } from 'vue';
import { NButton, NDataTable, NIcon, NTag, useMessage, type DataTableColumns } from 'naive-ui';
import { extractDetail } from '@/utils/extractDetail';
import { KeyRound, RefreshCw } from 'lucide-vue-next';

import AppLayout from '@/layouts/AppLayout.vue';
import {
  listEnrollmentTokens,
  type EnrollmentTokenAdminItem,
  type EnrollmentTokenStatus,
} from '@/api/admin';

const message = useMessage();
const rows = ref<EnrollmentTokenAdminItem[]>([]);
const filter = ref<EnrollmentTokenStatus | 'all'>('all');
const loading = ref(false);

async function refresh(): Promise<void> {
  loading.value = true;
  try {
    rows.value = await listEnrollmentTokens();
  } catch (err) {
    message.error(extractDetail(err, '加载失败'));
  } finally {
    loading.value = false;
  }
}

onMounted(refresh);

const filtered = computed(() =>
  filter.value === 'all' ? rows.value : rows.value.filter((r) => r.status === filter.value),
);

const counts = computed(() => ({
  all: rows.value.length,
  unused: rows.value.filter((r) => r.status === 'unused').length,
  used: rows.value.filter((r) => r.status === 'used').length,
  expired: rows.value.filter((r) => r.status === 'expired').length,
}));

// Pagebar meta — purely presentational, derived from counts above.
// 已用/过期 are grouped as "已消耗" since both mean the token can no
// longer enroll a new agent.
const spentCount = computed(() => counts.value.used + counts.value.expired);

const statusTagType = (s: EnrollmentTokenStatus): 'success' | 'default' | 'warning' =>
  s === 'unused' ? 'success' : s === 'used' ? 'default' : 'warning';

// Chinese display label for each derived status. Keeps the underlying
// enum (unused/used/expired) intact for any logic that depends on it.
const statusLabel = (s: EnrollmentTokenStatus): string =>
  s === 'unused' ? '有效' : s === 'used' ? '已使用' : '已过期';

const columns = computed<DataTableColumns<EnrollmentTokenAdminItem>>(() => [
  {
    title: 'ID',
    key: 'id',
    width: 64,
    render: (row) => h('span', { class: 'mono tabular' }, `#${row.id}`),
  },
  {
    title: '主机名模式',
    key: 'expected_hostname_pattern',
    // Flexible column — give it a floor + ellipsis so a narrow viewport
    // can't crush the header into vertical single-char text. Paired with
    // the table's scroll-x below, the row scrolls instead of collapsing.
    minWidth: 220,
    ellipsis: { tooltip: true },
    render: (row) => h('span', { class: 'mono' }, row.expected_hostname_pattern || '*'),
  },
  {
    title: '状态',
    key: 'status',
    width: 120,
    render: (row) =>
      h('span', { class: 'status-cell' }, [
        h('span', {
          class: [
            'status-dot',
            `status-dot--${row.status}`,
            row.status === 'unused' ? 'cl-pulse' : '',
          ],
          'aria-hidden': 'true',
        }),
        h(
          NTag,
          { size: 'small', type: statusTagType(row.status), bordered: false },
          { default: () => statusLabel(row.status) },
        ),
      ]),
  },
  {
    title: '创建时间',
    key: 'created_at',
    width: 180,
    render: (row) => h('span', { class: 'mono tabular muted' }, row.created_at ?? '—'),
  },
  {
    title: '过期时间',
    key: 'expires_at',
    width: 180,
    render: (row) => h('span', { class: 'mono tabular muted' }, row.expires_at ?? '—'),
  },
  {
    title: '使用时间',
    key: 'used_at',
    width: 180,
    render: (row) => h('span', { class: 'mono tabular muted' }, row.used_at ?? '—'),
  },
  {
    title: '使用者',
    key: 'used_by_server_id',
    width: 100,
    render: (row) =>
      h(
        'span',
        { class: 'mono tabular muted' },
        row.used_by_server_id ? `#${row.used_by_server_id}` : '—',
      ),
  },
]);

interface FilterChip {
  key: EnrollmentTokenStatus | 'all';
  label: string;
  count: number;
}

const chips = computed<FilterChip[]>(() => [
  { key: 'all', label: '全部', count: counts.value.all },
  { key: 'unused', label: '未使用', count: counts.value.unused },
  { key: 'used', label: '已使用', count: counts.value.used },
  { key: 'expired', label: '已过期', count: counts.value.expired },
]);
</script>

<template>
  <AppLayout>
    <div class="page">
      <header class="cl-pagebar cl-enter">
        <div class="cl-pagebar-icon">
          <NIcon :size="20"><KeyRound :size="20" :stroke-width="1.75" /></NIcon>
        </div>
        <div class="cl-pagebar-body">
          <h1 class="cl-pagebar-title">
            接入令牌
            <span class="cl-pagebar-meta">
              <span class="meta-item meta-ok">
                <span class="meta-dot cl-pulse" aria-hidden="true" />
                有效 <span class="meta-num mono tabular">{{ counts.unused }}</span>
              </span>
              <span class="meta-sep" aria-hidden="true">·</span>
              <span class="meta-item">
                已用 / 过期 <span class="meta-num mono tabular">{{ spentCount }}</span>
              </span>
              <span class="meta-sep" aria-hidden="true">·</span>
              <span class="meta-item">
                共 <span class="meta-num mono tabular">{{ counts.all }}</span>
              </span>
            </span>
          </h1>
          <p class="cl-pagebar-sub">用于把新服务器的 agent 接入本实验室</p>
        </div>
        <div class="cl-pagebar-actions">
          <NButton size="small" :loading="loading" @click="refresh">
            <template #icon>
              <NIcon><RefreshCw :size="14" :stroke-width="2" /></NIcon>
            </template>
            刷新
          </NButton>
        </div>
      </header>

      <div class="filters cl-enter" style="--cl-delay: 0.06s">
        <button
          v-for="chip in chips"
          :key="chip.key"
          type="button"
          :class="['filter-pill', { 'is-active': filter === chip.key }]"
          @click="filter = chip.key"
        >
          <span>{{ chip.label }}</span>
          <span class="filter-pill-count mono tabular">{{ chip.count }}</span>
        </button>
      </div>

      <div class="table-wrap cl-enter cl-lift" style="--cl-delay: 0.12s">
        <NDataTable
          :columns="columns"
          :data="filtered"
          :loading="loading"
          :bordered="false"
          size="small"
          :scroll-x="1040"
        />
      </div>

      <p class="hint cl-enter" style="--cl-delay: 0.18s">
        请从对应服务器的详情页重新生成 —— 安装代码片段只有在限定到该服务器主机名的情况下才有意义。
      </p>
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

/* ── 页头 meta — 计数内联在标题旁,有效计数带一枚小状态点 ──────────── */
.meta-item {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  white-space: nowrap;
}
.meta-num {
  font-weight: 600;
  color: var(--c-text-primary);
}
.meta-ok {
  color: var(--c-success);
}
.meta-ok .meta-num {
  color: var(--c-success);
}
.meta-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex-shrink: 0;
  background: var(--c-success);
  --cl-pulse-color: color-mix(in srgb, var(--c-success) 50%, transparent);
}
.meta-sep {
  color: var(--c-text-disabled);
}

.filters {
  display: flex;
  gap: var(--space-2);
  flex-wrap: wrap;
}
.filter-pill {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  height: 28px;
  padding: 0 var(--space-3);
  background: var(--c-bg-elevated);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-pill);
  font-family: var(--font-sans);
  font-size: var(--text-xs);
  color: var(--c-text-secondary);
  cursor: pointer;
  transition:
    background 120ms ease,
    border-color 120ms ease,
    color 120ms ease;
}
.filter-pill:hover {
  border-color: var(--c-border-default);
  color: var(--c-text-primary);
}
.filter-pill.is-active {
  background: var(--c-primary);
  border-color: var(--c-primary);
  color: var(--c-primary-on);
}
.filter-pill-count {
  font-size: var(--text-2xs);
  opacity: 0.7;
}
.filter-pill.is-active .filter-pill-count {
  opacity: 0.9;
}

.table-wrap {
  background: var(--c-bg-elevated);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-lg);
  overflow: hidden;
}
.hint {
  margin: 0;
  font-size: var(--text-xs);
  color: var(--c-text-tertiary);
}

/* Status cell — a small state dot precedes the tag; valid tokens
   breathe gently (cl-pulse). Dot colour mirrors the tag semantics. */
.status-cell {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
}
.status-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex-shrink: 0;
  background: var(--c-border-strong);
}
.status-dot--unused {
  background: var(--c-success);
  --cl-pulse-color: color-mix(in srgb, var(--c-success) 50%, transparent);
}
.status-dot--used {
  background: var(--c-text-disabled);
}
.status-dot--expired {
  background: var(--c-warning);
}

/* cl-enter / cl-pulse / cl-lift 的 reduced-motion 降级由 main.css 统一处理。 */
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

/* Row hover highlight — a subtle wash + accent left rail to echo the
   key/credential motif. Transition is gentle; reduced-motion still
   gets the colour change (no movement involved). */
.table-wrap .n-data-table .n-data-table-tbody .n-data-table-tr {
  transition: background 0.15s ease;
}
.table-wrap .n-data-table .n-data-table-tbody .n-data-table-tr:hover .n-data-table-td {
  background: color-mix(in srgb, var(--c-accent) 5%, var(--c-bg-elevated));
}
</style>

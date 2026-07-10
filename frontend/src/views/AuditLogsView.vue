<script setup lang="ts">
/**
 * AuditLogsView — lab-scoped audit-log browser (Phase 9 P9-9, docs/07 §6.6).
 *
 * 5 server-side filters (actor / action / target_type / target_server /
 * created_at_from + _to) + pagination + row-click drawer with the raw
 * payload pretty-printed. Backend `/audit-logs` scopes the rows: lab_admin
 * sees the whole lab, server_admin sees their owned servers, normal users
 * see only their own actor rows — the UI doesn't need to pre-gate.
 *
 * Routes accept `?target_server_id=N` as the deep-link hook from a
 * Server Detail page (Phase 9 C7 will use it).
 */

import { computed, h, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import {
  NButton,
  NDataTable,
  NDrawer,
  NDrawerContent,
  NInput,
  NInputNumber,
  NPagination,
  NTag,
  useMessage,
  type DataTableColumns,
} from 'naive-ui';
import { ScrollText } from 'lucide-vue-next';
import { extractDetail } from '@/utils/extractDetail';

import AppLayout from '@/layouts/AppLayout.vue';
import { listAuditLogs, type AuditFilters, type AuditLogRead } from '@/api/auditLogs';

const route = useRoute();
const router = useRouter();
const message = useMessage();

const items = ref<AuditLogRead[]>([]);
const total = ref(0);
const totalPages = ref(0);
const loading = ref(false);

const page = ref(1);
const size = ref(20);

const actorUserId = ref<number | null>(null);
const action = ref('');
const targetType = ref('');
const targetServerId = ref<number | null>(null);
const createdFromIso = ref('');
const createdToIso = ref('');

const drawerOpen = ref(false);
const drawerRow = ref<AuditLogRead | null>(null);

function _seedFromQuery(): void {
  const q = route.query;
  if (q.target_server_id) {
    const n = Number(q.target_server_id);
    if (!Number.isNaN(n) && n > 0) targetServerId.value = n;
  }
  if (q.actor_user_id) {
    const n = Number(q.actor_user_id);
    if (!Number.isNaN(n) && n > 0) actorUserId.value = n;
  }
  if (typeof q.action === 'string') action.value = q.action;
  if (typeof q.target_type === 'string') targetType.value = q.target_type;
}

function _buildFilters(): AuditFilters {
  return {
    actor_user_id: actorUserId.value,
    action: action.value.trim() || null,
    target_type: targetType.value.trim() || null,
    target_server_id: targetServerId.value,
    created_at_from: createdFromIso.value.trim() || null,
    created_at_to: createdToIso.value.trim() || null,
    page: page.value,
    size: size.value,
  };
}

async function reload(): Promise<void> {
  loading.value = true;
  try {
    const resp = await listAuditLogs(_buildFilters());
    items.value = resp.items;
    total.value = resp.total;
    totalPages.value = resp.total_pages;
  } catch (err) {
    message.error(extractDetail(err, '加载审计日志失败'));
  } finally {
    loading.value = false;
  }
}

function applyFilters(): void {
  page.value = 1;
  void reload();
}

function clearFilters(): void {
  actorUserId.value = null;
  action.value = '';
  targetType.value = '';
  targetServerId.value = null;
  createdFromIso.value = '';
  createdToIso.value = '';
  page.value = 1;
  void router.replace({ query: {} });
  void reload();
}

function openDrawer(row: AuditLogRead): void {
  drawerRow.value = row;
  drawerOpen.value = true;
}

// ── Pure-display helpers — no business logic, no data fetching ──────────
// result 语义色:ok → success,denied → warning,其余(error 等)→ danger。
function resultTone(result: string): 'success' | 'warning' | 'error' {
  if (result === 'ok') return 'success';
  if (result === 'denied') return 'warning';
  return 'error';
}
function resultDotClass(result: string): string {
  return `cl-pulse audit-dot audit-dot-${resultTone(result)}`;
}

// Pagebar 内联计数 — derived purely from already-loaded state.
const filterCount = computed(() => {
  let n = 0;
  if (actorUserId.value !== null) n += 1;
  if (action.value.trim()) n += 1;
  if (targetType.value.trim()) n += 1;
  if (targetServerId.value !== null) n += 1;
  if (createdFromIso.value.trim()) n += 1;
  if (createdToIso.value.trim()) n += 1;
  return n;
});
const okCount = computed(() => items.value.filter((r) => r.result === 'ok').length);
const flaggedCount = computed(() => items.value.filter((r) => r.result !== 'ok').length);

// Visual-only: tag each row so :deep() can give it the audit hover treatment.
// Rows were never click-handled here — the 详情 button is the only action — so
// this stays purely cosmetic.
function rowProps(): Record<string, string> {
  return { class: 'audit-row' };
}

const columns = computed<DataTableColumns<AuditLogRead>>(() => [
  {
    title: '时间',
    key: 'created_at',
    width: 180,
    render: (row) => h('span', { class: 'mono tabular muted' }, row.created_at ?? '—'),
  },
  {
    title: '操作者',
    key: 'actor',
    width: 160,
    render: (row) =>
      row.actor
        ? h('span', { class: 'mono' }, row.actor.username ?? `#${row.actor.id}`)
        : h(NTag, { type: 'default', size: 'small', bordered: false }, { default: () => 'system' }),
  },
  {
    title: '操作',
    key: 'action',
    width: 220,
    render: (row) => h('code', { class: 'audit-action' }, row.action),
  },
  {
    title: '目标',
    key: 'target',
    width: 200,
    render: (row) => {
      if (!row.target_type && row.target_id === null) return '—';
      const t = row.target_type ?? '?';
      const id = row.target_id !== null ? ` #${row.target_id}` : '';
      return h('span', { class: 'mono muted' }, `${t}${id}`);
    },
  },
  {
    title: '服务器',
    key: 'target_server_id',
    width: 90,
    render: (row) =>
      h(
        'span',
        { class: 'mono tabular muted' },
        row.target_server_id !== null ? `#${row.target_server_id}` : '—',
      ),
  },
  {
    title: '结果',
    key: 'result',
    width: 100,
    render: (row) =>
      h(
        NTag,
        {
          type: resultTone(row.result),
          size: 'small',
          bordered: false,
        },
        {
          default: () =>
            h('span', { class: 'audit-result' }, [
              h('span', {
                class:
                  row.result === 'ok'
                    ? resultDotClass(row.result)
                    : `audit-dot audit-dot-${resultTone(row.result)}`,
              }),
              row.result,
            ]),
        },
      ),
  },
  {
    title: '',
    key: 'detail',
    width: 70,
    render: (row) =>
      h(
        NButton,
        { size: 'tiny', tertiary: true, onClick: () => openDrawer(row) },
        { default: () => '详情 →' },
      ),
  },
]);

watch(page, () => {
  void reload();
});
watch(size, () => {
  page.value = 1;
  void reload();
});

onMounted(() => {
  _seedFromQuery();
  void reload();
});
</script>

<template>
  <AppLayout>
    <div class="page">
      <header class="cl-pagebar cl-enter">
        <div class="cl-pagebar-icon">
          <ScrollText :size="20" :stroke-width="1.75" />
        </div>
        <div class="cl-pagebar-body">
          <h1 class="cl-pagebar-title">
            Lab 审计日志
            <span class="cl-pagebar-meta">
              <span class="bar-count"
                ><span class="cl-num">{{ total }}</span> 条留痕</span
              >
              <span class="meta-dot" aria-hidden="true"></span>
              <span v-if="filterCount" class="bar-count bar-filter">
                <span class="status-dot dot-filter" aria-hidden="true" />
                <span class="cl-num">{{ filterCount }}</span> 个筛选生效
              </span>
              <span v-else class="bar-count">全部范围</span>
              <template v-if="okCount">
                <span class="meta-dot" aria-hidden="true"></span>
                <span class="bar-count bar-ok">
                  <span class="status-dot dot-ok cl-pulse" aria-hidden="true" />
                  本页 <span class="cl-num">{{ okCount }}</span> 条成功
                </span>
              </template>
              <template v-if="flaggedCount">
                <span class="meta-dot" aria-hidden="true"></span>
                <span class="bar-count bar-flagged">
                  <span class="status-dot dot-flagged" aria-hidden="true" />
                  本页 <span class="cl-num">{{ flaggedCount }}</span> 条异常
                </span>
              </template>
            </span>
          </h1>
          <p class="cl-pagebar-sub">
            实验室内所有操作的不可篡改留痕 —— 普通用户只能看到自己作为操作者的记录
          </p>
        </div>
        <div class="cl-pagebar-actions">
          <span class="bar-page cl-num">第 {{ page }} / {{ totalPages || 1 }} 页</span>
        </div>
      </header>

      <section class="filter-bar cl-enter" style="--cl-delay: 0.06s">
        <div class="filter-group">
          <label>操作者用户 id</label>
          <NInputNumber
            v-model:value="actorUserId"
            :min="1"
            placeholder="留空 = 不筛选"
            clearable
          />
        </div>
        <div class="filter-group">
          <label>操作</label>
          <NInput v-model:value="action" placeholder="reservation.create" clearable />
        </div>
        <div class="filter-group">
          <label>目标类型</label>
          <NInput v-model:value="targetType" placeholder="gpu / reservation / ..." clearable />
        </div>
        <div class="filter-group">
          <label>目标服务器 id</label>
          <NInputNumber
            v-model:value="targetServerId"
            :min="1"
            placeholder="留空 = 不筛选"
            clearable
          />
        </div>
        <div class="filter-group">
          <label>起始时间(ISO8601)</label>
          <NInput v-model:value="createdFromIso" placeholder="2026-06-01T00:00:00Z" clearable />
        </div>
        <div class="filter-group">
          <label>结束时间(ISO8601)</label>
          <NInput v-model:value="createdToIso" placeholder="2026-06-30T23:59:59Z" clearable />
        </div>
        <div class="filter-actions">
          <NButton @click="clearFilters">重置</NButton>
          <NButton type="primary" @click="applyFilters">应用</NButton>
        </div>
      </section>

      <div class="table-wrap cl-enter" style="--cl-delay: 0.12s">
        <NDataTable
          :columns="columns"
          :data="items"
          :loading="loading"
          :bordered="false"
          size="small"
          :row-key="(row: AuditLogRead) => row.id"
          :row-props="rowProps"
          @update:checked-row-keys="() => {}"
        />
        <footer class="pagination">
          <span class="total mono tabular">
            共 {{ total }} 条 · 第 {{ page }} / {{ totalPages }} 页
          </span>
          <NPagination
            v-model:page="page"
            v-model:page-size="size"
            :page-count="totalPages || 1"
            :page-sizes="[20, 50, 100]"
            show-size-picker
          />
        </footer>
      </div>

      <NDrawer v-model:show="drawerOpen" :width="560" placement="right">
        <NDrawerContent v-if="drawerRow" :title="`审计 #${drawerRow.id}`" closable>
          <dl class="kv">
            <dt>时间</dt>
            <dd class="mono tabular">{{ drawerRow.created_at ?? '—' }}</dd>
            <dt>操作者</dt>
            <dd>
              {{
                drawerRow.actor ? (drawerRow.actor.username ?? `#${drawerRow.actor.id}`) : 'system'
              }}
            </dd>
            <dt>操作</dt>
            <dd>
              <code>{{ drawerRow.action }}</code>
            </dd>
            <dt>目标</dt>
            <dd class="mono">
              {{ drawerRow.target_type ?? '—' }}
              {{ drawerRow.target_id !== null ? `#${drawerRow.target_id}` : '' }}
            </dd>
            <dt>服务器</dt>
            <dd class="mono tabular">
              {{ drawerRow.target_server_id !== null ? `#${drawerRow.target_server_id}` : '—' }}
            </dd>
            <dt>IP</dt>
            <dd class="mono">{{ drawerRow.ip_address ?? '—' }}</dd>
            <dt>结果</dt>
            <dd>
              <NTag :type="resultTone(drawerRow.result)" size="small" :bordered="false">
                {{ drawerRow.result }}
              </NTag>
            </dd>
          </dl>
          <h3 class="payload-h">Payload</h3>
          <pre class="payload">{{ JSON.stringify(drawerRow.payload ?? {}, null, 2) }}</pre>
        </NDrawerContent>
      </NDrawer>
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

/* ── pagebar extras(共享 .cl-pagebar-meta 里的内联计数) ──────────── */
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
.status-dot {
  flex: none;
  width: 6px;
  height: 6px;
  border-radius: var(--radius-full);
}
.bar-filter {
  color: var(--c-accent);
}
.dot-filter {
  background: var(--c-accent);
}
.bar-ok {
  color: var(--c-success);
}
.dot-ok {
  background: var(--c-success);
  --cl-pulse-color: color-mix(in srgb, var(--c-success) 45%, transparent);
}
.bar-flagged {
  color: var(--c-warning);
}
.dot-flagged {
  background: var(--c-warning);
}
.bar-page {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  color: var(--c-text-tertiary);
  white-space: nowrap;
}

.filter-bar {
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
  margin-top: var(--space-4);
  background: var(--c-bg-elevated);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-lg);
  overflow: hidden;
}
.pagination {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: var(--space-3) var(--space-4);
  border-top: 1px solid var(--c-border-subtle);
}
.pagination .total {
  color: var(--c-text-secondary);
  font-size: var(--text-xs);
}

.kv {
  display: grid;
  grid-template-columns: 100px 1fr;
  row-gap: var(--space-2);
  column-gap: var(--space-3);
  margin: 0 0 var(--space-4) 0;
}
.kv dt {
  color: var(--c-text-secondary);
  font-size: var(--text-sm);
}
.kv dd {
  margin: 0;
  font-size: var(--text-sm);
}
.kv .mono {
  font-family: var(--font-mono);
}
.payload-h {
  font-size: var(--text-sm);
  margin: 0 0 var(--space-2);
  color: var(--c-text-secondary);
}
.payload {
  background: var(--c-bg-code);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-md);
  padding: var(--space-3);
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  overflow-x: auto;
  margin: 0;
}
.audit-action {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  background: var(--c-bg-sunken);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-sm);
  padding: 1px 6px;
}

/* result status dot inside the tag */
.audit-result {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}
.audit-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex-shrink: 0;
}
.audit-dot-success {
  background: var(--c-success);
  --cl-pulse-color: color-mix(in srgb, var(--c-success) 50%, transparent);
}
.audit-dot-warning {
  background: var(--c-warning);
}
.audit-dot-error {
  background: var(--c-danger);
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

/* Audit row hover — soft accent wash + a gentle nudge, leaving an
   accent "margin mark" to reinforce the ledger / 留痕 motif. The first
   cell carries the bar so it doesn't shift column widths. */
.table-wrap .n-data-table tr.audit-row td {
  transition:
    background 0.15s ease,
    transform 0.15s ease;
}
.table-wrap .n-data-table tr.audit-row:hover td {
  background: color-mix(in srgb, var(--c-accent) 5%, transparent);
}
.table-wrap .n-data-table tr.audit-row td:first-child {
  box-shadow: inset 2px 0 0 transparent;
}
.table-wrap .n-data-table tr.audit-row:hover td:first-child {
  box-shadow: inset 2px 0 0 var(--c-accent);
  transform: translateX(2px);
}

@media (prefers-reduced-motion: reduce) {
  .table-wrap .n-data-table tr.audit-row:hover td:first-child {
    transform: none;
  }
}
</style>

<script setup lang="ts">
/**
 * ManagedServersList — Phase K 切法 D entry into per-server management.
 *
 * Sidebar "Servers I manage" routes here. Lists every server the
 * caller has grant of (lab_admin → all in lab; server_admin → only
 * granted). One row per server with a clear "Manage →" call to action
 * that drills into ManageServer.vue with the 5-tab workspace.
 *
 * Distinct from the read-only "Server status" page in 功能区 — that one
 * is for any authenticated user, this one is scoped to grants.
 */
import { computed, h, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import { NButton, NDataTable, NTag, useMessage, type DataTableColumns } from 'naive-ui';
import { RefreshCw, Server as ServerIcon, ServerCog, ShieldCheck } from 'lucide-vue-next';
import { extractDetail } from '@/utils/extractDetail';
import { formatDateTime, timeAgo } from '@/utils/timeago';

import AppLayout from '@/layouts/AppLayout.vue';
import CleanEmpty from '@/components/CleanEmpty.vue';
import { useAuthStore } from '@/stores/auth';
import { listServers } from '@/api/servers';
import type { ServerRead } from '@/api/servers';

const router = useRouter();
const auth = useAuthStore();
const message = useMessage();

const loading = ref(false);
const allServers = ref<ServerRead[]>([]);

async function refresh(): Promise<void> {
  loading.value = true;
  try {
    allServers.value = await listServers();
    await auth.loadGrants();
  } catch (err) {
    message.error(extractDetail(err, '加载服务器失败'));
  } finally {
    loading.value = false;
  }
}

onMounted(refresh);

const grantedIds = computed(() => new Set(auth.serverAdminGrants.map((g) => g.server_id)));
const myServers = computed(() => allServers.value.filter((s) => grantedIds.value.has(s.id)));

// ── 页头 summary — pure display-only counts derived from myServers.
//    Drives the inline meta counts in the .cl-pagebar; no behaviour change. ──
const totalCount = computed(() => myServers.value.length);
const onlineCount = computed(() => myServers.value.filter((s) => s.status === 'online').length);
const offlineCount = computed(() => myServers.value.filter((s) => s.status === 'offline').length);
const maintenanceCount = computed(
  () => myServers.value.filter((s) => s.status === 'maintenance').length,
);
const pendingCount = computed(() => myServers.value.filter((s) => s.status === 'pending').length);
const grantChip = computed(() =>
  auth.isLabAdmin ? 'lab 管理员 · 全部服务器' : '委派 server_admin',
);

interface Row {
  server: ServerRead;
  via: 'lab_admin' | 'delegated';
}

const rows = computed<Row[]>(() =>
  myServers.value.map((s) => ({
    server: s,
    via: auth.isLabAdmin ? 'lab_admin' : 'delegated',
  })),
);

function goManage(id: number): void {
  void router.push({ name: 'manage-server', params: { server_id: id } });
}

// Display-only: status dot class — online breathes green, others static.
function statusDotClass(status: ServerRead['status']): string {
  return status === 'online' ? 'st-dot st-dot-online cl-pulse' : `st-dot st-dot-${status}`;
}

const columns = computed<DataTableColumns<Row>>(() => [
  {
    title: '服务器',
    key: 'server',
    render: (row) =>
      h('div', { class: 'cell-server' }, [
        h('span', { class: 'mono target' }, row.server.display_name ?? row.server.hostname),
        h('span', { class: 'mono muted small' }, row.server.hostname),
      ]),
  },
  {
    title: '状态',
    key: 'status',
    width: 136,
    render: (row) =>
      h('span', { class: `st-pill st-${row.server.status}` }, [
        h('span', { class: statusDotClass(row.server.status), 'aria-hidden': 'true' }),
        h('span', { class: 'mono st-text' }, row.server.status),
      ]),
  },
  {
    title: '上次心跳',
    key: 'last_heartbeat_at',
    width: 180,
    render: (row) =>
      h(
        'span',
        {
          class: 'mono muted',
          // 相对时间为主,精确时刻(本地、无秒)挂在 title 上 hover 可见。
          title: row.server.last_heartbeat_at
            ? formatDateTime(row.server.last_heartbeat_at)
            : undefined,
        },
        timeAgo(row.server.last_heartbeat_at),
      ),
  },
  {
    title: '我的授权',
    key: 'via',
    width: 140,
    render: (row) =>
      row.via === 'lab_admin'
        ? h(NTag, { type: 'primary', size: 'small', bordered: false }, () => 'lab 管理员')
        : h(NTag, { type: 'info', size: 'small', bordered: false }, () => '服务器管理员'),
  },
  {
    title: '',
    key: 'actions',
    width: 140,
    render: (row) =>
      h(
        NButton,
        { size: 'tiny', type: 'primary', onClick: () => goManage(row.server.id) },
        () => '管理 →',
      ),
  },
]);
</script>

<template>
  <AppLayout>
    <div class="page">
      <!-- ── 紧凑页头(共享 .cl-pagebar,替代原 boxed hero)── -->
      <header class="cl-pagebar cl-enter">
        <div class="cl-pagebar-icon">
          <ServerCog :size="20" :stroke-width="1.75" />
        </div>
        <div class="cl-pagebar-body">
          <h1 class="cl-pagebar-title">
            我管理的服务器
            <span class="cl-pagebar-meta">
              <span class="bar-count"
                >共 <span class="cl-num">{{ totalCount }}</span> 台</span
              >
              <template v-if="onlineCount">
                <span class="meta-dot" aria-hidden="true"></span>
                <span class="bar-st st-on">
                  <span class="bar-dot dot-on cl-pulse" aria-hidden="true" />
                  <span class="cl-num">{{ onlineCount }}</span> 在线
                </span>
              </template>
              <template v-if="maintenanceCount">
                <span class="meta-dot" aria-hidden="true"></span>
                <span class="bar-st st-maint">
                  <span class="bar-dot dot-maint" aria-hidden="true" />
                  <span class="cl-num">{{ maintenanceCount }}</span> 维护
                </span>
              </template>
              <template v-if="pendingCount">
                <span class="meta-dot" aria-hidden="true"></span>
                <span class="bar-st st-pending">
                  <span class="bar-dot dot-pending" aria-hidden="true" />
                  <span class="cl-num">{{ pendingCount }}</span> 待接入
                </span>
              </template>
              <template v-if="offlineCount">
                <span class="meta-dot" aria-hidden="true"></span>
                <span class="bar-st st-off">
                  <span class="bar-dot dot-off" aria-hidden="true" />
                  <span class="cl-num">{{ offlineCount }}</span> 离线
                </span>
              </template>
              <span class="meta-dot" aria-hidden="true"></span>
              <span class="bar-grant">
                <ShieldCheck :size="11" :stroke-width="2" />
                {{ grantChip }}
              </span>
            </span>
          </h1>
          <p class="cl-pagebar-sub">
            你以管理员身份负责的算力节点 —— 点「管理 →」进入管理面板(Linux 账号 / 授权公钥 / 能力 /
            活动 / 管理员)。
            <span v-if="auth.isLabAdmin">你是 lab_admin —— 隐式管理 Lab 里所有服务器。</span>
            <span v-else>只显示已被委派 server_admin 的 server。</span>
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

      <div
        v-if="myServers.length === 0 && !loading"
        class="empty-wrap cl-enter"
        style="--cl-delay: 0.08s"
      >
        <CleanEmpty
          :icon="ServerIcon"
          title="还没被委派任何 server"
          description="联系 lab admin 申请在某台 server 上获得 server_admin 角色。"
          compact
        />
      </div>
      <div v-else class="table-wrap cl-enter cl-lift" style="--cl-delay: 0.08s">
        <NDataTable
          :columns="columns"
          :data="rows"
          :loading="loading"
          :bordered="false"
          :single-line="false"
          size="small"
          :row-props="
            (row) => ({
              style: 'cursor: pointer',
              onClick: () => goManage(row.server.id),
            })
          "
        />
      </div>
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
  width: 100%;
}

/* ── 页头 meta 扩展(计数 + 授权来源,挂在共享 .cl-pagebar-meta 里)── */
.bar-count,
.bar-st {
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
.bar-dot {
  width: 6px;
  height: 6px;
  border-radius: var(--radius-full);
  flex-shrink: 0;
}
.st-on {
  color: var(--c-success);
}
.dot-on {
  background: var(--c-success);
  --cl-pulse-color: color-mix(in srgb, var(--c-success) 50%, transparent);
}
.st-maint {
  color: var(--c-warning);
}
.dot-maint {
  background: var(--c-warning);
}
.st-pending {
  color: var(--c-info);
}
.dot-pending {
  background: var(--c-info);
}
.dot-off {
  background: var(--c-text-disabled);
}
.bar-grant {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  white-space: nowrap;
  color: var(--c-accent);
}
.table-wrap {
  background: var(--c-bg-elevated);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-lg);
  overflow: hidden;
}
.empty-wrap {
  background: var(--c-bg-elevated);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-lg);
}

/* Reduced motion: entrance / pulse degrade via the global cl- rules in main.css. */
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
/* Row hover — background highlight + the server cell nudges right. */
.page .table-wrap .n-data-table .n-data-table-tr:hover .n-data-table-td {
  background: color-mix(in srgb, var(--c-accent) 4%, var(--c-bg-elevated));
}
.page .table-wrap .n-data-table .cell-server {
  transition: transform 0.15s ease;
}
.page .table-wrap .n-data-table .n-data-table-tr:hover .cell-server {
  transform: translateX(3px);
}
.page .table-wrap .n-data-table .mono {
  font-family: var(--font-mono);
}
.page .table-wrap .n-data-table .muted {
  color: var(--c-text-secondary);
}
.page .table-wrap .target {
  color: var(--c-text-primary);
}
.page .table-wrap .cell-server {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.page .table-wrap .small {
  font-size: var(--text-xs);
}

/* Status pill — online breathes green (--c-success), offline stays grey. */
.page .table-wrap .st-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  /* 枚举 token(maintenance 等)绝不折行 —— 配合加宽后的状态列。 */
  white-space: nowrap;
}
.page .table-wrap .st-text {
  font-size: var(--text-xs);
  color: var(--c-text-tertiary);
}
.page .table-wrap .st-online .st-text {
  color: var(--c-success);
}
.page .table-wrap .st-maintenance .st-text {
  color: var(--c-warning);
}
.page .table-wrap .st-pending .st-text {
  color: var(--c-info);
}
.page .table-wrap .st-dot {
  width: 8px;
  height: 8px;
  border-radius: var(--radius-full);
  flex-shrink: 0;
}
.page .table-wrap .st-dot-online {
  background: var(--c-success);
  --cl-pulse-color: color-mix(in srgb, var(--c-success) 50%, transparent);
}
.page .table-wrap .st-dot-maintenance {
  background: var(--c-warning);
}
.page .table-wrap .st-dot-pending {
  background: var(--c-info);
}
.page .table-wrap .st-dot-offline {
  background: var(--c-text-disabled);
}

@media (prefers-reduced-motion: reduce) {
  .page .table-wrap .n-data-table .n-data-table-tr:hover .cell-server {
    transform: none;
  }
}
</style>

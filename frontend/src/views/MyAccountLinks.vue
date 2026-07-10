<script setup lang="ts">
/**
 * MyAccountLinks — Phase K user-side account-link center.
 *
 * Three sections (Vercel-style sub-tabs):
 *   - Active: currently-valid Linux account links (Open workspace, Revoke)
 *   - Pending: outstanding account_link_request rows awaiting admin
 *   - History: revoked links + denied/withdrawn requests
 *
 * Visual: shared compact page bar (.cl-pagebar — link icon, title with
 * inline active/pending/history counts, one-line sub, refresh + add-link
 * actions) above the NTabs + table-wrap shell. Mono IDs/timestamps.
 * Cards inside tabs use the same elevated-bg / subtle-border tokens as
 * the rest of the app and animate in via the shared cl- motion vocabulary.
 */
import { computed, h, onMounted, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import {
  NButton,
  NDataTable,
  NModal,
  NTabPane,
  NTabs,
  NTag,
  useMessage,
  type DataTableColumns,
} from 'naive-ui';
import { Inbox, KeyRound, Link2, Plus, RefreshCw } from 'lucide-vue-next';

import AppLayout from '@/layouts/AppLayout.vue';
import CleanEmpty from '@/components/CleanEmpty.vue';
import AddLinkWizard from '@/components/claim/AddLinkWizard.vue';
import RevokeLinkDialog from '@/components/claim/RevokeLinkDialog.vue';
import * as accountLinksApi from '@/api/accountLinks';
import * as alrApi from '@/api/accountLinkRequests';
import * as paApi from '@/api/physicalAccounts';
import * as serversApi from '@/api/servers';
import type { AccountLinkRead } from '@/api/accountLinks';
import type { AccountLinkRequestRead } from '@/api/accountLinkRequests';
import type { PhysicalAccountRead } from '@/api/physicalAccounts';
import type { ServerRead } from '@/api/servers';
import { useWorkspaceStore } from '@/stores/workspace';
import { extractDetail } from '@/utils/extractDetail';

const router = useRouter();
const route = useRoute();
const message = useMessage();
const workspace = useWorkspaceStore();

const loading = ref(false);
const links = ref<AccountLinkRead[]>([]);
const requests = ref<AccountLinkRequestRead[]>([]);
const pas = ref<Map<number, PhysicalAccountRead>>(new Map());
const servers = ref<Map<number, ServerRead>>(new Map());

const tab = ref<'active' | 'pending' | 'history'>('active');
const wizardOpen = ref(false);
const revokeTarget = ref<AccountLinkRead | null>(null);

async function refresh(): Promise<void> {
  loading.value = true;
  try {
    const [allLinks, mineReqs, serverList] = await Promise.all([
      accountLinksApi.listMyAccountLinks(true),
      alrApi.listMine(),
      serversApi.listServers(),
    ]);
    links.value = allLinks;
    requests.value = mineReqs;
    servers.value = new Map(serverList.map((s) => [s.id, s]));

    const paIds = new Set<number>();
    for (const l of allLinks) paIds.add(l.physical_account_id);
    for (const r of mineReqs) paIds.add(r.physical_account_id);
    const fetched = await Promise.all([...paIds].map((id) => paApi.getPa(id).catch(() => null)));
    const next = new Map<number, PhysicalAccountRead>();
    for (const pa of fetched) {
      if (pa !== null) next.set(pa.id, pa);
    }
    pas.value = next;
  } catch (err) {
    message.error(extractDetail(err, '加载账号关联失败'));
  } finally {
    loading.value = false;
  }
}

onMounted(async () => {
  await refresh();
  // Deep-link from onboarding / claim page opens the add-link wizard directly.
  if (route.query.add !== undefined) wizardOpen.value = true;
});

const activeLinks = computed(() => links.value.filter((l) => l.is_active));
const revokedLinks = computed(() => links.value.filter((l) => !l.is_active));
const pendingRequests = computed(() =>
  requests.value.filter((r) => r.status === 'pending' || r.status === 'approved'),
);
const historyRequests = computed(() =>
  requests.value.filter((r) => r.status === 'denied' || r.status === 'withdrawn'),
);
const historyCount = computed(() => revokedLinks.value.length + historyRequests.value.length);

function paLabel(paId: number): string {
  const pa = pas.value.get(paId);
  if (pa === undefined) return `PA #${paId}`;
  const s = servers.value.get(pa.server_id);
  const hostname = s?.display_name ?? s?.hostname ?? `server #${pa.server_id}`;
  return `${pa.linux_username} @ ${hostname}`;
}

function fmtDateTime(iso: string): string {
  const d = new Date(iso);
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  return `${y}-${m}-${day} ${hh}:${mm}`;
}

const sourceTag = (source: AccountLinkRead['source']) => {
  const map: Record<
    AccountLinkRead['source'],
    { label: string; type: 'success' | 'info' | 'warning' | 'default' }
  > = {
    ssh_challenge: { label: 'SSH challenge', type: 'success' },
    password_pam: { label: 'PAM 密码', type: 'info' },
    admin_prepared_then_ssh: { label: '管理员推 key', type: 'warning' },
    admin_declared: { label: '管理员声明', type: 'default' },
  };
  const m = map[source];
  return h(NTag, { size: 'small', type: m.type, bordered: false }, () => m.label);
};

async function openWorkspace(link: AccountLinkRead): Promise<void> {
  workspace.setCurrent(link.physical_account_id);
  await router.push({ name: 'pa-workspace', params: { pa_id: link.physical_account_id } });
}

async function withdrawRequest(req: AccountLinkRequestRead): Promise<void> {
  try {
    await alrApi.withdraw(req.id);
    message.success('已撤回申请');
    await refresh();
  } catch (err) {
    message.error(extractDetail(err, '撤回失败'));
  }
}

function continueApprovedRequest(): void {
  wizardOpen.value = true;
}

const activeColumns = computed<DataTableColumns<AccountLinkRead>>(() => [
  {
    title: '关联',
    key: 'physical_account_id',
    render: (row) =>
      h('div', { class: 'cell-link-row' }, [
        h('span', {
          class: 'link-dot cl-pulse',
          style: '--cl-pulse-color: color-mix(in srgb, var(--c-success) 55%, transparent)',
          title: '生效中',
        }),
        h('div', { class: 'cell-link' }, [
          h('span', { class: 'mono tabular target' }, paLabel(row.physical_account_id)),
          h('span', { class: 'mono muted', style: 'font-size: var(--text-xs)' }, `link #${row.id}`),
        ]),
      ]),
  },
  {
    title: '来源',
    key: 'source',
    width: 150,
    render: (row) => sourceTag(row.source),
  },
  {
    title: '建立时间',
    key: 'established_at',
    width: 160,
    render: (row) => h('span', { class: 'mono tabular muted' }, fmtDateTime(row.established_at)),
  },
  {
    title: '',
    key: 'actions',
    width: 200,
    render: (row) =>
      h('div', { style: 'display:flex;gap:6px;justify-content:flex-end' }, [
        h(NButton, { size: 'tiny', onClick: () => openWorkspace(row) }, () => '打开工作区'),
        h(
          NButton,
          {
            size: 'tiny',
            quaternary: true,
            type: 'error',
            onClick: () => (revokeTarget.value = row),
          },
          () => '撤销',
        ),
      ]),
  },
]);

const pendingColumns = computed<DataTableColumns<AccountLinkRequestRead>>(() => [
  {
    title: '目标',
    key: 'physical_account_id',
    render: (row) =>
      h('div', { class: 'cell-link' }, [
        h('span', { class: 'mono tabular target' }, paLabel(row.physical_account_id)),
        h(
          'span',
          { class: 'mono muted', style: 'font-size: var(--text-xs)' },
          `request #${row.id}`,
        ),
      ]),
  },
  { title: '理由', key: 'request_note', render: (row) => row.request_note ?? '—' },
  {
    title: '状态',
    key: 'status',
    width: 110,
    render: (row) =>
      h(
        NTag,
        {
          size: 'small',
          bordered: false,
          type: row.status === 'approved' ? 'success' : 'warning',
        },
        () => (row.status === 'approved' ? '已批准' : '待审批'),
      ),
  },
  {
    title: '提交时间',
    key: 'created_at',
    width: 160,
    render: (row) => h('span', { class: 'mono tabular muted' }, fmtDateTime(row.created_at)),
  },
  {
    title: '',
    key: 'actions',
    width: 130,
    render: (row) =>
      row.status === 'pending'
        ? h(
            NButton,
            { size: 'tiny', quaternary: true, onClick: () => withdrawRequest(row) },
            () => '撤回',
          )
        : h(
            NButton,
            { size: 'tiny', type: 'primary', onClick: () => continueApprovedRequest() },
            () => '完成验证',
          ),
  },
]);

interface HistoryItem {
  key: string;
  kind: 'link' | 'request';
  paLabel: string;
  status: string;
  statusType: 'default' | 'error';
  when: string;
  note: string;
}

const historyRows = computed<HistoryItem[]>(() => {
  const items: HistoryItem[] = [];
  for (const l of revokedLinks.value) {
    items.push({
      key: `link-${l.id}`,
      kind: 'link',
      paLabel: paLabel(l.physical_account_id),
      status: 'revoked',
      statusType: 'default',
      when: l.revoked_at ?? l.established_at,
      note: l.revoke_reason ?? '',
    });
  }
  for (const r of historyRequests.value) {
    items.push({
      key: `req-${r.id}`,
      kind: 'request',
      paLabel: paLabel(r.physical_account_id),
      status: r.status,
      statusType: r.status === 'denied' ? 'error' : 'default',
      when: r.decided_at ?? r.created_at,
      note: r.decision_note ?? '',
    });
  }
  items.sort((a, b) => (a.when < b.when ? 1 : -1));
  return items;
});

const historyColumns = computed<DataTableColumns<HistoryItem>>(() => [
  {
    title: '目标',
    key: 'paLabel',
    render: (row) => h('span', { class: 'mono tabular target' }, row.paLabel),
  },
  {
    title: '类型',
    key: 'kind',
    width: 90,
    render: (row) =>
      h(NTag, { size: 'small', bordered: false }, () => (row.kind === 'link' ? 'link' : 'request')),
  },
  {
    title: '状态',
    key: 'status',
    width: 110,
    render: (row) =>
      h(NTag, { size: 'small', type: row.statusType, bordered: false }, () => row.status),
  },
  { title: '备注', key: 'note', render: (row) => row.note || '—' },
  {
    title: '时间',
    key: 'when',
    width: 160,
    render: (row) => h('span', { class: 'mono tabular muted' }, fmtDateTime(row.when)),
  },
]);

async function onRevokeDone(): Promise<void> {
  revokeTarget.value = null;
  message.success('已撤销');
  await refresh();
}
</script>

<template>
  <AppLayout>
    <div class="page">
      <header class="cl-pagebar cl-enter">
        <div class="cl-pagebar-icon">
          <Link2 :size="20" :stroke-width="1.75" />
        </div>
        <div class="cl-pagebar-body">
          <h1 class="cl-pagebar-title">
            我的账号关联
            <span class="cl-pagebar-meta">
              <span
                class="live-dot cl-pulse"
                style="--cl-pulse-color: color-mix(in srgb, var(--c-success) 55%, transparent)"
                aria-hidden="true"
              />
              <span
                >生效中 <span class="cl-num">{{ activeLinks.length }}</span></span
              >
              <span class="meta-dot" aria-hidden="true" />
              <span
                >待处理 <span class="cl-num">{{ pendingRequests.length }}</span></span
              >
              <span class="meta-dot" aria-hidden="true" />
              <span
                >历史 <span class="cl-num">{{ historyCount }}</span></span
              >
            </span>
          </h1>
          <p class="cl-pagebar-sub">
            绑定平台账号到具体 server 上的 Linux 账号 —— 预约 / 跑脚本的前提
          </p>
        </div>
        <div class="cl-pagebar-actions">
          <NButton size="small" :loading="loading" @click="refresh">
            <template #icon>
              <RefreshCw :size="13" :stroke-width="1.75" />
            </template>
            刷新
          </NButton>
          <NButton type="primary" size="small" @click="wizardOpen = true">
            <template #icon>
              <Plus :size="14" :stroke-width="2.25" />
            </template>
            新增关联
          </NButton>
        </div>
      </header>

      <div class="tabs-wrap">
        <NTabs v-model:value="tab" type="line" animated size="small">
          <NTabPane name="active" :tab="`生效中 (${activeLinks.length})`">
            <div v-if="activeLinks.length === 0 && !loading" class="empty-wrap cl-enter">
              <CleanEmpty
                :icon="KeyRound"
                title="还没有生效中的关联"
                description="点「新增关联」走向导,选个 Linux 账号开始绑定。"
                compact
              />
            </div>
            <div v-else class="table-wrap cl-enter">
              <NDataTable
                :columns="activeColumns"
                :data="activeLinks"
                :loading="loading"
                :bordered="false"
                :single-line="false"
                size="small"
              />
            </div>
          </NTabPane>

          <NTabPane name="pending" :tab="`待处理 (${pendingRequests.length})`">
            <div v-if="pendingRequests.length === 0 && !loading" class="empty-wrap cl-enter">
              <CleanEmpty
                :icon="Inbox"
                title="没有待审批的申请"
                description="走情形 4 路径(申请管理员推 key)的请求会显示在这里。"
                compact
              />
            </div>
            <div v-else class="table-wrap cl-enter">
              <NDataTable
                :columns="pendingColumns"
                :data="pendingRequests"
                :loading="loading"
                :bordered="false"
                :single-line="false"
                size="small"
              />
            </div>
          </NTabPane>

          <NTabPane name="history" :tab="`历史 (${historyCount})`">
            <div v-if="historyCount === 0 && !loading" class="empty-wrap cl-enter">
              <CleanEmpty
                :icon="Inbox"
                title="还没有历史记录"
                description="撤销过的 link / 拒绝过的申请会在这里留底。"
                compact
              />
            </div>
            <div v-else class="table-wrap cl-enter">
              <NDataTable
                :columns="historyColumns"
                :data="historyRows"
                :loading="loading"
                :bordered="false"
                :single-line="false"
                size="small"
              />
            </div>
          </NTabPane>
        </NTabs>
      </div>

      <NModal
        v-model:show="wizardOpen"
        :mask-closable="false"
        preset="card"
        style="max-width: 44rem"
      >
        <AddLinkWizard @done="((wizardOpen = false), refresh())" @cancel="wizardOpen = false" />
      </NModal>

      <RevokeLinkDialog
        v-if="revokeTarget !== null"
        :link="revokeTarget"
        @done="onRevokeDone"
        @cancel="revokeTarget = null"
      />
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
.muted {
  color: var(--c-text-tertiary);
}

/* ── pagebar extras (status counts inside the shared .cl-pagebar-meta) ── */
.live-dot {
  flex: none;
  width: 6px;
  height: 6px;
  border-radius: var(--radius-full);
  background: var(--c-success);
}
.meta-dot {
  flex: none;
  width: 3px;
  height: 3px;
  border-radius: var(--radius-full);
  background: var(--c-border-strong);
}
.tabs-wrap {
  background: var(--c-bg-elevated);
}
.table-wrap {
  background: var(--c-bg-elevated);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-lg);
  overflow: hidden;
  margin-top: var(--space-3);
}
.empty-wrap {
  background: var(--c-bg-elevated);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-lg);
  margin-top: var(--space-3);
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
.page .table-wrap .n-data-table .mono {
  font-family: var(--font-mono);
}
.page .table-wrap .n-data-table .muted {
  color: var(--c-text-secondary);
}
.page .table-wrap .cell-link {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.page .table-wrap .cell-link-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}
.page .table-wrap .link-dot {
  flex-shrink: 0;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--c-success);
}
.page .table-wrap .target {
  font-size: var(--text-sm);
  color: var(--c-text-primary);
}

/* Row nudge on hover — links slide a touch (connection motif). */
.page .table-wrap .n-data-table .n-data-table-tbody .n-data-table-tr {
  transition:
    background 0.15s ease,
    transform 0.15s ease;
}
.page .table-wrap .n-data-table .n-data-table-tbody .n-data-table-tr:hover {
  transform: translateX(3px);
}
@media (prefers-reduced-motion: reduce) {
  .page .table-wrap .n-data-table .n-data-table-tbody .n-data-table-tr:hover {
    transform: none;
  }
}
</style>

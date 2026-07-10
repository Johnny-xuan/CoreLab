<script setup lang="ts">
/**
 * ServerAdminInbox — Phase K 切法 C 跨 server 收件箱。
 *
 * Listsacccount_link_request rows pending across every server this user
 * is currently granted admin of. lab_admin sees all-lab pending here
 * too (backend filters server-scope when role != lab_admin).
 *
 * v2 设计:收件/审批型(C 型)骨架 —— 紧凑 .cl-pagebar 页头,无大 hero;
 * 待审条目卡片化(--c-warning 色轨 = 待审身份),力气花在条目本身。
 * 数据获取 / 审批逻辑与 v1 完全一致,改动仅限视觉层。
 */
import { onMounted, ref } from 'vue';
import { NButton, NModal, useMessage } from 'naive-ui';
import { Check, Inbox, RefreshCw, X } from 'lucide-vue-next';
import { extractDetail } from '@/utils/extractDetail';
import { timeAgo, formatDateTime } from '@/utils/timeago';

import AppLayout from '@/layouts/AppLayout.vue';
import CleanEmpty from '@/components/CleanEmpty.vue';
import AccountLinkRequestReview from '@/components/admin/AccountLinkRequestReview.vue';
import * as alrApi from '@/api/accountLinkRequests';
import type { AccountLinkRequestRead } from '@/api/accountLinkRequests';
import * as paApi from '@/api/physicalAccounts';
import * as serversApi from '@/api/servers';
import type { PhysicalAccountRead } from '@/api/physicalAccounts';
import type { ServerRead } from '@/api/servers';

const message = useMessage();

const pending = ref<AccountLinkRequestRead[]>([]);
const pas = ref<Map<number, PhysicalAccountRead>>(new Map());
const servers = ref<Map<number, ServerRead>>(new Map());
const loading = ref(false);
const reviewRow = ref<AccountLinkRequestRead | null>(null);

async function refresh(): Promise<void> {
  loading.value = true;
  try {
    const [rows, srv] = await Promise.all([alrApi.listPending(), serversApi.listServers()]);
    pending.value = rows;
    servers.value = new Map(srv.map((s) => [s.id, s]));

    const paIds = [...new Set(rows.map((r) => r.physical_account_id))];
    const fetched = await Promise.all(paIds.map((id) => paApi.getPa(id).catch(() => null)));
    const next = new Map<number, PhysicalAccountRead>();
    for (const pa of fetched) {
      if (pa !== null) next.set(pa.id, pa);
    }
    pas.value = next;
  } catch (err) {
    message.error(extractDetail(err, '加载待审申请失败'));
  } finally {
    loading.value = false;
  }
}

onMounted(refresh);

function paLabel(paId: number): string {
  const pa = pas.value.get(paId);
  if (pa === undefined) return `PA #${paId}`;
  const s = servers.value.get(pa.server_id);
  const hostname = s?.display_name ?? s?.hostname ?? `server #${pa.server_id}`;
  return `${pa.linux_username} @ ${hostname}`;
}

/** 纯展示:申请人标签(列表行只有 user id,无 username)。 */
function requesterLabel(row: AccountLinkRequestRead): string {
  return `user #${row.requester_user_id}`;
}

/** 纯展示:头像块首字母 —— 取申请人标签的第一个字母数字字符。 */
function requesterInitial(row: AccountLinkRequestRead): string {
  const ch = requesterLabel(row).match(/[a-z0-9]/i)?.[0];
  return (ch ?? '#').toUpperCase();
}

/** 纯展示:入场 stagger 延迟(封顶,长列表后段不再拖)。 */
function staggerDelay(index: number): string {
  return `${(0.06 + Math.min(index, 8) * 0.05).toFixed(2)}s`;
}

async function quickApprove(row: AccountLinkRequestRead): Promise<void> {
  try {
    await alrApi.approve(row.id);
    message.success('已批准');
    await refresh();
  } catch (err) {
    message.error(extractDetail(err, '批准失败'));
  }
}

async function quickDeny(row: AccountLinkRequestRead): Promise<void> {
  try {
    await alrApi.deny(row.id, { decision_note: 'denied' });
    message.success('已拒绝');
    await refresh();
  } catch (err) {
    message.error(extractDetail(err, '拒绝失败'));
  }
}
</script>

<template>
  <AppLayout>
    <div class="page">
      <header class="cl-pagebar cl-enter">
        <div class="cl-pagebar-icon">
          <Inbox :size="20" :stroke-width="1.75" />
        </div>
        <div class="cl-pagebar-body">
          <h1 class="cl-pagebar-title">
            关联申请
            <span class="cl-pagebar-meta">
              <span v-if="pending.length > 0" class="pending-dot cl-pulse" aria-hidden="true" />
              <span class="tabular" :class="{ 'pending-hot': pending.length > 0 }"
                >{{ pending.length }} 待审</span
              >
            </span>
          </h1>
          <p class="cl-pagebar-sub">
            审你管理的 server 上的账号绑定申请;「批准」会把申请人的公钥推到 authorized_keys。
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
        v-if="pending.length === 0 && !loading"
        class="empty-wrap cl-enter"
        style="--cl-delay: 0.08s"
      >
        <CleanEmpty
          :icon="Inbox"
          title="收件箱为空"
          description="没有待审的账号绑定申请。"
          compact
        />
      </div>
      <ul v-else class="inbox-list">
        <li v-if="loading && pending.length === 0" class="list-loading">
          <RefreshCw :size="13" :stroke-width="1.75" class="list-loading-icon" aria-hidden="true" />
          正在加载待审申请…
        </li>
        <li
          v-for="(row, i) in pending"
          :key="row.id"
          class="req-card cl-enter"
          :style="{ '--cl-delay': staggerDelay(i) }"
        >
          <span class="req-rail" aria-hidden="true" />
          <span class="req-avatar font-mono" aria-hidden="true">{{ requesterInitial(row) }}</span>
          <div class="req-main">
            <div class="req-top">
              <span class="req-target font-mono tabular">{{
                paLabel(row.physical_account_id)
              }}</span>
              <span class="req-id font-mono tabular">#{{ row.id }}</span>
            </div>
            <div class="req-meta">
              <span class="req-user font-mono tabular">{{ requesterLabel(row) }}</span>
              <span class="req-sep" aria-hidden="true">·</span>
              <span class="req-note" :title="row.request_note ?? undefined">{{
                row.request_note ?? '—'
              }}</span>
            </div>
          </div>
          <div class="req-side">
            <time
              class="req-time font-mono tabular nowrap"
              :datetime="row.created_at"
              :title="formatDateTime(row.created_at)"
              >{{ timeAgo(row.created_at) }}</time
            >
            <div class="req-actions">
              <NButton size="tiny" @click="reviewRow = row">审核</NButton>
              <NButton size="tiny" type="primary" @click="quickApprove(row)">
                <template #icon>
                  <Check :size="12" :stroke-width="2.25" />
                </template>
                批准
              </NButton>
              <NButton size="tiny" quaternary type="error" @click="quickDeny(row)">
                <template #icon>
                  <X :size="12" :stroke-width="2.25" />
                </template>
                拒绝
              </NButton>
            </div>
          </div>
        </li>
      </ul>

      <NModal
        :show="reviewRow !== null"
        preset="card"
        title="审核申请"
        style="max-width: 720px"
        :mask-closable="false"
        @close="reviewRow = null"
      >
        <AccountLinkRequestReview
          v-if="reviewRow !== null"
          :request="reviewRow"
          @done="((reviewRow = null), refresh())"
          @cancel="reviewRow = null"
        />
      </NModal>
    </div>
  </AppLayout>
</template>

<style scoped>
/* .page 布局(padding / max-width)来自 main.css 共享类,不在此重复定义。 */

/* ── 页头 meta:待审数 ─────────────────────────────── */
.pending-dot {
  flex: none;
  width: 8px;
  height: 8px;
  border-radius: var(--radius-full);
  background: var(--c-warning);
  --cl-pulse-color: color-mix(in srgb, var(--c-warning) 45%, transparent);
}
.pending-hot {
  color: var(--c-warning);
  font-weight: 600;
}

/* ── 待审条目卡片 ─────────────────────────────────── */
.inbox-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}
.req-card {
  position: relative;
  display: flex;
  align-items: center;
  gap: var(--space-4);
  padding: var(--space-4) var(--space-5);
  padding-left: calc(var(--space-5) + 3px);
  background: var(--c-bg-elevated);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-lg);
  overflow: hidden;
  transition:
    border-color 0.16s ease,
    background 0.16s ease,
    box-shadow 0.16s ease;
}
.req-card:hover {
  border-color: color-mix(in srgb, var(--c-accent) 55%, var(--c-border-subtle));
  background: color-mix(in srgb, var(--c-accent) 3%, var(--c-bg-elevated));
  box-shadow: var(--shadow-sm);
}

/* 左侧竖向色轨 —— 待审身份(--c-warning)。 */
.req-rail {
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 3px;
  background: var(--c-warning);
}

/* 首字母头像块(纯展示)。 */
.req-avatar {
  flex: none;
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--c-text-secondary);
  background: var(--c-bg-sunken);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-md);
}

.req-main {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}
.req-top {
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
  min-width: 0;
}
.req-target {
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--c-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.req-id {
  flex: none;
  font-size: var(--text-xs);
  color: var(--c-text-tertiary);
}
.req-meta {
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
  min-width: 0;
  font-size: var(--text-xs);
  line-height: var(--leading-snug);
}
.req-user {
  flex: none;
  color: var(--c-text-secondary);
}
.req-sep {
  flex: none;
  color: var(--c-text-tertiary);
}
.req-note {
  color: var(--c-text-tertiary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.req-side {
  flex: none;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: var(--space-2);
}
.req-time {
  font-size: var(--text-xs);
  color: var(--c-text-tertiary);
}
.req-actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

/* ── 空态 / 加载占位 ─────────────────────────────── */
.empty-wrap {
  background: var(--c-bg-elevated);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-lg);
}
.list-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  padding: var(--space-8);
  font-size: var(--text-sm);
  color: var(--c-text-tertiary);
  background: var(--c-bg-elevated);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-lg);
}
.list-loading-icon {
  animation: cl-spin 1s linear infinite;
}

/* 窄屏:右侧时间 + 操作折到下一行,卡片仍然完整可用。 */
@media (max-width: 720px) {
  .req-card {
    flex-wrap: wrap;
  }
  .req-side {
    width: 100%;
    flex-direction: row;
    align-items: center;
    justify-content: space-between;
  }
}

@media (prefers-reduced-motion: reduce) {
  .list-loading-icon {
    animation: none;
  }
  .req-card,
  .req-card:hover {
    transition: none;
    box-shadow: none;
  }
}
</style>

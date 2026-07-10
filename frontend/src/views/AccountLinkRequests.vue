<script setup lang="ts">
/**
 * AccountLinkRequests inbox — toggles between "my requests" + (for
 * lab_admin) "pending in lab". Both tabs share the same request-card
 * anatomy; admin gets review / approve / deny buttons on pending rows.
 *
 * Visual (design v2, C 型收件/审批页): compact .cl-pagebar instead of a
 * boxed hero; each request is a card with a status rail + breathing dot
 * (pending), an initial-avatar block, mono identities and timeago
 * timestamps. Decision modal stays close to Login's button rhythm:
 * ghost Cancel + primary Approve / red Deny.
 */

import { computed, onMounted, ref } from 'vue';
import { NButton, NInput, NModal, NTabPane, NTabs, useDialog, useMessage } from 'naive-ui';
import { extractDetail } from '@/utils/extractDetail';
import { Check, Inbox, MailQuestion, RefreshCw, X } from 'lucide-vue-next';

import AppLayout from '@/layouts/AppLayout.vue';
import CleanEmpty from '@/components/CleanEmpty.vue';
import AccountLinkRequestReview from '@/components/admin/AccountLinkRequestReview.vue';
import { useAuthStore } from '@/stores/auth';
import { timeAgo, formatDateTime } from '@/utils/timeago';
import * as alrApi from '@/api/accountLinkRequests';
import type { AccountLinkRequestRead, AlrStatus } from '@/api/accountLinkRequests';

const auth = useAuthStore();
const message = useMessage();
const dialog = useDialog();

const mine = ref<AccountLinkRequestRead[]>([]);
const pending = ref<AccountLinkRequestRead[]>([]);
const needsPush = ref<AccountLinkRequestRead[]>([]);
const loading = ref(false);
const tab = ref<'mine' | 'pending' | 'needs-push'>('mine');

const decisionOpen = ref(false);
const decisionMode = ref<'approve' | 'deny'>('approve');
const decisionRow = ref<AccountLinkRequestRead | null>(null);
const decisionNote = ref('');
const decisionSubmitting = ref(false);

// K-5 — rich review modal (signal-bundled approve/deny)
const reviewRow = ref<AccountLinkRequestRead | null>(null);
const retryingId = ref<number | null>(null);

async function refresh(): Promise<void> {
  loading.value = true;
  try {
    mine.value = await alrApi.listMine();
    if (auth.isLabAdmin) {
      const [pendingRows, needsPushRows] = await Promise.all([
        alrApi.listPending(),
        alrApi.listNeedsPush(),
      ]);
      pending.value = pendingRows;
      needsPush.value = needsPushRows;
    } else {
      pending.value = [];
      needsPush.value = [];
    }
  } catch (err) {
    message.error(extractDetail(err, '加载失败'));
  } finally {
    loading.value = false;
  }
}

onMounted(refresh);

/* ── 纯展示 helpers(不触碰任何数据/逻辑) ────────────────────── */

/** status enum → 中文标签。 */
const STATUS_LABEL: Record<AlrStatus, string> = {
  pending: '待处理',
  approved: '已批准',
  denied: '已拒绝',
  withdrawn: '已撤回',
};

/** 当前用户 username 的首字母(头像块用)。 */
const myInitial = computed(() => {
  const ch = (auth.user?.username ?? '').trim().charAt(0);
  return ch === '' ? 'U' : ch.toUpperCase();
});

/**
 * 申请人头像块文本:行数据里只有 requester_user_id,拿得到 username 的
 * 只有「自己」——是自己就取首字母,否则退化为用户编号数字。
 */
function requesterBadge(row: AccountLinkRequestRead): string {
  if (auth.user !== null && row.requester_user_id === auth.user.id) {
    return myInitial.value;
  }
  return String(row.requester_user_id);
}

/** stagger 入场延迟(封顶,避免长列表拖沓)。 */
function enterDelay(i: number): string {
  return `${Math.min(i * 50, 400)}ms`;
}

/* ── 操作(与 v1 完全一致) ──────────────────────────────────── */

function openDecision(row: AccountLinkRequestRead, mode: 'approve' | 'deny'): void {
  decisionRow.value = row;
  decisionMode.value = mode;
  decisionNote.value = '';
  decisionOpen.value = true;
}

async function submitDecision(): Promise<void> {
  const row = decisionRow.value;
  if (row === null) return;
  decisionSubmitting.value = true;
  try {
    if (decisionMode.value === 'approve') {
      await alrApi.approve(row.id, { decision_note: decisionNote.value || null });
    } else {
      await alrApi.deny(row.id, { decision_note: decisionNote.value || null });
    }
    message.success(decisionMode.value === 'approve' ? '已批准。' : '已拒绝。');
    decisionOpen.value = false;
    await refresh();
  } catch (err) {
    message.error(extractDetail(err, '操作失败'));
  } finally {
    decisionSubmitting.value = false;
  }
}

async function retryPushRow(row: AccountLinkRequestRead): Promise<void> {
  retryingId.value = row.id;
  try {
    const resp = await alrApi.retryPush(row.id);
    const outcome = resp.key_push_outcome;
    if (outcome.ok === true) {
      message.success(
        outcome.already_active === true ? '这把 key 已经生效。' : '已重新推送 SSH 公钥。',
      );
    } else {
      message.warning(`推送仍失败:${String(outcome.error ?? 'agent 未返回成功')}`);
    }
    await refresh();
  } catch (err) {
    message.error(extractDetail(err, '重试推送失败'));
  } finally {
    retryingId.value = null;
  }
}

function withdrawRow(row: AccountLinkRequestRead): void {
  dialog.warning({
    title: '撤回此申请?',
    content: '该申请将变为 status=withdrawn,管理员将不再对其进行任何处理。',
    positiveText: '撤回',
    negativeText: '保留',
    onPositiveClick: async () => {
      try {
        await alrApi.withdraw(row.id);
        message.success('已撤回。');
        await refresh();
      } catch (err) {
        message.error(extractDetail(err, '撤回失败'));
      }
    },
  });
}
</script>

<template>
  <AppLayout>
    <div class="page">
      <header class="cl-pagebar cl-enter">
        <div class="cl-pagebar-icon">
          <MailQuestion :size="20" :stroke-width="1.75" />
        </div>
        <div class="cl-pagebar-body">
          <h1 class="cl-pagebar-title">
            账号关联申请
            <span class="cl-pagebar-meta">
              <span class="font-mono tabular cl-num">{{ mine.length }}</span>
              <span>条我的申请</span>
              <template v-if="auth.isLabAdmin">
                <span aria-hidden="true">·</span>
                <span class="font-mono tabular cl-num">{{ pending.length }}</span>
                <span>条待处理</span>
                <span aria-hidden="true">·</span>
                <span class="font-mono tabular cl-num">{{ needsPush.length }}</span>
                <span>条需重推 key</span>
              </template>
            </span>
          </h1>
          <p class="cl-pagebar-sub">认领 Linux 账号的申请收件箱,批准后自动推送 SSH 公钥。</p>
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

      <div class="tabs-wrap cl-enter" style="--cl-delay: 0.08s">
        <NTabs v-model:value="tab" type="line" animated size="small">
          <NTabPane :name="'mine'" :tab="`我的申请 (${mine.length})`">
            <div v-if="mine.length === 0 && !loading" class="empty-wrap">
              <CleanEmpty
                :icon="Inbox"
                title="暂无申请"
                description="当你申请认领某个 Linux 账号时,申请会显示在这里。"
                compact
              />
            </div>
            <div v-else class="req-list">
              <template v-if="loading && mine.length === 0">
                <div class="req-skel" aria-hidden="true"></div>
                <div class="req-skel" aria-hidden="true"></div>
              </template>
              <article
                v-for="(row, i) in mine"
                :key="row.id"
                class="req-card cl-enter cl-nudge"
                :class="`is-${row.status}`"
                :style="{ '--cl-delay': enterDelay(i) }"
              >
                <span class="req-rail" aria-hidden="true"></span>
                <span class="req-avatar font-mono" aria-hidden="true">{{ myInitial }}</span>
                <div class="req-body">
                  <div class="req-line">
                    <span class="req-target font-mono">{{ auth.user?.username ?? 'me' }}</span>
                    <span class="req-arrow" aria-hidden="true">→</span>
                    <span class="req-target font-mono tabular"
                      >PA #{{ row.physical_account_id }}</span
                    >
                    <span class="req-id font-mono tabular">#{{ row.id }}</span>
                  </div>
                  <p class="req-note" :class="{ 'is-blank': row.request_note === null }">
                    {{ row.request_note ?? '(未填备注)' }}
                  </p>
                  <div class="req-meta">
                    <span class="req-status">
                      <i
                        class="req-dot"
                        :class="{ 'cl-pulse': row.status === 'pending' }"
                        aria-hidden="true"
                      ></i>
                      {{ STATUS_LABEL[row.status] }}
                      <span class="req-status-token font-mono">{{ row.status }}</span>
                    </span>
                    <span class="req-sep" aria-hidden="true">·</span>
                    <span class="req-time" :title="formatDateTime(row.created_at)">
                      提交于 {{ timeAgo(row.created_at) }}
                    </span>
                    <template v-if="row.decided_at !== null">
                      <span class="req-sep" aria-hidden="true">·</span>
                      <span class="req-time" :title="formatDateTime(row.decided_at)">
                        决定于 {{ timeAgo(row.decided_at) }}
                      </span>
                      <span v-if="row.decision_note" class="req-dnote">
                        「{{ row.decision_note }}」
                      </span>
                    </template>
                  </div>
                </div>
                <div class="req-actions">
                  <NButton
                    v-if="row.status === 'pending'"
                    size="tiny"
                    quaternary
                    @click="withdrawRow(row)"
                  >
                    撤回
                  </NButton>
                </div>
              </article>
            </div>
          </NTabPane>
          <NTabPane
            v-if="auth.isLabAdmin"
            :name="'pending'"
            :tab="`lab 内待处理 (${pending.length})`"
          >
            <div v-if="pending.length === 0 && !loading" class="empty-wrap">
              <CleanEmpty
                :icon="Inbox"
                title="收件箱为空"
                description="当用户申请账号关联时,会显示在这里。"
                compact
              />
            </div>
            <div v-else class="req-list">
              <template v-if="loading && pending.length === 0">
                <div class="req-skel" aria-hidden="true"></div>
                <div class="req-skel" aria-hidden="true"></div>
              </template>
              <article
                v-for="(row, i) in pending"
                :key="row.id"
                class="req-card cl-enter cl-nudge"
                :class="`is-${row.status}`"
                :style="{ '--cl-delay': enterDelay(i) }"
              >
                <span class="req-rail" aria-hidden="true"></span>
                <span class="req-avatar font-mono" aria-hidden="true">{{
                  requesterBadge(row)
                }}</span>
                <div class="req-body">
                  <div class="req-line">
                    <span class="req-target font-mono tabular"
                      >user #{{ row.requester_user_id }}</span
                    >
                    <span class="req-arrow" aria-hidden="true">→</span>
                    <span class="req-target font-mono tabular"
                      >PA #{{ row.physical_account_id }}</span
                    >
                    <span class="req-id font-mono tabular">#{{ row.id }}</span>
                  </div>
                  <p class="req-note" :class="{ 'is-blank': row.request_note === null }">
                    {{ row.request_note ?? '(未填备注)' }}
                  </p>
                  <div class="req-meta">
                    <span class="req-status">
                      <i
                        class="req-dot"
                        :class="{ 'cl-pulse': row.status === 'pending' }"
                        aria-hidden="true"
                      ></i>
                      {{ STATUS_LABEL[row.status] }}
                      <span class="req-status-token font-mono">{{ row.status }}</span>
                    </span>
                    <span class="req-sep" aria-hidden="true">·</span>
                    <span class="req-time" :title="formatDateTime(row.created_at)">
                      提交于 {{ timeAgo(row.created_at) }}
                    </span>
                  </div>
                </div>
                <div class="req-actions">
                  <NButton size="tiny" @click="reviewRow = row">审核</NButton>
                  <NButton size="tiny" type="primary" @click="openDecision(row, 'approve')">
                    <template #icon>
                      <Check :size="12" :stroke-width="2.25" />
                    </template>
                    批准
                  </NButton>
                  <NButton size="tiny" quaternary type="error" @click="openDecision(row, 'deny')">
                    <template #icon>
                      <X :size="12" :stroke-width="2.25" />
                    </template>
                    拒绝
                  </NButton>
                </div>
              </article>
            </div>
          </NTabPane>
          <NTabPane
            v-if="auth.isLabAdmin"
            :name="'needs-push'"
            :tab="`需重推 key (${needsPush.length})`"
          >
            <div v-if="needsPush.length === 0 && !loading" class="empty-wrap">
              <CleanEmpty
                :icon="Inbox"
                title="没有需要重推的申请"
                description="已批准但 key 未准备好的申请会出现在这里。"
                compact
              />
            </div>
            <div v-else class="req-list">
              <template v-if="loading && needsPush.length === 0">
                <div class="req-skel" aria-hidden="true"></div>
                <div class="req-skel" aria-hidden="true"></div>
              </template>
              <article
                v-for="(row, i) in needsPush"
                :key="row.id"
                class="req-card cl-enter cl-nudge is-approved"
                :style="{ '--cl-delay': enterDelay(i) }"
              >
                <span class="req-rail" aria-hidden="true"></span>
                <span class="req-avatar font-mono" aria-hidden="true">{{
                  requesterBadge(row)
                }}</span>
                <div class="req-body">
                  <div class="req-line">
                    <span class="req-target font-mono tabular"
                      >user #{{ row.requester_user_id }}</span
                    >
                    <span class="req-arrow" aria-hidden="true">→</span>
                    <span class="req-target font-mono tabular"
                      >PA #{{ row.physical_account_id }}</span
                    >
                    <span class="req-id font-mono tabular">#{{ row.id }}</span>
                  </div>
                  <p class="req-note">已批准,但当前活跃 SSH 公钥还没有确认写入该 Linux 账号。</p>
                  <div class="req-meta">
                    <span class="req-status">
                      <i class="req-dot" aria-hidden="true"></i>
                      需重推 key
                      <span class="req-status-token font-mono">needs_push</span>
                    </span>
                    <span class="req-sep" aria-hidden="true">·</span>
                    <span class="req-time" :title="formatDateTime(row.updated_at)">
                      更新于 {{ timeAgo(row.updated_at) }}
                    </span>
                  </div>
                </div>
                <div class="req-actions">
                  <NButton
                    size="tiny"
                    type="primary"
                    :loading="retryingId === row.id"
                    @click="retryPushRow(row)"
                  >
                    <template #icon>
                      <RefreshCw :size="12" :stroke-width="2.25" />
                    </template>
                    重试推 key
                  </NButton>
                </div>
              </article>
            </div>
          </NTabPane>
        </NTabs>
      </div>

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

      <NModal
        v-model:show="decisionOpen"
        preset="card"
        :title="decisionMode === 'approve' ? '批准申请' : '拒绝申请'"
        style="max-width: 28rem"
      >
        <p class="modal-blurb">
          {{
            decisionMode === 'approve'
              ? '批准后,agent 会被要求把申请人的活跃 SSH 公钥推送到该 PA 的 authorized_keys 中。'
              : '拒绝将结束此申请。如有需要,申请人之后可以重新提交。'
          }}
        </p>
        <NInput
          v-model:value="decisionNote"
          type="textarea"
          :rows="3"
          placeholder="可选:决定备注(将记入审计日志)"
        />
        <div class="modal-actions">
          <NButton @click="decisionOpen = false">取消</NButton>
          <NButton
            :type="decisionMode === 'approve' ? 'primary' : 'error'"
            :loading="decisionSubmitting"
            @click="submitDecision"
          >
            {{ decisionMode === 'approve' ? '批准' : '拒绝' }}
          </NButton>
        </div>
      </NModal>
    </div>
  </AppLayout>
</template>

<style scoped>
/* 页面外框沿用全局 .page;页头由共享 .cl-pagebar 负责(无大 hero)。 */

/* ── 申请条目卡片(本页主角) ───────────────────────────────── */
.req-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin-top: var(--space-3);
}

.req-card {
  position: relative;
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  background: var(--c-bg-elevated);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-lg);
  overflow: hidden;
}
.req-card:hover {
  border-color: color-mix(in srgb, var(--c-accent) 28%, var(--c-border-subtle));
  background: color-mix(in srgb, var(--c-accent) 2%, var(--c-bg-elevated));
  box-shadow: var(--shadow-sm);
}

/* 状态基调:竖向色轨 + 状态点共用一个 --req-tone。 */
.req-card.is-pending {
  --req-tone: var(--c-warning);
}
.req-card.is-approved {
  --req-tone: var(--c-success);
}
.req-card.is-denied {
  --req-tone: var(--c-danger);
}
.req-card.is-withdrawn {
  --req-tone: var(--c-text-disabled);
}

.req-rail {
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 3px;
  background: var(--req-tone, var(--c-border-default));
  opacity: 0.9;
}

/* 首字母小头像块 — 纯展示,品牌蓝 color-mix 底。 */
.req-avatar {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  font-size: var(--text-xs);
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: var(--c-accent);
  background: color-mix(in srgb, var(--c-accent) 10%, var(--c-bg-elevated));
  border: 1px solid color-mix(in srgb, var(--c-accent) 25%, transparent);
  border-radius: var(--radius-md);
}

.req-body {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.req-line {
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
  flex-wrap: wrap;
  font-size: var(--text-sm);
}
.req-target {
  font-weight: 500;
  color: var(--c-text-primary);
  white-space: nowrap;
}
.req-arrow {
  color: var(--c-text-tertiary);
  font-size: var(--text-xs);
}
.req-id {
  margin-left: auto;
  font-size: var(--text-xs);
  color: var(--c-text-tertiary);
}

.req-note {
  margin: 0;
  font-size: var(--text-sm);
  color: var(--c-text-secondary);
  line-height: var(--leading-snug);
  overflow: hidden;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}
.req-note.is-blank {
  color: var(--c-text-disabled);
}

.req-meta {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
  font-size: var(--text-xs);
  color: var(--c-text-tertiary);
}
.req-status {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  color: var(--c-text-secondary);
  font-weight: 500;
}
.req-dot {
  flex: none;
  width: 6px;
  height: 6px;
  border-radius: var(--radius-full);
  background: var(--req-tone, var(--c-border-strong));
  /* pending 的呼吸圈用同一基调着色(.cl-pulse 读取此变量)。 */
  --cl-pulse-color: color-mix(in srgb, var(--req-tone, var(--c-accent)) 45%, transparent);
}
.req-status-token {
  font-weight: 400;
  color: var(--c-text-disabled);
}
.req-sep {
  color: var(--c-text-disabled);
}
.req-time {
  font-variant-numeric: tabular-nums;
}
.req-dnote {
  color: var(--c-text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 24rem;
}

.req-actions {
  flex: none;
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

/* 首次加载骨架 — 复用全局 @keyframes cl-sheen。 */
.req-skel {
  height: 72px;
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-lg);
  background: linear-gradient(
    100deg,
    var(--c-bg-elevated) 40%,
    var(--c-bg-sunken) 50%,
    var(--c-bg-elevated) 60%
  );
  background-size: 200% 100%;
  animation: cl-sheen 1.4s linear infinite;
}
@media (prefers-reduced-motion: reduce) {
  .req-skel {
    animation: none;
    background: var(--c-bg-sunken);
  }
}

/* ── 空态:带边框卡片包住 CleanEmpty ───────────────────────── */
.empty-wrap {
  margin-top: var(--space-3);
  padding: var(--space-6) var(--space-4);
  background: var(--c-bg-elevated);
  border: 1px dashed var(--c-border-default);
  border-radius: var(--radius-lg);
}

/* ── 弹窗(逻辑不变,仅原有外观) ───────────────────────────── */
.modal-blurb {
  margin: 0 0 var(--space-3);
  font-size: var(--text-sm);
  color: var(--c-text-secondary);
  line-height: var(--leading-snug);
}
.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
  margin-top: var(--space-3);
}
</style>

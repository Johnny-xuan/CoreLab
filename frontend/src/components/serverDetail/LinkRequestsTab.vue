<script setup lang="ts">
/**
 * LinkRequestsTab — Server Detail / link-requests (Phase 9 P9-14,
 * docs/07 §3.3 line 315 server-scoped).
 *
 * Lists pending AccountLinkRequest rows whose physical_account
 * belongs to this server, with approve / deny actions.
 *
 * Backend ``GET /account-link-requests`` returns the whole lab
 * (lab_admin gated). We filter client-side by cross-referencing
 * the server's PhysicalAccount list — backend ergonomics are kept
 * unchanged for now (added a server_id query param would be a
 * Phase 10 FU if usage warrants it).
 *
 * Approve / deny is also lab_admin gated server-side; the UI mirrors
 * that with ``canEdit`` so non-admins see a read-only view.
 */

import { computed, h, onMounted, ref, watch } from 'vue';
import {
  NAlert,
  NButton,
  NDataTable,
  NInput,
  NModal,
  NSpace,
  NTag,
  useMessage,
  type DataTableColumns,
} from 'naive-ui';
import { extractDetail } from '@/utils/extractDetail';

import * as alrApi from '@/api/accountLinkRequests';
import type { AccountLinkRequestRead } from '@/api/accountLinkRequests';
import * as paApi from '@/api/physicalAccounts';
import type { PhysicalAccountRead } from '@/api/physicalAccounts';

const props = defineProps<{
  serverId: number;
  canEdit: boolean;
}>();

const message = useMessage();

const allPending = ref<AccountLinkRequestRead[]>([]);
const pas = ref<PhysicalAccountRead[]>([]);
const loading = ref(false);

type DecisionMode = 'approve' | 'deny';
const modalOpen = ref(false);
const modalMode = ref<DecisionMode>('approve');
const modalRow = ref<AccountLinkRequestRead | null>(null);
const modalNote = ref('');
const modalBusy = ref(false);

async function reload(): Promise<void> {
  loading.value = true;
  try {
    const [pending, paList] = await Promise.all([
      alrApi.listPending().catch(() => [] as AccountLinkRequestRead[]),
      paApi.listPas(props.serverId).catch(() => [] as PhysicalAccountRead[]),
    ]);
    allPending.value = pending;
    pas.value = paList;
  } catch (err) {
    message.error(extractDetail(err, '加载申请失败'));
  } finally {
    loading.value = false;
  }
}

const paIdSet = computed(() => new Set(pas.value.map((p) => p.id)));

const scoped = computed<AccountLinkRequestRead[]>(() =>
  allPending.value.filter((r) => paIdSet.value.has(r.physical_account_id)),
);

function paLabelOf(paId: number): string {
  const pa = pas.value.find((p) => p.id === paId);
  return pa ? pa.linux_username : `pa#${paId}`;
}

function openDecision(row: AccountLinkRequestRead, mode: DecisionMode): void {
  modalRow.value = row;
  modalMode.value = mode;
  modalNote.value = '';
  modalOpen.value = true;
}

async function submitDecision(): Promise<void> {
  if (!modalRow.value) return;
  modalBusy.value = true;
  try {
    const id = modalRow.value.id;
    const note = modalNote.value.trim() || null;
    if (modalMode.value === 'approve') {
      await alrApi.approve(id, { decision_note: note });
      message.success(`申请 #${id} 已通过`);
    } else {
      await alrApi.deny(id, { decision_note: note });
      message.success(`申请 #${id} 已拒绝`);
    }
    modalOpen.value = false;
    await reload();
  } catch (err) {
    message.error(extractDetail(err, '处理失败'));
  } finally {
    modalBusy.value = false;
  }
}

const columns = computed<DataTableColumns<AccountLinkRequestRead>>(() => [
  {
    title: 'ID',
    key: 'id',
    width: 70,
    render: (row) => `#${row.id}`,
  },
  {
    title: '申请时间',
    key: 'created_at',
    width: 180,
  },
  {
    title: '申请人',
    key: 'requester_user_id',
    width: 120,
    render: (row) => `user #${row.requester_user_id}`,
  },
  {
    title: '目标账户',
    key: 'physical_account_id',
    width: 180,
    render: (row) => paLabelOf(row.physical_account_id),
  },
  {
    title: '理由',
    key: 'request_note',
    render: (row) => row.request_note ?? '—',
  },
  {
    title: '状态',
    key: 'status',
    width: 100,
    render: (row) =>
      h(
        NTag,
        { size: 'small', type: row.status === 'pending' ? 'warning' : 'default' },
        { default: () => row.status },
      ),
  },
]);

watch(
  () => props.serverId,
  () => {
    void reload();
  },
);

onMounted(() => {
  void reload();
});

defineExpose({ reload });
</script>

<template>
  <div class="lr-tab">
    <header class="lr-head">
      <p class="lr-hint">
        本 server 范围内的 AccountLinkRequest 申请。审批仅 lab_admin。批准后系统会发出
        <code>notification.link.prepared</code> 给申请人。
      </p>
      <NButton size="small" @click="reload">刷新</NButton>
    </header>

    <NAlert v-if="loading" type="default" :show-icon="false">加载中…</NAlert>
    <NAlert v-else-if="scoped.length === 0" type="default" :show-icon="false">
      本 server 没有 pending 的 link request。
    </NAlert>
    <NDataTable
      v-else
      :columns="columns"
      :data="scoped"
      :bordered="true"
      size="small"
      :row-key="(row: AccountLinkRequestRead) => row.id"
    />

    <section v-if="canEdit && scoped.length > 0" class="lr-actions">
      <article v-for="row in scoped" :key="`act-${row.id}`" class="lr-row">
        <span class="lr-row-label">#{{ row.id }} ({{ paLabelOf(row.physical_account_id) }})</span>
        <NSpace>
          <NButton size="small" type="primary" @click="openDecision(row, 'approve')">
            通过
          </NButton>
          <NButton size="small" @click="openDecision(row, 'deny')">拒绝</NButton>
        </NSpace>
      </article>
    </section>

    <NModal
      v-model:show="modalOpen"
      preset="dialog"
      :title="modalMode === 'approve' ? '通过申请' : '拒绝申请'"
      :positive-text="modalBusy ? '处理中…' : '确认'"
      negative-text="取消"
      :positive-button-props="{ disabled: modalBusy }"
      @positive-click="submitDecision"
      @negative-click="modalOpen = false"
    >
      <div v-if="modalRow" class="lr-modal-body">
        <p>
          申请 #{{ modalRow.id }} —
          <NTag size="small" :type="modalMode === 'approve' ? 'success' : 'warning'">
            {{ modalMode === 'approve' ? '通过' : '拒绝' }}
          </NTag>
        </p>
        <NInput
          v-model:value="modalNote"
          type="textarea"
          placeholder="可选:审批备注"
          :maxlength="500"
          show-count
          :autosize="{ minRows: 3, maxRows: 6 }"
        />
      </div>
    </NModal>
  </div>
</template>

<style scoped>
.lr-tab {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}
.lr-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-3);
}
.lr-hint {
  font-size: var(--text-sm);
  color: var(--c-text-secondary);
  margin: 0;
}
.lr-actions {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-4);
  background: var(--c-bg-elevated);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-md);
}
.lr-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  font-size: var(--text-sm);
}
.lr-row-label {
  font-family: var(--font-mono, ui-monospace, monospace);
}
.lr-modal-body {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}
</style>

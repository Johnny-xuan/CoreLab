<script setup lang="ts">
import { computed, h } from 'vue';
import { NAlert, NButton, NDataTable, NTag, type DataTableColumns } from 'naive-ui';
import { KeyRound, RefreshCw, ScanSearch } from 'lucide-vue-next';

import CleanEmpty from '@/components/CleanEmpty.vue';
import type {
  AuthorizedKeyInventoryEntry,
  AuthorizedKeyEntryStatus,
  AuthorizedKeyReadbackResponse,
} from '@/api/physicalAccounts';

const props = defineProps<{
  entries: AuthorizedKeyInventoryEntry[];
  loading?: boolean;
  readbacks?: Record<number, AuthorizedKeyReadbackResponse>;
  readbackLoadingPaId?: number | null;
}>();

const emit = defineEmits<{
  retry: [entry: AuthorizedKeyInventoryEntry];
  readback: [entry: AuthorizedKeyInventoryEntry];
}>();

function statusLabel(status: AuthorizedKeyEntryStatus): string {
  if (status === 'active') return '已推送';
  if (status === 'removed') return '已撤销';
  return '待重推';
}

function statusType(status: AuthorizedKeyEntryStatus): 'success' | 'warning' | 'default' {
  if (status === 'active') return 'success';
  if (status === 'push_failed') return 'warning';
  return 'default';
}

function displayUser(entry: AuthorizedKeyInventoryEntry, role: 'for' | 'by' | 'removed'): string {
  if (role === 'removed') {
    return entry.removed_by_user_id === null
      ? '—'
      : `${entry.removed_by_display_name} @${entry.removed_by_username}`;
  }
  return role === 'for'
    ? `${entry.pushed_for_display_name} @${entry.pushed_for_username}`
    : `${entry.pushed_by_display_name} @${entry.pushed_by_username}`;
}

function shortTime(value: string | null): string {
  return value === null ? '—' : new Date(value).toLocaleString();
}

const activeCount = computed(
  () => props.entries.filter((entry) => entry.status === 'active').length,
);
const retryCount = computed(() => props.entries.filter((entry) => entry.can_retry).length);
const readbackList = computed(() => Object.values(props.readbacks ?? {}));

function readbackFor(entry: AuthorizedKeyInventoryEntry): AuthorizedKeyReadbackResponse | null {
  return props.readbacks?.[entry.physical_account_id] ?? null;
}

function hostPresenceLabel(entry: AuthorizedKeyInventoryEntry): string {
  const readback = readbackFor(entry);
  if (readback === null) return '未读取 host';
  if (!readback.ok) return '读取失败';
  const managed = readback.managed_entries.find((item) => item.entry_id === entry.entry_id);
  if (managed === undefined) return '未纳入 active 对照';
  return managed.present_on_host ? 'host 存在' : 'host 缺失';
}

function hostPresenceType(
  entry: AuthorizedKeyInventoryEntry,
): 'success' | 'error' | 'warning' | 'default' {
  const readback = readbackFor(entry);
  if (readback === null) return 'default';
  if (!readback.ok) return 'error';
  const managed = readback.managed_entries.find((item) => item.entry_id === entry.entry_id);
  if (managed === undefined) return 'default';
  return managed.present_on_host ? 'success' : 'warning';
}

const columns = computed<DataTableColumns<AuthorizedKeyInventoryEntry>>(() => [
  {
    title: 'Linux 账号',
    key: 'linux_username',
    width: 150,
    render: (row) =>
      h('div', { class: 'stack' }, [
        h('span', { class: 'mono target' }, row.linux_username),
        h('span', { class: 'muted tiny' }, `PA #${row.physical_account_id}`),
      ]),
  },
  {
    title: '用户公钥',
    key: 'fingerprint_sha256',
    render: (row) =>
      h('div', { class: 'stack' }, [
        h('span', { class: 'target' }, displayUser(row, 'for')),
        h('span', { class: 'mono muted' }, row.fingerprint_sha256),
        h(
          'span',
          { class: 'muted tiny' },
          `${row.key_type}${row.key_comment ? ` · ${row.key_comment}` : ''}`,
        ),
      ]),
  },
  {
    title: '状态',
    key: 'status',
    width: 120,
    render: (row) =>
      h('div', { class: 'stack' }, [
        h(NTag, { type: statusType(row.status), size: 'small', bordered: false }, () =>
          statusLabel(row.status),
        ),
        h(NTag, { type: hostPresenceType(row), size: 'small', bordered: false }, () =>
          hostPresenceLabel(row),
        ),
        row.key_is_active ? null : h('span', { class: 'muted tiny' }, '原 SSH key 已停用'),
      ]),
  },
  {
    title: '推送',
    key: 'pushed_at',
    width: 210,
    render: (row) =>
      h('div', { class: 'stack' }, [
        h('span', { class: 'muted' }, shortTime(row.pushed_at)),
        h('span', { class: 'muted tiny' }, `by ${displayUser(row, 'by')}`),
      ]),
  },
  {
    title: '撤销',
    key: 'removed_at',
    width: 210,
    render: (row) =>
      h('div', { class: 'stack' }, [
        h('span', { class: 'muted' }, shortTime(row.removed_at)),
        row.removed_at === null
          ? null
          : h('span', { class: 'muted tiny' }, `by ${displayUser(row, 'removed')}`),
      ]),
  },
  {
    title: '',
    key: 'actions',
    width: 190,
    render: (row) =>
      h('div', { class: 'action-stack' }, [
        h(
          NButton,
          {
            size: 'tiny',
            secondary: true,
            loading: props.readbackLoadingPaId === row.physical_account_id,
            onClick: () => emit('readback', row),
          },
          {
            icon: () => h(ScanSearch, { size: 13, strokeWidth: 1.8 }),
            default: () => '读取 host',
          },
        ),
        row.can_retry
          ? h(
              NButton,
              {
                size: 'tiny',
                type: 'warning',
                secondary: true,
                onClick: () => emit('retry', row),
              },
              {
                icon: () => h(RefreshCw, { size: 13, strokeWidth: 1.8 }),
                default: () => '重推',
              },
            )
          : h(
              'span',
              { class: 'muted tiny' },
              row.status === 'active' ? '撤销走账号关联' : '无可用操作',
            ),
      ]),
  },
]);
</script>

<template>
  <div>
    <div class="bar">
      <p class="bar-hint">
        CoreLab 推送过的 authorized_keys 账本。这里只显示平台写入的 key;手工写入的 host 文件会在读取
        host 后显示为未知 fingerprint。移除 key 继续走账号关联撤销流程。
      </p>
      <div class="summary">
        <NTag size="small" type="success" bordered> {{ activeCount }} 已推送 </NTag>
        <NTag size="small" :type="retryCount > 0 ? 'warning' : 'default'" bordered>
          {{ retryCount }} 可重推
        </NTag>
      </div>
    </div>
    <div v-if="readbackList.length > 0" class="readback-wrap">
      <NAlert
        v-for="readback in readbackList"
        :key="readback.physical_account_id"
        :type="readback.ok ? 'info' : 'warning'"
        :show-icon="false"
        class="readback-alert"
      >
        <div class="readback-line">
          <span class="mono target">{{ readback.linux_username }}</span>
          <span v-if="readback.ok">
            host {{ readback.line_count }} 条 key,未知 {{ readback.unknown_host_keys.length }}, 无效
            {{ readback.invalid_line_count }}
          </span>
          <span v-else>{{ readback.error ?? '读取失败' }}</span>
        </div>
        <div v-if="readback.unknown_host_keys.length > 0" class="unknown-list">
          <span
            v-for="key in readback.unknown_host_keys"
            :key="`${readback.physical_account_id}-${key.line_number}`"
            class="mono muted tiny"
          >
            line {{ key.line_number }} · {{ key.fingerprint_sha256 }}
          </span>
        </div>
      </NAlert>
    </div>
    <div v-if="entries.length === 0" class="empty-wrap cl-lift">
      <CleanEmpty
        :icon="KeyRound"
        title="还没有 CoreLab 管理的 key"
        description="Onboard 用户或批准账号绑定申请后,这里会出现平台推送过的 authorized_keys 记录。"
        compact
      />
    </div>
    <div v-else class="table-wrap cl-lift">
      <NDataTable
        :columns="columns"
        :data="entries"
        :loading="loading"
        :row-key="(row) => row.entry_id"
        :bordered="false"
        :single-line="false"
        size="small"
      />
    </div>
  </div>
</template>

<style scoped>
.bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-3) 0;
}
.bar-hint {
  margin: 0;
  color: var(--c-text-secondary);
  font-size: var(--text-sm);
}
.summary {
  display: inline-flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: var(--space-2);
  min-width: 11rem;
}
.stack {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 2px;
}
.mono {
  font-family: var(--font-mono);
}
.target {
  color: var(--c-text-primary);
  font-weight: 600;
}
.muted {
  color: var(--c-text-tertiary);
}
.tiny {
  font-size: var(--text-xs);
}
.empty-wrap {
  padding: var(--space-8) 0;
}
.table-wrap {
  overflow-x: auto;
}
.action-stack {
  display: inline-flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}
.readback-wrap {
  display: grid;
  gap: var(--space-2);
  margin-bottom: var(--space-3);
}
.readback-alert {
  border-radius: var(--radius-sm);
}
.readback-line {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  align-items: center;
}
.unknown-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin-top: var(--space-2);
}
</style>

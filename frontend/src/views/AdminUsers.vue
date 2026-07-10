<script setup lang="ts">
/**
 * AdminUsers — lab_admin user finder.
 *
 * Phase L L-2: rows are clickable and navigate to `/admin/users/:id` (the
 * entity-detail page). Drawer-style inline edit has been removed; all
 * per-user governance happens on the detail page's Danger zone tab. The
 * Invite modal stays here — it creates a row, not modifies one.
 */

import { computed, h, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import {
  NAlert,
  NButton,
  NDataTable,
  NForm,
  NFormItem,
  NIcon,
  NModal,
  NSelect,
  NTag,
  useDialog,
  useMessage,
  type DataTableColumns,
  type FormInst,
} from 'naive-ui';
import { extractDetail } from '@/utils/extractDetail';
import { Clipboard, RefreshCw, Trash2, UserPlus, Users } from 'lucide-vue-next';

import AppLayout from '@/layouts/AppLayout.vue';
import type { UserRead } from '@/api/auth';
import {
  inviteUser,
  listRegistrationInvites,
  listUsers,
  revokeRegistrationInvite,
  type RegistrationInviteRead,
} from '@/api/users';

const router = useRouter();
const message = useMessage();
const dialog = useDialog();

const users = ref<UserRead[]>([]);
const loading = ref(false);
const invites = ref<RegistrationInviteRead[]>([]);
const inviteLoading = ref(false);
const inviteRevokingId = ref<number | null>(null);

const inviteOpen = ref(false);
const inviteSubmitting = ref(false);
const inviteResult = ref<{ url: string; token: string } | null>(null);
const inviteFormRef = ref<FormInst | null>(null);
const invitePayload = ref({
  role: 'user' as 'user' | 'lab_admin',
});

async function refresh(): Promise<void> {
  loading.value = true;
  inviteLoading.value = true;
  try {
    const [nextUsers, nextInvites] = await Promise.all([listUsers(), listRegistrationInvites()]);
    users.value = nextUsers;
    invites.value = nextInvites;
  } catch (err) {
    message.error(err instanceof Error ? err.message : '加载用户失败');
  } finally {
    loading.value = false;
    inviteLoading.value = false;
  }
}

onMounted(refresh);

// Pagebar meta — drives the counts beside the page title. Pure display,
// derived from the existing users list (启用 = is_active && is_activated,
// 待激活 = is_active && !is_activated).
const totalCount = computed(() => users.value.length);
const activeCount = computed(() => users.value.filter((u) => u.is_active && u.is_activated).length);
const pendingCount = computed(
  () => users.value.filter((u) => u.is_active && !u.is_activated).length,
);
const activeInviteCount = computed(
  () => invites.value.filter((invite) => invite.status === 'active').length,
);

// Avatar glyph — first character of username, uppercased. `charAt` returns
// '' (never undefined) so this is safe under noUncheckedIndexedAccess.
function avatarInitial(username: string): string {
  const c = username.trim().charAt(0);
  return c ? c.toUpperCase() : '·';
}

function fmtRelative(iso: string | null | undefined): string {
  if (!iso) return '从未';
  const d = new Date(iso).getTime();
  const diff = Date.now() - d;
  if (diff < 60_000) return `${Math.floor(diff / 1000)} 秒前`;
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前`;
  return `${Math.floor(diff / 86_400_000)} 天前`;
}

function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(iso));
}

function inviteUserLabel(user: RegistrationInviteRead['created_by']): string {
  if (user === null) return '—';
  return `${user.display_name} (@${user.username})`;
}

const columns = computed<DataTableColumns<UserRead>>(() => [
  {
    title: 'ID',
    key: 'id',
    width: 64,
    render: (row) => h('span', { class: 'mono tabular' }, `#${row.id}`),
  },
  {
    title: '用户名',
    key: 'username',
    width: 176,
    render: (row) => {
      // Identity mark mirrors AdminUserDetail's hdr-mark-core (brand-blue
      // tint); pending users go gray, disabled users gray + faded.
      const avatarClass = !row.is_active
        ? 'avatar avatar-off'
        : !row.is_activated
          ? 'avatar avatar-pending'
          : 'avatar';
      return h('span', { class: 'user-cell' }, [
        h(
          'span',
          { class: `${avatarClass} mono`, 'aria-hidden': 'true' },
          avatarInitial(row.username),
        ),
        h('span', { class: 'mono user-name' }, row.username),
      ]);
    },
  },
  { title: '显示名', key: 'display_name' },
  {
    title: '邮箱',
    key: 'email',
    render: (row) => h('span', { class: 'mono muted' }, row.email),
  },
  {
    title: '角色',
    key: 'role',
    width: 110,
    render: (row) =>
      h(
        NTag,
        {
          type: row.role === 'lab_admin' ? 'primary' : 'default',
          size: 'small',
          bordered: false,
        },
        { default: () => row.role },
      ),
  },
  {
    title: '上次登录',
    key: 'last_login_at',
    width: 130,
    render: (row) => h('span', { class: 'mono muted' }, fmtRelative(row.last_login_at)),
  },
  {
    title: '状态',
    key: 'status',
    width: 96,
    render: (row) => {
      // Three-state「dot + text」(labels unchanged): 启用 = green dot;
      // 待激活 = warning dot breathing (cl-pulse); 停用 = gray dot + dim text.
      const s = !row.is_active
        ? { label: '停用', dot: 'st-dot st-off', text: 'st-text st-text-off' }
        : !row.is_activated
          ? { label: '待激活', dot: 'st-dot st-pending cl-pulse', text: 'st-text' }
          : { label: '启用', dot: 'st-dot st-ok', text: 'st-text' };
      return h('span', { class: 'st-cell' }, [
        h('span', { class: s.dot, 'aria-hidden': 'true' }),
        h('span', { class: s.text }, s.label),
      ]);
    },
  },
]);

const inviteStatusMeta = {
  active: { label: '未使用', type: 'success' },
  used: { label: '已使用', type: 'default' },
  expired: { label: '已过期', type: 'warning' },
  revoked: { label: '已撤销', type: 'error' },
} as const;

const inviteColumns = computed<DataTableColumns<RegistrationInviteRead>>(() => [
  {
    title: 'ID',
    key: 'id',
    width: 72,
    render: (row) => h('span', { class: 'mono tabular' }, `#${row.id}`),
  },
  {
    title: '角色',
    key: 'role',
    width: 110,
    render: (row) =>
      h(
        NTag,
        {
          type: row.role === 'lab_admin' ? 'primary' : 'default',
          size: 'small',
          bordered: false,
        },
        { default: () => row.role },
      ),
  },
  {
    title: '状态',
    key: 'status',
    width: 104,
    render: (row) => {
      const meta = inviteStatusMeta[row.status];
      return h(NTag, { type: meta.type, size: 'small', bordered: false }, () => meta.label);
    },
  },
  {
    title: '创建人',
    key: 'created_by',
    render: (row) => h('span', { class: 'muted' }, inviteUserLabel(row.created_by)),
  },
  {
    title: '创建',
    key: 'created_at',
    width: 108,
    render: (row) => h('span', { class: 'mono muted' }, fmtRelative(row.created_at)),
  },
  {
    title: '过期',
    key: 'expires_at',
    width: 122,
    render: (row) => h('span', { class: 'mono muted' }, fmtDate(row.expires_at)),
  },
  {
    title: '使用人',
    key: 'used_by',
    render: (row) => h('span', { class: 'muted' }, inviteUserLabel(row.used_by)),
  },
  {
    title: '',
    key: 'actions',
    width: 92,
    render: (row) =>
      row.can_revoke
        ? h(
            NButton,
            {
              size: 'tiny',
              tertiary: true,
              type: 'error',
              loading: inviteRevokingId.value === row.id,
              onClick: () => confirmRevokeInvite(row),
            },
            {
              icon: () => h(Trash2, { size: 12, strokeWidth: 1.75 }),
              default: () => '撤销',
            },
          )
        : h('span', { class: 'muted' }, '—'),
  },
]);

function openRow(row: UserRead): void {
  router.push({ name: 'admin-user-detail', params: { id: row.id } });
}

function openInvite(): void {
  invitePayload.value = { role: 'user' };
  inviteResult.value = null;
  inviteOpen.value = true;
}

async function submitInvite(): Promise<void> {
  if (!inviteFormRef.value) return;
  try {
    await inviteFormRef.value.validate();
  } catch {
    return;
  }
  inviteSubmitting.value = true;
  try {
    const resp = await inviteUser(invitePayload.value);
    inviteResult.value = { url: resp.activation_url, token: resp.setup_token };
    await refresh();
    message.success('注册链接已生成,请复制给用户');
  } catch (err) {
    message.error(extractDetail(err, '邀请失败'));
  } finally {
    inviteSubmitting.value = false;
  }
}

function confirmRevokeInvite(row: RegistrationInviteRead): void {
  dialog.warning({
    title: '撤销注册链接',
    content: `撤销 #${row.id} 后,原链接将无法完成注册。`,
    positiveText: '撤销',
    negativeText: '取消',
    onPositiveClick: async () => {
      inviteRevokingId.value = row.id;
      try {
        const updated = await revokeRegistrationInvite(row.id);
        const index = invites.value.findIndex((item) => item.id === updated.id);
        if (index >= 0) invites.value.splice(index, 1, updated);
        else invites.value.unshift(updated);
        message.success('邀请已撤销');
      } catch (err) {
        message.error(extractDetail(err, '撤销失败'));
      } finally {
        inviteRevokingId.value = null;
      }
    },
  });
}

async function copyActivationUrl(): Promise<void> {
  if (!inviteResult.value) return;
  try {
    await navigator.clipboard.writeText(inviteResult.value.url);
    message.success('已复制注册链接');
  } catch {
    message.error('无法访问剪贴板');
  }
}

const inviteRules = {
  role: [{ required: true, message: '请选择角色', trigger: 'change' }],
};
</script>

<template>
  <AppLayout>
    <div class="page">
      <header class="cl-pagebar cl-enter">
        <div class="cl-pagebar-icon">
          <Users :size="20" :stroke-width="1.75" />
        </div>
        <div class="cl-pagebar-body">
          <h1 class="cl-pagebar-title">
            用户
            <span class="cl-pagebar-meta">
              <span
                >共 <span class="cl-num">{{ totalCount }}</span> 人</span
              >
              <span class="meta-dot" aria-hidden="true"></span>
              <span
                >活跃 <span class="cl-num">{{ activeCount }}</span></span
              >
              <span class="meta-dot" aria-hidden="true"></span>
              <span class="meta-item" :class="{ 'meta-pending': pendingCount > 0 }">
                <span v-if="pendingCount > 0" class="pending-dot cl-pulse" aria-hidden="true" />
                待激活 <span class="cl-num">{{ pendingCount }}</span>
              </span>
            </span>
          </h1>
          <p class="cl-pagebar-sub">实验室全体成员花名册 —— 生成邀请、查看与管理已注册成员</p>
        </div>
        <div class="cl-pagebar-actions">
          <NButton type="primary" @click="openInvite">
            <template #icon>
              <NIcon><UserPlus :size="14" :stroke-width="2.25" /></NIcon>
            </template>
            邀请用户
          </NButton>
        </div>
      </header>

      <div class="table-wrap cl-enter cl-lift" style="--cl-delay: 0.08s">
        <NDataTable
          :columns="columns"
          :data="users"
          :loading="loading"
          :row-props="
            (row) => ({
              style: 'cursor: pointer',
              class: 'roster-row',
              onClick: () => openRow(row),
            })
          "
          :bordered="false"
          size="small"
        />
      </div>

      <section class="invite-panel cl-enter cl-lift" style="--cl-delay: 0.12s">
        <header class="invite-head">
          <div>
            <h2 class="section-title">邀请链接</h2>
            <div class="section-meta">
              未使用 <span class="cl-num">{{ activeInviteCount }}</span>
            </div>
          </div>
          <NButton size="small" tertiary :loading="inviteLoading" @click="refresh">
            <template #icon>
              <NIcon><RefreshCw :size="13" :stroke-width="1.75" /></NIcon>
            </template>
            刷新
          </NButton>
        </header>
        <NDataTable
          :columns="inviteColumns"
          :data="invites"
          :loading="inviteLoading"
          :bordered="false"
          size="small"
        />
      </section>

      <NModal v-model:show="inviteOpen" preset="card" title="生成注册链接" style="max-width: 28rem">
        <NForm
          v-if="inviteResult === null"
          ref="inviteFormRef"
          :model="invitePayload"
          :rules="inviteRules"
          label-placement="top"
          :show-require-mark="false"
        >
          <NFormItem label="注册后角色" path="role">
            <NSelect
              v-model:value="invitePayload.role"
              :options="[
                { label: 'user', value: 'user' },
                { label: 'lab_admin', value: 'lab_admin' },
              ]"
            />
          </NFormItem>
          <NButton type="primary" block :loading="inviteSubmitting" @click="submitInvite">
            <template #icon>
              <UserPlus :size="14" :stroke-width="2" />
            </template>
            生成邀请链接
          </NButton>
        </NForm>

        <div v-else class="invite-result">
          <NAlert type="success" :show-icon="false" title="注册链接已生成">
            链接只显示一次,用户打开后会自己填写用户名、邮箱、密码和 SSH 公钥。
          </NAlert>
          <pre class="snippet">{{ inviteResult.url }}</pre>
          <div class="actions">
            <NButton @click="copyActivationUrl">
              <template #icon>
                <Clipboard :size="14" :stroke-width="2" />
              </template>
              复制
            </NButton>
            <NButton @click="inviteOpen = false">关闭</NButton>
          </div>
        </div>
      </NModal>
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
  gap: var(--space-5);
}

.invite-panel {
  background: var(--c-bg-elevated);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-lg);
  overflow: hidden;
}
.invite-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-4) var(--space-4) var(--space-3);
  border-bottom: 1px solid var(--c-border-subtle);
}
.section-title {
  margin: 0;
  font-size: var(--text-base);
  font-weight: 600;
  color: var(--c-text-primary);
}
.section-meta {
  margin-top: 2px;
  font-size: var(--text-xs);
  color: var(--c-text-secondary);
}

/* ── 页头 meta 补充(共享 .cl-pagebar 之上的小件)──────────────── */
.meta-dot {
  flex: none;
  width: 3px;
  height: 3px;
  border-radius: var(--radius-full);
  background: var(--c-border-strong);
}
.meta-item {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
}
/* 待激活 > 0:文案转 warning 色 + 小呼吸点 */
.meta-pending {
  color: var(--c-warning);
  font-weight: 600;
}
.pending-dot {
  flex: none;
  width: 6px;
  height: 6px;
  border-radius: var(--radius-full);
  background: var(--c-warning);
  --cl-pulse-color: color-mix(in srgb, var(--c-warning) 45%, transparent);
}

/* ── Username cell: initial avatar + mono handle ─────────────────── */
/* ⚠ 这些元素由 columns 的 h() render 产出,落在 NDataTable 的渲染上下文里,
   拿不到本组件的 data-v 属性 —— 必须经 .table-wrap :deep() 触达,
   直接写裸类名会静默失效(曾经栽过:头像/状态点完全没渲染样式)。 */
.table-wrap :deep(.user-cell) {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
}
.table-wrap :deep(.user-name) {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
/* 28px rounded-square identity mark — brand-blue tint, echoing the
   hdr-mark-core avatar on AdminUserDetail. */
.table-wrap :deep(.avatar) {
  width: 28px;
  height: 28px;
  flex-shrink: 0;
  display: inline-grid;
  place-items: center;
  border-radius: var(--radius-md);
  font-size: 13px;
  font-weight: 600;
  line-height: 1;
  color: var(--c-accent);
  background: color-mix(in srgb, var(--c-accent) 12%, var(--c-bg-elevated));
  border: 1px solid color-mix(in srgb, var(--c-accent) 30%, transparent);
  user-select: none;
}
/* pending-activation: gray, not yet "lit up" */
.table-wrap :deep(.avatar-pending) {
  color: var(--c-text-secondary);
  background: color-mix(in srgb, var(--c-text-tertiary) 10%, var(--c-bg-elevated));
  border-color: var(--c-border-default);
}
/* disabled: gray + faded */
.table-wrap :deep(.avatar-off) {
  color: var(--c-text-tertiary);
  background: color-mix(in srgb, var(--c-text-tertiary) 10%, var(--c-bg-elevated));
  border-color: var(--c-border-subtle);
  opacity: 0.55;
}

/* status column:「dot + text」three-state */
.table-wrap :deep(.st-cell) {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.table-wrap :deep(.st-dot) {
  display: inline-block;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex-shrink: 0;
}
.table-wrap :deep(.st-ok) {
  background: var(--c-success);
}
.table-wrap :deep(.st-off) {
  background: var(--c-text-disabled);
}
.table-wrap :deep(.st-pending) {
  background: var(--c-warning);
  --cl-pulse-color: color-mix(in srgb, var(--c-warning) 55%, transparent);
}
.table-wrap :deep(.st-text) {
  font-size: 12px;
}
.table-wrap :deep(.st-text-off) {
  color: var(--c-text-tertiary);
}

/* row hover highlight (keeps the click-to-detail affordance) */
.table-wrap :deep(.roster-row:hover .n-data-table-td) {
  background: color-mix(in srgb, var(--c-accent) 5%, var(--c-bg-elevated));
}

.table-wrap :deep(.n-data-table .n-data-table-th),
.table-wrap :deep(.n-data-table .n-data-table-td) {
  padding: 8px 12px;
}

.mono {
  font-family: var(--font-mono);
}

.muted {
  color: var(--c-text-tertiary);
}

.tabular {
  font-variant-numeric: tabular-nums;
}

.snippet {
  margin: var(--space-3) 0;
  padding: var(--space-3);
  background: var(--c-bg-sunken);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-sm);
  font-family: var(--font-mono);
  font-size: 12px;
  word-break: break-all;
  white-space: pre-wrap;
}

.actions {
  display: flex;
  gap: var(--space-2);
  justify-content: flex-end;
}

/* cl-pulse / cl-enter / cl-lift 在全局规则里已按
   prefers-reduced-motion 降级,此处无需额外处理。 */
</style>

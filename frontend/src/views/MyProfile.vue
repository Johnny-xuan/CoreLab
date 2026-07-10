<script setup lang="ts">
/**
 * MyProfile — edit own display_name / email, change password, manage SSH keys.
 *
 * All actions hit /api/v1/users/me/* or /api/v1/users/me/ssh-keys/*.
 * Updating the profile refreshes the auth store so the topbar / sidebar
 * see the new display_name immediately.
 *
 * Visual: three section cards (User / Password / SSH keys) with lucide
 * section icons + Vercel-style table chrome. The ssh-keygen helper
 * paragraph and Chinese copy from agent-D's pass are preserved.
 */

import { computed, h, onMounted, ref } from 'vue';
import {
  NAlert,
  NButton,
  NCard,
  NDataTable,
  NForm,
  NFormItem,
  NInput,
  NModal,
  NTag,
  useDialog,
  useMessage,
  type DataTableColumns,
  type FormInst,
} from 'naive-ui';
import { extractDetail } from '@/utils/extractDetail';
import { Key, KeyRound, Plus, Trash2, User, UserRound } from 'lucide-vue-next';

import AppLayout from '@/layouts/AppLayout.vue';
import CleanEmpty from '@/components/CleanEmpty.vue';
import { changeOwnPassword, updateUser } from '@/api/users';
import { addMyKey, deleteMyKey, listMyKeys, type SshKeyRead } from '@/api/sshKeys';
import { useAuthStore } from '@/stores/auth';

const auth = useAuthStore();
const message = useMessage();
const dialog = useDialog();

// ── Pure-display helpers for the frameless identity masthead ─────────
// Derived only from the existing auth.user snapshot; no extra fetching.
const heroName = computed(
  () => auth.user?.display_name?.trim() || auth.user?.username || '个人资料',
);
const heroUsername = computed(() => auth.user?.username ?? '');
const heroEmail = computed(() => auth.user?.email ?? '');

// First glyph for the avatar — first letter / CJK char of the display name,
// falling back to the username, then a neutral dot.
const heroInitial = computed(() => {
  const source = auth.user?.display_name?.trim() || auth.user?.username?.trim() || '';
  return source ? (Array.from(source)[0]?.toUpperCase() ?? '·') : '·';
});

const roleLabel = computed(() => (auth.user?.role === 'lab_admin' ? '实验室管理员' : '普通用户'));
const isActive = computed(() => auth.user?.is_active ?? false);

const profileFormRef = ref<FormInst | null>(null);
const passwordFormRef = ref<FormInst | null>(null);
const keyFormRef = ref<FormInst | null>(null);

const profileValue = ref({
  display_name: auth.user?.display_name ?? '',
  email: auth.user?.email ?? '',
});
const profileSubmitting = ref(false);

const passwordValue = ref({ old_password: '', new_password: '', new_password_confirm: '' });
const passwordSubmitting = ref(false);

const keys = ref<SshKeyRead[]>([]);
const keysLoading = ref(false);
const keyModalOpen = ref(false);
const keyPayload = ref({ label: '', public_key: '' });
const keySubmitting = ref(false);

async function refreshKeys(): Promise<void> {
  keysLoading.value = true;
  try {
    keys.value = await listMyKeys();
  } catch (err) {
    message.error(extractDetail(err, '加载 SSH key 失败'));
  } finally {
    keysLoading.value = false;
  }
}

onMounted(refreshKeys);

const profileRules = {
  display_name: [{ required: true, message: '请输入显示名', trigger: 'blur' }],
  email: [
    { required: true, message: '请输入邮箱', trigger: 'blur' },
    { pattern: /^[^@\s]+@[^@\s]+\.[^@\s]+$/, message: '邮箱格式不正确', trigger: 'blur' },
  ],
};

const passwordRules = {
  old_password: [{ required: true, message: '请输入当前密码', trigger: 'blur' }],
  new_password: [
    { required: true, message: '请输入新密码', trigger: 'blur' },
    { min: 8, message: '新密码至少 8 位', trigger: 'blur' },
  ],
  new_password_confirm: [
    { required: true, message: '请再次输入新密码', trigger: 'blur' },
    {
      validator: (_rule: unknown, value: string) => value === passwordValue.value.new_password,
      message: '两次密码不一致',
      trigger: 'blur',
    },
  ],
};

const keyRules = {
  public_key: [
    { required: true, message: '请粘贴公钥', trigger: 'blur' },
    {
      pattern: /^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp\d+|sk-)/,
      message: '只支持 ssh-ed25519 / ssh-rsa / ecdsa / sk-',
      trigger: 'blur',
    },
  ],
};

async function submitProfile(): Promise<void> {
  if (!profileFormRef.value || !auth.user) return;
  try {
    await profileFormRef.value.validate();
  } catch {
    return;
  }
  profileSubmitting.value = true;
  try {
    const updated = await updateUser(auth.user.id, profileValue.value);
    await auth.refreshMe();
    profileValue.value = { display_name: updated.display_name, email: updated.email };
    message.success('已保存');
  } catch (err) {
    message.error(extractDetail(err, '保存失败'));
  } finally {
    profileSubmitting.value = false;
  }
}

async function submitPassword(): Promise<void> {
  if (!passwordFormRef.value) return;
  try {
    await passwordFormRef.value.validate();
  } catch {
    return;
  }
  passwordSubmitting.value = true;
  try {
    await changeOwnPassword(passwordValue.value.old_password, passwordValue.value.new_password);
    passwordValue.value = { old_password: '', new_password: '', new_password_confirm: '' };
    message.success('密码已更新,下次登录请使用新密码');
  } catch (err) {
    message.error(extractDetail(err, '密码更新失败'));
  } finally {
    passwordSubmitting.value = false;
  }
}

const keyColumns = computed<DataTableColumns<SshKeyRead>>(() => [
  {
    title: '类型',
    key: 'key_type',
    width: 110,
    render: (row) =>
      h(NTag, { size: 'small', bordered: false, type: 'default' }, { default: () => row.key_type }),
  },
  {
    title: '指纹',
    key: 'fingerprint_sha256',
    render: (row) => h('span', { class: 'mono' }, row.fingerprint_sha256),
  },
  { title: '备注', key: 'comment', render: (row) => row.comment ?? '—' },
  {
    title: '',
    key: 'actions',
    width: 90,
    render: (row) =>
      h(
        NButton,
        {
          size: 'tiny',
          quaternary: true,
          type: 'error',
          onClick: () => confirmDelete(row),
        },
        {
          icon: () => h(Trash2, { size: 12, 'stroke-width': 1.75 }),
          default: () => '删除',
        },
      ),
  },
]);

function confirmDelete(row: SshKeyRead): void {
  dialog.warning({
    title: '删除 SSH key?',
    content: `Fingerprint: ${row.fingerprint_sha256}`,
    positiveText: '删除',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        await deleteMyKey(row.id);
        await refreshKeys();
        message.success('已删除');
      } catch (err) {
        message.error(extractDetail(err, '删除失败'));
      }
    },
  });
}

function openAddKey(): void {
  keyPayload.value = { label: '', public_key: '' };
  keyModalOpen.value = true;
}

async function submitAddKey(): Promise<void> {
  if (!keyFormRef.value) return;
  try {
    await keyFormRef.value.validate();
  } catch {
    return;
  }
  keySubmitting.value = true;
  try {
    await addMyKey({
      public_key: keyPayload.value.public_key,
      label: keyPayload.value.label || undefined,
    });
    await refreshKeys();
    keyModalOpen.value = false;
    message.success('已添加');
  } catch (err) {
    message.error(extractDetail(err, '添加失败'));
  } finally {
    keySubmitting.value = false;
  }
}
</script>

<template>
  <AppLayout>
    <div class="page">
      <header class="masthead cl-enter">
        <div class="masthead-row">
          <div class="avatar" aria-hidden="true">
            <span v-if="heroInitial !== '·'" class="avatar-initial">{{ heroInitial }}</span>
            <UserRound v-else :size="24" :stroke-width="1.75" />
          </div>
          <div class="masthead-text">
            <div class="masthead-name-row">
              <h1 class="masthead-name">{{ heroName }}</h1>
              <span class="role-tag">{{ roleLabel }}</span>
              <span v-if="isActive" class="status-ok">
                <span class="status-dot cl-pulse" />在用
              </span>
            </div>
            <div class="masthead-meta">
              <span v-if="heroUsername" class="mono">@{{ heroUsername }}</span>
              <span v-if="heroUsername && heroEmail" class="meta-sep" aria-hidden="true">·</span>
              <span v-if="heroEmail" class="mono">{{ heroEmail }}</span>
            </div>
          </div>
        </div>
        <p class="masthead-sub">管理个人资料、密码、以及关联的 SSH 公钥。</p>
      </header>

      <NCard :bordered="true" class="card cl-lift cl-enter" style="--cl-delay: 0.06s">
        <template #header>
          <div class="card-head">
            <span class="card-icon">
              <User :size="14" :stroke-width="1.75" />
            </span>
            <span class="card-title">资料</span>
          </div>
        </template>
        <NForm
          ref="profileFormRef"
          :model="profileValue"
          :rules="profileRules"
          label-placement="top"
          :show-require-mark="false"
        >
          <NFormItem label="显示名" path="display_name">
            <NInput v-model:value="profileValue.display_name" />
          </NFormItem>
          <NFormItem label="邮箱" path="email">
            <NInput v-model:value="profileValue.email" />
          </NFormItem>
          <NButton type="primary" :loading="profileSubmitting" @click="submitProfile">
            保存
          </NButton>
        </NForm>
      </NCard>

      <NCard :bordered="true" class="card cl-lift cl-enter" style="--cl-delay: 0.12s">
        <template #header>
          <div class="card-head">
            <span class="card-icon">
              <KeyRound :size="14" :stroke-width="1.75" />
            </span>
            <span class="card-title">修改密码</span>
          </div>
        </template>
        <NForm
          ref="passwordFormRef"
          :model="passwordValue"
          :rules="passwordRules"
          label-placement="top"
          :show-require-mark="false"
        >
          <NFormItem label="当前密码" path="old_password">
            <NInput v-model:value="passwordValue.old_password" type="password" />
          </NFormItem>
          <NFormItem label="新密码" path="new_password">
            <NInput v-model:value="passwordValue.new_password" type="password" />
          </NFormItem>
          <NFormItem label="确认新密码" path="new_password_confirm">
            <NInput v-model:value="passwordValue.new_password_confirm" type="password" />
          </NFormItem>
          <NButton type="primary" :loading="passwordSubmitting" @click="submitPassword">
            更新密码
          </NButton>
        </NForm>
      </NCard>

      <NCard :bordered="true" class="card cl-lift cl-enter" style="--cl-delay: 0.18s">
        <template #header>
          <div class="card-head">
            <span class="card-icon">
              <Key :size="14" :stroke-width="1.75" />
            </span>
            <span class="card-title">SSH 公钥</span>
            <span class="count-chip mono tabular">{{ keys.length }}</span>
            <div class="card-spacer" />
            <NButton size="small" type="primary" @click="openAddKey">
              <template #icon>
                <Plus :size="14" :stroke-width="2.25" />
              </template>
              添加公钥
            </NButton>
          </div>
        </template>
        <p class="ssh-blurb">
          添加 SSH 公钥后,管理员可以把这把 key push 到你的 Linux 账号
          <code>authorized_keys</code>。如何生成 SSH key:在终端运行
          <code>ssh-keygen -t ed25519 -C "your-email@example.com"</code>,然后把
          <code>~/.ssh/id_ed25519.pub</code> 文件的内容粘贴进来。
        </p>
        <div v-if="keys.length === 0 && !keysLoading" class="empty-wrap">
          <CleanEmpty
            :icon="Key"
            title="还没有 SSH 公钥"
            description="添加公钥后才能 claim/PA 通过 SSH challenge 完成账号关联。"
            compact
          />
        </div>
        <div v-else class="table-wrap">
          <NDataTable
            :columns="keyColumns"
            :data="keys"
            :loading="keysLoading"
            :bordered="false"
            :single-line="false"
            size="small"
          />
        </div>
      </NCard>

      <NModal
        v-model:show="keyModalOpen"
        preset="card"
        title="添加 SSH 公钥"
        style="max-width: 36rem"
      >
        <NForm
          ref="keyFormRef"
          :model="keyPayload"
          :rules="keyRules"
          label-placement="top"
          :show-require-mark="false"
        >
          <NFormItem label="标签(可选)">
            <NInput v-model:value="keyPayload.label" placeholder="如:Laptop / MacBook" />
          </NFormItem>
          <NFormItem label="公钥" path="public_key">
            <NInput
              v-model:value="keyPayload.public_key"
              type="textarea"
              :rows="4"
              placeholder="ssh-ed25519 AAAA... user@host"
            />
          </NFormItem>
          <NAlert type="info" :show-icon="false">
            粘贴 <code>~/.ssh/id_*.pub</code> 的内容(整行,包含类型前缀)。
          </NAlert>
          <div class="key-modal-actions">
            <NButton @click="keyModalOpen = false">取消</NButton>
            <NButton type="primary" :loading="keySubmitting" @click="submitAddKey">添加</NButton>
          </div>
        </NForm>
      </NModal>
    </div>
  </AppLayout>
</template>

<style scoped>
.page {
  padding: var(--space-6) var(--space-8);
  max-width: 60rem;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}
/* ── 无框身份头 — 头像 + 名字靠排版立住,不要装饰盒子 ──────────────── */
.masthead {
  padding: var(--space-2) 0 var(--space-5);
  border-bottom: 1px solid var(--c-border-subtle);
}
.masthead-row {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  min-width: 0;
}

/* 首字母头像 — 唯一保留的图形元素 */
.avatar {
  width: 52px;
  height: 52px;
  flex-shrink: 0;
  display: grid;
  place-items: center;
  border-radius: 50%;
  color: var(--c-accent);
  background: color-mix(in srgb, var(--c-accent) 10%, transparent);
  border: 1px solid color-mix(in srgb, var(--c-accent) 28%, transparent);
}
.avatar-initial {
  font-size: var(--text-xl);
  font-weight: 600;
  letter-spacing: var(--tracking-tight);
  line-height: 1;
}

.masthead-text {
  min-width: 0;
}
.masthead-name-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-3);
}
.masthead-name {
  font-size: var(--text-2xl);
  font-weight: 600;
  letter-spacing: var(--tracking-tight);
  line-height: var(--leading-tight);
  margin: 0;
  color: var(--c-text-primary);
  word-break: break-word;
}
.role-tag {
  font-size: var(--text-xs);
  font-weight: 500;
  color: var(--c-text-secondary);
  border: 1px solid var(--c-border-default);
  border-radius: var(--radius-full);
  padding: 1px 8px;
  white-space: nowrap;
}
.status-ok {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: var(--text-xs);
  color: var(--c-success);
  white-space: nowrap;
}
.status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--c-success);
  --cl-pulse-color: color-mix(in srgb, var(--c-success) 50%, transparent);
}
.masthead-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
  margin-top: var(--space-1);
  font-size: var(--text-xs);
  color: var(--c-text-secondary);
  min-width: 0;
}
.masthead-meta .mono {
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.meta-sep {
  color: var(--c-text-tertiary);
}
.masthead-sub {
  margin: var(--space-3) 0 0;
  font-size: var(--text-sm);
  color: var(--c-text-secondary);
}
.mono {
  font-family: var(--font-mono);
}

.card {
  background: var(--c-bg-elevated);
}
.card :deep(.n-card__content) {
  padding: var(--space-5);
}
.card :deep(.n-card-header) {
  padding: var(--space-3) var(--space-5);
  border-bottom: 1px solid var(--c-border-subtle);
}

.card-head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}
.card-spacer {
  flex: 1;
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
  font-size: var(--text-base);
  font-weight: 600;
  letter-spacing: var(--tracking-tight);
  color: var(--c-text-primary);
}
.count-chip {
  display: inline-flex;
  align-items: center;
  padding: 1px 6px;
  font-size: var(--text-2xs);
  color: var(--c-text-secondary);
  background: var(--c-bg-sunken);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-sm);
}

.ssh-blurb {
  margin: 0 0 var(--space-4);
  font-size: var(--text-xs);
  color: var(--c-text-secondary);
  line-height: var(--leading-snug);
}
.ssh-blurb code {
  font-family: var(--font-mono);
  font-size: 0.95em;
  background: var(--c-bg-sunken);
  border: 1px solid var(--c-border-subtle);
  padding: 1px 4px;
  border-radius: var(--radius-sm);
}

.table-wrap {
  background: var(--c-bg-elevated);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-md);
  overflow: hidden;
}
.empty-wrap {
  background: var(--c-bg-elevated);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-md);
}

.key-modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-3);
  margin-top: var(--space-3);
}
</style>

<style>
/* Cross-scope: align Naive data table cells with token-driven spacing. */
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
  color: var(--c-text-secondary);
}

/* SSH key rows: a touch of slide on hover (echoes .cl-nudge), so the
   list feels as alive as the rest of the console. */
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

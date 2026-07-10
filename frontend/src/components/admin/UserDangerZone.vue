<script setup lang="ts">
/**
 * UserDangerZone — Phase L L-1 admin-only governance block.
 *
 * Lives at the bottom of /admin/users/:id as the Danger zone tab. Each
 * block is a single irreversible action with a short rationale and one
 * confirmation step. All buttons mutate user state — fold this away from
 * the observation tabs so admins do not stumble into it.
 */

import { computed, ref } from 'vue';
import { NButton, NCard, NIcon, NSelect, useDialog, useMessage } from 'naive-ui';
import { AlertTriangle } from 'lucide-vue-next';
import { extractDetail } from '@/utils/extractDetail';

import type { UserRead } from '@/api/auth';
import type { ProfileLinkItem } from '@/api/users';
import { changeRole, disableUser, reactivateUser, resetUserPassword } from '@/api/users';
import { revokeLink } from '@/api/accountLinks';
import OneTimeLinkModal from './OneTimeLinkModal.vue';

const props = defineProps<{
  user: UserRead;
  links: ProfileLinkItem[];
}>();

const emit = defineEmits<{
  (e: 'changed'): void;
}>();

const dialog = useDialog();
const message = useMessage();

const disableSubmitting = ref(false);
const roleSubmitting = ref(false);
const revokeSubmitting = ref(false);
const resetSubmitting = ref(false);
const resetLink = ref<string | null>(null);
const resetModalOpen = ref(false);

const selectedLinkId = ref<number | null>(null);

const linkOptions = computed(() =>
  props.links.map((l) => ({
    label: `#${l.link_id}  ${l.server_hostname} / ${l.linux_username}  (${l.source})`,
    value: l.link_id,
  })),
);

const otherRole = computed(() => (props.user.role === 'lab_admin' ? 'user' : 'lab_admin'));

function doDisable(): void {
  const reactivating = !props.user.is_active;
  dialog.warning({
    title: reactivating ? '重新启用该用户?' : '禁用该用户?',
    content: reactivating
      ? `${props.user.username} 将可以重新登录。此前被撤销的账号关联与 server admin 权限不会自动恢复,需要重新授予。`
      : `${props.user.username} 将无法再登录,其全部账号关联与 server admin 权限会被一并撤销(并移除服务器上的公钥)。已有会话在过期前仍然有效。`,
    positiveText: reactivating ? '重新启用' : '禁用',
    negativeText: '取消',
    onPositiveClick: async () => {
      disableSubmitting.value = true;
      try {
        if (reactivating) {
          await reactivateUser(props.user.id);
          message.success('用户已重新启用');
        } else {
          await disableUser(props.user.id);
          message.success('用户已禁用');
        }
        emit('changed');
      } catch (err) {
        message.error(extractDetail(err, '操作失败'));
      } finally {
        disableSubmitting.value = false;
      }
    },
  });
}

function doChangeRole(): void {
  const isDemote = props.user.role === 'lab_admin';
  dialog.warning({
    title: isDemote ? '降级为 user?' : '提升为 lab_admin?',
    content: `将把 ${props.user.username} 的角色设为 ${otherRole.value}。`,
    positiveText: '确认',
    negativeText: '取消',
    onPositiveClick: async () => {
      roleSubmitting.value = true;
      try {
        await changeRole(props.user.id, otherRole.value as 'user' | 'lab_admin');
        message.success(`角色已改为 ${otherRole.value}`);
        emit('changed');
      } catch (err) {
        message.error(extractDetail(err, '操作失败'));
      } finally {
        roleSubmitting.value = false;
      }
    },
  });
}

async function doResetPassword(): Promise<void> {
  resetSubmitting.value = true;
  try {
    const resp = await resetUserPassword(props.user.id);
    resetLink.value = resp.reset_url;
    resetModalOpen.value = true;
    message.success('重置链接已生成,请复制后转交给用户');
  } catch (err) {
    message.error(extractDetail(err, '生成失败'));
  } finally {
    resetSubmitting.value = false;
  }
}

function doForceRevokeLink(): void {
  if (selectedLinkId.value === null) {
    message.warning('请先选择一个关联');
    return;
  }
  const link = props.links.find((l) => l.link_id === selectedLinkId.value);
  if (!link) return;
  dialog.warning({
    title: '强制撤销关联?',
    content: `将在不征求用户同意的情况下撤销关联 #${link.link_id}(${link.server_hostname} / ${link.linux_username}),并移除 CoreLab 推送到 authorized_keys 的公钥。`,
    positiveText: '撤销',
    negativeText: '取消',
    onPositiveClick: async () => {
      revokeSubmitting.value = true;
      try {
        await revokeLink(link.link_id, 'admin_force', true);
        message.success(`关联 #${link.link_id} 已撤销`);
        selectedLinkId.value = null;
        emit('changed');
      } catch (err) {
        message.error(extractDetail(err, '撤销失败'));
      } finally {
        revokeSubmitting.value = false;
      }
    },
  });
}
</script>

<template>
  <NCard size="small" :bordered="false" class="dz-card">
    <div class="dz-header">
      <NIcon :size="14" :color="'var(--c-danger)'"><AlertTriangle /></NIcon>
      <span class="dz-title">危险操作区</span>
    </div>

    <div class="dz-block">
      <div class="dz-block-text">
        <div class="dz-block-title">{{ user.is_active ? '禁用用户' : '重新启用用户' }}</div>
        <div class="dz-block-desc">
          {{
            user.is_active
              ? '用户将无法再登录,其账号关联与 server admin 权限会被一并撤销(重新启用后需重新授予)。已有会话在过期前仍然有效。'
              : '用户将可以重新登录。此前被撤销的关联与权限不会自动恢复,需要重新授予。'
          }}
        </div>
      </div>
      <NButton
        :type="user.is_active ? 'error' : 'primary'"
        size="small"
        :loading="disableSubmitting"
        @click="doDisable"
      >
        {{ user.is_active ? '禁用' : '重新启用' }}
      </NButton>
    </div>

    <div class="dz-sep" />

    <div class="dz-block">
      <div class="dz-block-text">
        <div class="dz-block-title">变更角色</div>
        <div class="dz-block-desc">
          在 user 与 lab_admin 之间提升 / 降级。不允许降级最后一个 lab_admin。 当前角色:<span
            class="mono"
            >{{ user.role }}</span
          >
        </div>
      </div>
      <NButton size="small" :loading="roleSubmitting" @click="doChangeRole">
        改为 {{ otherRole }}
      </NButton>
    </div>

    <div class="dz-sep" />

    <div class="dz-block">
      <div class="dz-block-text">
        <div class="dz-block-title">重置密码</div>
        <div class="dz-block-desc">
          {{
            user.is_activated
              ? '生成一个一次性的重置链接,由管理员复制后转交给用户。会使该用户此前未使用的链接失效。'
              : '该用户尚未激活,请改用「重发邀请」生成激活链接。'
          }}
        </div>
      </div>
      <NButton
        size="small"
        :loading="resetSubmitting"
        :disabled="!user.is_activated"
        @click="doResetPassword"
      >
        生成链接
      </NButton>
    </div>

    <div class="dz-sep" />

    <div class="dz-block">
      <div class="dz-block-text">
        <div class="dz-block-title">强制撤销一个账号关联</div>
        <div class="dz-block-desc">
          跳过用户的同意流程,从服务器的 authorized_keys 中移除 CoreLab
          推送的公钥。在用户离场时很有用。
        </div>
        <div class="dz-block-control">
          <NSelect
            v-model:value="selectedLinkId"
            :options="linkOptions"
            placeholder="选择一个有效的关联…"
            size="small"
            :disabled="!links.length"
            clearable
          />
        </div>
      </div>
      <NButton
        type="error"
        size="small"
        :loading="revokeSubmitting"
        :disabled="selectedLinkId === null"
        @click="doForceRevokeLink"
      >
        撤销
      </NButton>
    </div>
  </NCard>

  <OneTimeLinkModal
    v-model:show="resetModalOpen"
    title="密码重置链接"
    :url="resetLink"
    description="链接只显示一次,请立即复制并转交给用户。用户打开后即可设置新密码。"
  />
</template>

<style scoped>
.dz-card {
  background: var(--c-bg-sunken) !important;
  border: 1px solid var(--c-border-subtle) !important;
}
.dz-card :deep(.n-card__content) {
  padding: var(--space-3) var(--space-4);
}
.dz-header {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--c-danger);
  font-size: 11px;
  letter-spacing: 0.08em;
  font-weight: 600;
  margin-bottom: var(--space-3);
}
.dz-title {
  text-transform: uppercase;
}
.dz-block {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-4);
  padding: var(--space-3) 0;
}
.dz-block-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  flex: 1;
}
.dz-block-title {
  font-size: 13px;
  color: var(--c-text-primary);
  font-weight: 600;
}
.dz-block-desc {
  font-size: 12px;
  color: var(--c-text-tertiary);
  line-height: 1.5;
}
.dz-block-control {
  margin-top: 6px;
  max-width: 320px;
}
.dz-sep {
  border-top: 1px solid var(--c-border-subtle);
}
.mono {
  font-family: var(--font-mono);
}
</style>

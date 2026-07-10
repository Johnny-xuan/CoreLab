<script setup lang="ts">
/**
 * OnboardUserDialog — Phase K K-7.
 *
 * Server admin's one-stop "create the Linux account + push the user's
 * key + write the link" flow. Backend handles the composite in a
 * single transaction (POST /servers/:id/onboard-user).
 *
 * The warning banner here is the platform-design hinge: this UI gives
 * admin the ability to instantly grant SSH access to anyone in the lab
 * — surface that, audit it, and require a written reason.
 */
import { computed, onMounted, ref } from 'vue';
import { NAlert, NButton, NCard, NInput, NModal, NSelect, NSpace, useMessage } from 'naive-ui';

import { listUsers } from '@/api/users';
import type { UserRead } from '@/api/users';
import { listUserKeys } from '@/api/sshKeys';
import type { SshKeyRead } from '@/api/sshKeys';
import { onboardUser, retryAuthorizedKeyPush } from '@/api/physicalAccounts';
import type { OnboardUserResponse } from '@/api/physicalAccounts';

const props = defineProps<{ serverId: number; show: boolean }>();
const emit = defineEmits<{ done: []; cancel: [] }>();
const message = useMessage();

const users = ref<UserRead[]>([]);
const keys = ref<SshKeyRead[]>([]);
const ownerId = ref<number | null>(null);
const keyId = ref<number | null>(null);
const linuxUsername = ref('');
const reason = ref('');
const submitting = ref(false);
const retryingKey = ref(false);
const loading = ref(true);
const lastResult = ref<OnboardUserResponse | null>(null);

onMounted(async () => {
  try {
    users.value = (await listUsers()).filter((u) => u.is_active);
  } catch (e) {
    message.error(`加载用户失败: ${String(e)}`);
  } finally {
    loading.value = false;
  }
});

const userOptions = computed(() =>
  users.value.map((u) => ({ label: `${u.display_name} (@${u.username})`, value: u.id })),
);
const keyOptions = computed(() =>
  keys.value
    .filter((k) => k.is_active)
    .map((k) => ({
      label: `${k.key_type} · ${k.fingerprint_sha256.slice(0, 26)}…${k.comment ? ` (${k.comment})` : ''}`,
      value: k.id,
    })),
);

async function onOwnerSelected(id: number): Promise<void> {
  ownerId.value = id;
  keyId.value = null;
  keys.value = [];
  try {
    keys.value = await listUserKeys(id);
  } catch (e) {
    message.error(`加载用户公钥失败: ${String(e)}`);
  }
}

const canSubmit = computed(
  () =>
    ownerId.value !== null &&
    keyId.value !== null &&
    linuxUsername.value.match(/^[a-z_][a-z0-9_-]{0,31}$/) !== null &&
    reason.value.trim().length >= 20,
);

function outcomeOk(outcome: Record<string, unknown> | undefined): boolean {
  return outcome?.ok === true;
}

const useraddOk = computed(() => outcomeOk(lastResult.value?.useradd_outcome));
const keyPushOk = computed(() => outcomeOk(lastResult.value?.key_push_outcome));

async function submit(): Promise<void> {
  if (!canSubmit.value || ownerId.value === null || keyId.value === null) return;
  submitting.value = true;
  try {
    const result = await onboardUser(props.serverId, {
      linux_username: linuxUsername.value,
      owner_user_id: ownerId.value,
      ssh_public_key_id: keyId.value,
      reason: reason.value,
    });
    lastResult.value = result;
    if (outcomeOk(result.useradd_outcome) && outcomeOk(result.key_push_outcome)) {
      message.success('已接入 —— Linux 账号、公钥、关联全部就位');
      emit('done');
    } else {
      message.warning('接入已记录,但有步骤需要修复。请查看下方结果。');
    }
  } catch (e) {
    message.error(`onboard 失败: ${String(e)}`);
  } finally {
    submitting.value = false;
  }
}

async function retryKeyPush(): Promise<void> {
  const result = lastResult.value;
  if (result === null) return;
  retryingKey.value = true;
  try {
    const retry = await retryAuthorizedKeyPush(
      result.physical_account_id,
      result.authorized_key_entry_id,
    );
    lastResult.value = { ...result, key_push_outcome: retry.key_push_outcome };
    if (outcomeOk(retry.key_push_outcome)) {
      message.success('SSH 公钥已重新推送。');
      if (useraddOk.value) emit('done');
    } else {
      message.warning(`推送仍失败:${String(retry.key_push_outcome.error ?? 'agent 未返回成功')}`);
    }
  } catch (e) {
    message.error(`重试推送失败: ${String(e)}`);
  } finally {
    retryingKey.value = false;
  }
}
</script>

<template>
  <NModal
    :show="show"
    preset="card"
    title="接入用户 — 创建 Linux 账号 + 推送公钥"
    style="width: 560px"
    :mask-closable="false"
    @close="emit('cancel')"
  >
    <div class="body">
      <NAlert type="warning" :show-icon="true" title="一次性赋权">
        这个操作会在 server 上 <code>useradd</code> + 把用户公钥推进
        authorized_keys。落地后用户既能用平台预约,也能直接 ssh 登 — 请确认必要性,理由会记入 audit
        log。
      </NAlert>

      <NCard size="small" title="目标用户">
        <NSelect
          v-model:value="ownerId"
          :options="userOptions"
          :loading="loading"
          placeholder="选 lab 内的一个 user"
          @update:value="onOwnerSelected"
        />
      </NCard>

      <NCard v-if="ownerId !== null" size="small" title="用他的哪把公钥">
        <NSelect
          v-model:value="keyId"
          :options="keyOptions"
          placeholder="选一把这个 user 已注册的 active 公钥"
        />
        <p v-if="keyOptions.length === 0" class="muted">
          这个 user 还没有 active 公钥 — 让他先去 My profile 添加一把。
        </p>
      </NCard>

      <NCard size="small" title="新建账号信息">
        <p class="hint">Linux 账号名 — 必须符合 <code>^[a-z_][a-z0-9_-]{0,31}$</code></p>
        <NInput v-model:value="linuxUsername" placeholder="alice_lab" />
        <p class="hint" style="margin-top: var(--space-3)">操作理由(≥ 20 字符,记入审计)</p>
        <NInput
          v-model:value="reason"
          type="textarea"
          :autosize="{ minRows: 3 }"
          placeholder="例如: 新加入实验室的 Alice,需要在 lab-gpu-01 上跑 Pytorch 实验"
        />
      </NCard>

      <NAlert
        v-if="lastResult !== null"
        :type="useraddOk && keyPushOk ? 'success' : 'warning'"
        :show-icon="true"
        title="接入结果"
      >
        <div class="result-lines">
          <span>
            useradd:
            <strong :class="useraddOk ? 'ok' : 'bad'">
              {{ useraddOk ? '成功' : '需要处理' }}
            </strong>
          </span>
          <span>
            authorized_keys:
            <strong :class="keyPushOk ? 'ok' : 'bad'">
              {{ keyPushOk ? '已推送' : '未完成' }}
            </strong>
          </span>
          <NButton
            v-if="!keyPushOk"
            size="small"
            type="primary"
            :loading="retryingKey"
            @click="retryKeyPush"
          >
            重试推 key
          </NButton>
        </div>
      </NAlert>
    </div>

    <template #footer>
      <NSpace justify="end">
        <NButton :disabled="submitting" @click="emit('cancel')">取消</NButton>
        <NButton type="primary" :loading="submitting" :disabled="!canSubmit" @click="submit">
          接入
        </NButton>
      </NSpace>
    </template>
  </NModal>
</template>

<style scoped>
.body {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}
.hint {
  margin: 0 0 var(--space-2);
  color: var(--c-text-secondary);
  font-size: var(--text-sm);
}
.muted {
  color: var(--c-text-secondary);
  font-size: var(--text-sm);
  margin: var(--space-2) 0 0;
}
.result-lines {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2) var(--space-3);
  font-size: var(--text-sm);
}
.ok {
  color: var(--c-success);
}
.bad {
  color: var(--c-warning);
}
code {
  font-family: var(--font-mono);
  background: var(--c-bg-sunken);
  padding: 0 var(--space-1);
  border-radius: var(--radius-sm);
}
</style>

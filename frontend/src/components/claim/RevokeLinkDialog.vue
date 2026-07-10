<script setup lang="ts">
/**
 * RevokeLinkDialog — Phase K confirmation modal for revoking a Linux
 * account link.
 *
 * The "also revoke the SSH key from the server" checkbox defaults to
 * checked. When the link was established via admin-pushed key
 * (source='admin_prepared_then_ssh'), we strongly recommend keeping it
 * checked — otherwise the key sits stranded in authorized_keys and the
 * user can still ssh in despite their CoreLab access being gone. For
 * source='ssh_challenge' the user's key was theirs to begin with;
 * unchecking is reasonable.
 */
import { computed, ref } from 'vue';
import { NAlert, NButton, NCheckbox, NModal, NSpace, useMessage } from 'naive-ui';

import { revokeLink } from '@/api/accountLinks';
import type { AccountLinkRead } from '@/api/accountLinks';

const props = defineProps<{ link: AccountLinkRead }>();
const emit = defineEmits<{ done: []; cancel: [] }>();
const message = useMessage();

const wasAdminPushed = computed(() => props.link.source === 'admin_prepared_then_ssh');
const revokeKey = ref(true);
const submitting = ref(false);

async function confirm(): Promise<void> {
  submitting.value = true;
  try {
    await revokeLink(props.link.id, 'self', revokeKey.value);
    emit('done');
  } catch (e) {
    message.error(`撤销失败: ${String(e)}`);
  } finally {
    submitting.value = false;
  }
}
</script>

<template>
  <NModal
    :show="true"
    preset="card"
    title="撤销账号关联"
    style="width: 480px"
    :mask-closable="false"
    @close="emit('cancel')"
  >
    <div class="body">
      <p>确认要撤销这条 Linux 账号绑定吗?之后你将不能再用这个账号预约 / 跑脚本。</p>
      <NCheckbox v-model:checked="revokeKey"> 同时从 server 上撤除已推送的 SSH 公钥 </NCheckbox>
      <NAlert v-if="wasAdminPushed && !revokeKey" type="warning" :show-icon="true">
        这条 link 的公钥是当时管理员代你推上去的 — 不撤的话,你仍可绕过平台直接 ssh 登陆,审计 trail
        会出现断裂。强烈建议保持勾选。
      </NAlert>
      <NAlert v-else-if="!wasAdminPushed && !revokeKey" type="info" :show-icon="true">
        这条 link 是你自己用已有的公钥建立的,不撤 key 一般没问题。
      </NAlert>
    </div>
    <template #footer>
      <NSpace justify="end">
        <NButton :disabled="submitting" @click="emit('cancel')">取消</NButton>
        <NButton type="error" :loading="submitting" @click="confirm">确认撤销</NButton>
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
</style>

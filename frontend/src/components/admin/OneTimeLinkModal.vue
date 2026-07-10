<script setup lang="ts">
/**
 * OneTimeLinkModal — shows a single-use link (registration / re-invite /
 * password reset) that the backend returns exactly once, with a copy
 * button. Admin copies it and hands it to the user out-of-band (no email
 * dependency). Shared by the invite, resend-invite and reset-password flows
 * so the "secret link" presentation stays identical everywhere.
 */
import { NAlert, NButton, NModal, useMessage } from 'naive-ui';
import { Clipboard } from 'lucide-vue-next';

const props = defineProps<{
  show: boolean;
  title: string;
  url: string | null;
  /** Override the default "shown once, copy now" hint. */
  description?: string;
}>();

const emit = defineEmits<{
  (e: 'update:show', value: boolean): void;
}>();

const message = useMessage();

async function copy(): Promise<void> {
  if (!props.url) return;
  try {
    await navigator.clipboard.writeText(props.url);
    message.success('已复制链接');
  } catch {
    message.error('无法访问剪贴板,请手动选择复制');
  }
}
</script>

<template>
  <NModal
    :show="show"
    preset="card"
    :title="title"
    style="max-width: 30rem"
    @update:show="(v: boolean) => emit('update:show', v)"
  >
    <div class="otl">
      <NAlert type="success" :show-icon="false">
        {{ description ?? '链接只显示一次,请立即复制并转交给对方。' }}
      </NAlert>
      <pre class="otl-snippet">{{ url }}</pre>
      <div class="otl-actions">
        <NButton type="primary" @click="copy">
          <template #icon>
            <Clipboard :size="14" :stroke-width="2" />
          </template>
          复制链接
        </NButton>
        <NButton @click="emit('update:show', false)">关闭</NButton>
      </div>
    </div>
  </NModal>
</template>

<style scoped>
.otl {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}
.otl-snippet {
  margin: 0;
  padding: var(--space-3);
  background: var(--c-bg-sunken);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-sm);
  font-family: var(--font-mono);
  font-size: 12px;
  word-break: break-all;
  white-space: pre-wrap;
}
.otl-actions {
  display: flex;
  gap: var(--space-2);
  justify-content: flex-end;
}
</style>

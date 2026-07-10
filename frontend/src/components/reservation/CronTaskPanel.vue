<script setup lang="ts">
/**
 * CronTaskPanel — Mode 3: cron-style task that doesn't occupy a GPU.
 *
 * User picks a trigger time + writes a bash script + sets a max runtime.
 * Backend creates a single reservation row with gpu_id NULL; the
 * scheduler tick fires the script at the trigger instant.
 *
 * This used to live in SubmitTask.vue alongside the recommender.
 * Pulled out so it can be embedded inside the PaReserve "Reserve"
 * page as one of three tabs.
 *
 * The earlier SubmitTask layout had two "trigger time" inputs — one
 * inside ScriptEditor and one outside — which confused users. This
 * panel uses a bare NInput textarea + one NDatePicker instead of
 * ScriptEditor so the form has exactly one trigger field.
 */
import { computed, ref } from 'vue';
import { useRouter } from 'vue-router';
import { NButton, NDatePicker, NInput, NInputNumber, useMessage } from 'naive-ui';
import { SendHorizonal } from 'lucide-vue-next';

import * as resApi from '@/api/reservations';
import { extractDetail } from '@/utils/extractDetail';

interface Props {
  paId: number;
  serverId: number | null;
  accountLinkId: number | null;
  linuxUsername: string | null;
  hostname: string | null;
}

const props = defineProps<Props>();

const router = useRouter();
const message = useMessage();

const SCRIPT_MAX_BYTES = 4096;

const script = ref<string>('');
const triggerTs = ref<number | null>(null);
const maxRuntimeSeconds = ref<number>(1800); // 30 min default
const submitting = ref<boolean>(false);

const scriptBytes = computed(() => new TextEncoder().encode(script.value).byteLength);
const scriptByteWarn = computed(() => scriptBytes.value > SCRIPT_MAX_BYTES);

const canSubmit = computed<boolean>(
  () =>
    script.value.trim().length > 0 &&
    !scriptByteWarn.value &&
    triggerTs.value !== null &&
    maxRuntimeSeconds.value >= 60 &&
    props.serverId !== null &&
    props.accountLinkId !== null &&
    !submitting.value,
);

async function submit(): Promise<void> {
  if (!canSubmit.value) return;
  if (props.serverId === null || props.accountLinkId === null || triggerTs.value === null) {
    message.error('工作区或触发时间缺失');
    return;
  }
  const triggerIso = new Date(triggerTs.value).toISOString();
  // end_at = trigger + max_runtime + 60s buffer for the kill grace.
  const endTs = triggerTs.value + (maxRuntimeSeconds.value + 60) * 1000;
  const endIso = new Date(endTs).toISOString();

  submitting.value = true;
  try {
    const items: resApi.ReservationItemInput[] = [
      {
        server_id: props.serverId,
        gpu_id: null,
        start_at: triggerIso,
        end_at: endIso,
        account_link_id: props.accountLinkId,
      },
    ];
    const payload: resApi.ReservationCreateRequest = {
      items,
      script: script.value,
      script_scheduled_start_at: triggerIso,
      script_max_runtime_seconds: maxRuntimeSeconds.value,
      share_script: false,
    };
    const resp = await resApi.createReservationsForPa(props.paId, payload);
    message.success('定时任务已创建');
    await router.push({
      name: 'pa-scripts',
      params: { pa_id: props.paId },
      hash: `#res-${resp.reservations[0]?.id ?? ''}`,
    });
  } catch (err) {
    message.error(extractDetail(err, '创建失败'));
  } finally {
    submitting.value = false;
  }
}
</script>

<template>
  <div class="panel">
    <section class="block">
      <h2 class="block-title">脚本内容</h2>
      <NInput
        :value="script"
        type="textarea"
        :autosize="{ minRows: 5, maxRows: 14 }"
        placeholder="#!/usr/bin/env bash&#10;cd ~/proj && python train.py --epochs 5"
        class="script-input"
        @update:value="(v: string) => (script = v)"
      />
      <span class="bytes" :class="{ warn: scriptByteWarn }">
        {{ scriptBytes }} / {{ SCRIPT_MAX_BYTES }} 字节
      </span>
    </section>

    <section class="block">
      <h2 class="block-title">触发时间</h2>
      <NDatePicker
        v-model:value="triggerTs"
        type="datetime"
        size="medium"
        placeholder="选择触发时间"
        clearable
        class="trigger-input"
      />
    </section>

    <section class="block">
      <h2 class="block-title">最长运行(秒)</h2>
      <NInputNumber
        v-model:value="maxRuntimeSeconds"
        :min="60"
        size="medium"
        class="runtime-input"
      />
      <p class="hint">
        到点会在 server <code class="mono">#{{ serverId ?? '?' }}</code> 上以
        <code class="mono">{{ linuxUsername ?? '?' }}</code> 身份跑脚本,**不占任何
        GPU**。超过最长运行会被强制终止。
      </p>
    </section>

    <div class="confirm-row">
      <NButton
        type="primary"
        size="medium"
        :loading="submitting"
        :disabled="!canSubmit"
        @click="submit"
      >
        <template #icon>
          <SendHorizonal :size="14" :stroke-width="1.75" />
        </template>
        创建定时任务
      </NButton>
    </div>
  </div>
</template>

<style scoped>
.panel {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
  padding-top: var(--space-3);
  max-width: 720px;
}
.block {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
.block-title {
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--c-text-primary);
  margin: 0;
}

.script-input :deep(textarea) {
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: 12px;
  line-height: 1.55;
}
.bytes {
  font-size: 10px;
  color: var(--c-text-tertiary);
  font-family: var(--font-mono, ui-monospace, monospace);
  align-self: flex-end;
}
.bytes.warn {
  color: var(--c-danger, #dc2626);
  font-weight: 600;
}

.trigger-input {
  max-width: 320px;
}
.runtime-input {
  max-width: 200px;
}
.hint {
  font-size: var(--text-xs);
  color: var(--c-text-tertiary);
  margin: 0;
}
.hint code {
  background: var(--c-bg-code, var(--c-bg-sunken));
  padding: 1px 6px;
  border-radius: 4px;
}
.mono {
  font-family: var(--font-mono, ui-monospace, monospace);
}

.confirm-row {
  display: flex;
  justify-content: flex-end;
  padding-top: var(--space-2);
}
</style>

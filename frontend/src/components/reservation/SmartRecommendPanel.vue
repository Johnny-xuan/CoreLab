<script setup lang="ts">
/**
 * SmartRecommendPanel — Mode 2: ask the recommender for the earliest
 * window, then confirm to create the reservation.
 *
 * One of the three tabs inside PaReserve.vue. The grid (Mode 1) and the
 * Cron form (Mode 3) live in the other two. The whole page presents a
 * single "Reserve" concept with three modes, replacing the older
 * separate SubmitTask entry.
 */
import { computed, ref } from 'vue';
import { useRouter } from 'vue-router';
import { NButton, NEmpty, NInputNumber, useMessage } from 'naive-ui';
import { CalendarClock, Cpu, SendHorizonal } from 'lucide-vue-next';

import ScriptEditor, { type ScriptEditorValue } from '@/components/script/ScriptEditor.vue';
import * as resApi from '@/api/reservations';
import { extractDetail } from '@/utils/extractDetail';

interface Props {
  paId: number;
  accountLinkId: number | null;
}

const props = defineProps<Props>();

const router = useRouter();
const message = useMessage();

const gpuCount = ref<number>(1);
const durationHours = ref<number>(1);
const loading = ref<boolean>(false);
const candidates = ref<resApi.RecommendCandidate[]>([]);
const picked = ref<resApi.RecommendCandidate | null>(null);
const attachScript = ref<boolean>(false);
const scriptValue = ref<ScriptEditorValue>({
  script: '',
  scriptScheduledStartAt: null,
  scriptMaxRuntimeSeconds: null,
  shareScript: true,
});
const submitting = ref<boolean>(false);

async function fetchRecommendations(): Promise<void> {
  loading.value = true;
  picked.value = null;
  try {
    const resp = await resApi.recommendReservation({
      gpu_count: gpuCount.value,
      time_limit_seconds: Math.round(durationHours.value * 3600),
      top_k: 3,
    });
    candidates.value = resp.candidates;
    if (resp.candidates.length === 0) {
      message.warning('未来 7 天内没找到合适的时段 — 试试缩短时长或减少卡数');
    }
  } catch (err) {
    message.error(extractDetail(err, '推荐失败'));
  } finally {
    loading.value = false;
  }
}

function pickCandidate(c: resApi.RecommendCandidate): void {
  picked.value = c;
}

async function confirmRecommendation(): Promise<void> {
  if (picked.value === null) return;
  if (props.accountLinkId === null) {
    message.error('未找到当前工作区的账号绑定');
    return;
  }
  submitting.value = true;
  try {
    const items: resApi.ReservationItemInput[] = picked.value.gpu_ids.map((gpuId) => ({
      server_id: picked.value!.server_id,
      gpu_id: gpuId,
      start_at: picked.value!.start_at,
      end_at: picked.value!.end_at,
      account_link_id: props.accountLinkId!,
    }));
    const payload: resApi.ReservationCreateRequest = {
      items,
      script: attachScript.value ? scriptValue.value.script : null,
      script_scheduled_start_at: attachScript.value
        ? scriptValue.value.scriptScheduledStartAt
        : null,
      script_max_runtime_seconds: attachScript.value
        ? scriptValue.value.scriptMaxRuntimeSeconds
        : null,
      share_script: attachScript.value ? scriptValue.value.shareScript : false,
    };
    const resp = await resApi.createReservationsForPa(props.paId, payload);
    message.success(`已创建 ${resp.reservations.length} 段预约`);
    await router.push({
      name: 'pa-reservations',
      params: { pa_id: props.paId },
      query: { highlight: String(resp.reservations[0]?.id ?? '') },
    });
  } catch (err) {
    if (
      typeof err === 'object' &&
      err !== null &&
      'response' in err &&
      typeof (err as { response?: { status?: number } }).response?.status === 'number' &&
      (err as { response: { status: number } }).response.status === 409
    ) {
      message.warning('刚被其他人抢了 — 重新算一次推荐');
      await fetchRecommendations();
    } else {
      message.error(extractDetail(err, '创建失败'));
    }
  } finally {
    submitting.value = false;
  }
}

function formatCandidateRange(c: resApi.RecommendCandidate): string {
  const start = new Date(c.start_at);
  const end = new Date(c.end_at);
  const fmt = (d: Date): string =>
    `${String(d.getMonth() + 1).padStart(2, '0')}/${String(d.getDate()).padStart(2, '0')} ` +
    `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  return `${fmt(start)} → ${fmt(end)}`;
}

function relativeFromNow(iso: string): string {
  const ms = new Date(iso).getTime() - Date.now();
  if (ms < 60_000) return '马上开始';
  const mins = Math.round(ms / 60_000);
  if (mins < 60) return `${mins} 分钟后`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours} 小时后`;
  return `${Math.round(hours / 24)} 天后`;
}

const canConfirm = computed(
  () => picked.value !== null && props.accountLinkId !== null && !submitting.value,
);
</script>

<template>
  <div class="panel">
    <section class="block">
      <h2 class="block-title">资源需求</h2>
      <div class="form-row">
        <label class="field">
          <span class="field-head">几张 GPU?</span>
          <NInputNumber
            v-model:value="gpuCount"
            :min="1"
            :max="8"
            size="medium"
            class="num-input"
          />
        </label>
        <label class="field">
          <span class="field-head">大约跑多久?(小时)</span>
          <NInputNumber
            v-model:value="durationHours"
            :min="0.1"
            :max="24"
            :step="0.5"
            size="medium"
            class="num-input"
          />
        </label>
        <div class="field actions">
          <NButton type="primary" size="medium" :loading="loading" @click="fetchRecommendations">
            <template #icon>
              <Cpu :size="14" :stroke-width="1.75" />
            </template>
            找时段
          </NButton>
        </div>
      </div>
    </section>

    <section v-if="candidates.length > 0" class="block">
      <h2 class="block-title">候选时段</h2>
      <div class="candidate-grid">
        <button
          v-for="(c, i) in candidates"
          :key="`${c.server_id}-${c.start_at}-${i}`"
          type="button"
          class="candidate"
          :class="{ picked: picked === c }"
          @click="pickCandidate(c)"
        >
          <div class="candidate-head">
            <CalendarClock :size="14" :stroke-width="1.75" />
            <span class="when">{{ formatCandidateRange(c) }}</span>
          </div>
          <div class="candidate-meta">
            <span class="rel">{{ relativeFromNow(c.start_at) }}</span>
            <span class="server mono">server #{{ c.server_id }}</span>
          </div>
          <div class="candidate-gpus mono">GPU {{ c.gpu_ids.join(', ') }}</div>
        </button>
      </div>
    </section>

    <section v-if="picked !== null" class="block">
      <h2 class="block-title">附加脚本(可选)</h2>
      <label class="toggle-row">
        <input v-model="attachScript" type="checkbox" />
        <span>到点自动跑一段脚本</span>
      </label>
      <ScriptEditor
        v-if="attachScript"
        v-model:value="scriptValue"
        :window-start-at="picked.start_at"
        :window-end-at="picked.end_at"
        :show-share-script="picked.gpu_ids.length > 1"
        embedded
      />
      <div class="confirm-row">
        <NButton size="medium" @click="picked = null">取消</NButton>
        <NButton
          type="primary"
          size="medium"
          :loading="submitting"
          :disabled="!canConfirm"
          @click="confirmRecommendation"
        >
          <template #icon>
            <SendHorizonal :size="14" :stroke-width="1.75" />
          </template>
          确认创建
        </NButton>
      </div>
    </section>

    <NEmpty
      v-else-if="candidates.length === 0 && !loading"
      description="填好需求后点「找时段」,系统会推荐最近的空闲窗口"
      class="empty-state"
    />
  </div>
</template>

<style scoped>
.panel {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
  padding-top: var(--space-3);
}
.block {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}
.block-title {
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--c-text-primary);
  margin: 0;
}
.form-row {
  display: flex;
  gap: var(--space-4);
  align-items: flex-end;
  flex-wrap: wrap;
}
.field {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  font-size: var(--text-xs);
  color: var(--c-text-secondary);
}
.field-head {
  font-weight: 500;
}
.field.actions {
  justify-content: flex-end;
}
.num-input {
  width: 160px;
}

.candidate-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: var(--space-3);
}
.candidate {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-4);
  background: var(--c-bg-elevated);
  border: 1px solid var(--c-border-default);
  border-radius: var(--radius-md);
  cursor: pointer;
  text-align: left;
  font: inherit;
  color: inherit;
  transition:
    border-color 150ms ease,
    background 150ms ease;
}
.candidate:hover {
  border-color: var(--c-accent);
}
.candidate.picked {
  border-color: var(--c-accent);
  background: color-mix(in oklab, var(--c-accent) 8%, var(--c-bg-elevated));
}
.candidate-head {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--c-text-primary);
}
.when {
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: var(--text-sm);
  font-weight: 500;
}
.candidate-meta {
  display: flex;
  justify-content: space-between;
  font-size: 11px;
  color: var(--c-text-secondary);
}
.rel {
  color: var(--c-text-tertiary);
}
.candidate-gpus {
  font-size: 11px;
  color: var(--c-text-secondary);
  background: var(--c-bg-sunken);
  padding: 2px 8px;
  border-radius: var(--radius-sm);
  align-self: flex-start;
}

.toggle-row {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: var(--text-sm);
  cursor: pointer;
}
.toggle-row input {
  margin: 0;
}

.confirm-row {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
  padding-top: var(--space-2);
}

.empty-state {
  padding: var(--space-8) 0;
}

.mono {
  font-family: var(--font-mono, ui-monospace, monospace);
}
</style>

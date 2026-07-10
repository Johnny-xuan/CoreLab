<script setup lang="ts">
/**
 * ScriptEditor — editable form for the script body + timing knobs.
 *
 * Field-only — no submit / cancel buttons. The parent decides where this
 * lives (PaReserve's advanced area, the Scripts page edit modal, or the
 * Templates page) and what to do with the value. Emits update:value
 * continuously so the parent can validate before letting the user submit.
 *
 * Shape (matches the PATCH /reservations/{id} request fields the Scripts
 * page will fire — see T90 backend extension):
 *   • script              — TEXT, ≤4096 bytes
 *   • scriptScheduledStartAt — optional ISO; must satisfy start <= x < end
 *   • scriptMaxRuntimeSeconds — optional positive int
 *   • shareScript         — multi-reservation "this script runs in every
 *                            range" toggle; hidden when ``showShareScript``
 *                            is false (single-reservation edit modal)
 */
import { computed, watch } from 'vue';
import { NCheckbox, NDatePicker, NInput, NInputNumber } from 'naive-ui';

export interface ScriptEditorValue {
  script: string;
  scriptScheduledStartAt: string | null;
  scriptMaxRuntimeSeconds: number | null;
  shareScript: boolean;
}

const SCRIPT_MAX_BYTES = 4096;

interface Props {
  /** Named v-model — parents bind via ``v-model:value`` so the prop name
   * stays explicit (we already use ``modelValue`` semantics in a number
   * of legacy components for different purposes). */
  value: ScriptEditorValue;
  /** Inclusive lower bound for scriptScheduledStartAt (typically the
   * reservation's start_at). Format: ISO. Used only for the hint string;
   * server-side enforces the constraint. */
  windowStartAt?: string | null;
  /** Exclusive upper bound — typically reservation.end_at. */
  windowEndAt?: string | null;
  /** Hide the "this script runs in every range" checkbox. Defaults to
   * true (Reserve page); set false in the single-reservation edit modal
   * where the flag has no meaning. */
  showShareScript?: boolean;
  /** Visual placement hint — true tightens spacing for embedding in a
   * modal/drawer where the parent owns padding. */
  embedded?: boolean;
}

const props = withDefaults(defineProps<Props>(), {
  windowStartAt: null,
  windowEndAt: null,
  showShareScript: true,
  embedded: false,
});

const emit = defineEmits<{
  (e: 'update:value', value: ScriptEditorValue): void;
}>();

function patch(partial: Partial<ScriptEditorValue>): void {
  emit('update:value', { ...props.value, ...partial });
}

const scriptBytes = computed(() => new TextEncoder().encode(props.value.script).byteLength);
const scriptByteWarn = computed(() => scriptBytes.value > SCRIPT_MAX_BYTES);

const windowHint = computed<string>(() => {
  if (props.windowStartAt === null || props.windowEndAt === null) {
    return '留空 = 预约开始时立即触发';
  }
  return '留空 = 预约开始时立即触发;选了就必须落在预约时段内';
});

// ── 触发时间:ISO 字符串 ↔ 时间戳(给 NDatePicker 用) ──────────
// 模型存 UTC ISO;选择器吃毫秒时间戳、按本地时区显示。toISOString()
// 把本地选的瞬间转回 UTC ISO,后端再归一化为 UTC naive,往返一致。
const scheduledMs = computed<number | null>(() =>
  props.value.scriptScheduledStartAt === null
    ? null
    : new Date(props.value.scriptScheduledStartAt).getTime(),
);
function onScheduledUpdate(ms: number | null): void {
  patch({ scriptScheduledStartAt: ms === null ? null : new Date(ms).toISOString() });
}
// 选择器默认打开到预约开始那一刻,省得用户从今天翻过去。
const defaultPickerMs = computed<number | null>(() =>
  props.windowStartAt === null ? null : new Date(props.windowStartAt).getTime(),
);
// 灰掉预约时段之外的整天(精确到分的边界由后端兜底校验)。
function startOfLocalDay(ts: number): number {
  const d = new Date(ts);
  d.setHours(0, 0, 0, 0);
  return d.getTime();
}
function isDateDisabled(ts: number): boolean {
  if (props.windowStartAt === null || props.windowEndAt === null) return false;
  const lo = startOfLocalDay(new Date(props.windowStartAt).getTime());
  const hi = startOfLocalDay(new Date(props.windowEndAt).getTime());
  const cur = startOfLocalDay(ts);
  return cur < lo || cur > hi;
}

// ── 最长运行:秒 ↔ 小时(更直观) ───────────────────────────────
const maxRuntimeHours = computed<number | null>(() =>
  props.value.scriptMaxRuntimeSeconds === null
    ? null
    : Math.round((props.value.scriptMaxRuntimeSeconds / 3600) * 100) / 100,
);
function onMaxRuntimeHoursUpdate(h: number | null): void {
  patch({ scriptMaxRuntimeSeconds: h === null ? null : Math.round(h * 3600) });
}

// Defensive: if the parent passes a value with byteLength > 4096 (e.g.
// loaded from a template that's grown over time), surface it immediately
// without clobbering — only the bytes counter goes red.
watch(
  () => props.value,
  () => {
    /* no-op; here as a hook point if parents want a callback later */
  },
);
</script>

<template>
  <div class="editor" :class="{ embedded }">
    <label class="field">
      <span class="field-head"> 脚本内容 <span class="hint">bash · 最大 4 KB</span> </span>
      <NInput
        :value="value.script"
        type="textarea"
        :autosize="{ minRows: 4, maxRows: 12 }"
        placeholder="#!/usr/bin/env bash&#10;cd ~/proj && python train.py --epochs 5"
        class="script-input"
        @update:value="(v: string) => patch({ script: v })"
      />
      <span class="bytes" :class="{ warn: scriptByteWarn }">
        {{ scriptBytes }} / {{ SCRIPT_MAX_BYTES }} 字节
      </span>
    </label>

    <div class="row">
      <label class="field">
        <span class="field-head">触发时间(可选)</span>
        <NDatePicker
          :value="scheduledMs"
          type="datetime"
          format="yyyy-MM-dd HH:mm"
          size="small"
          clearable
          :default-value="defaultPickerMs ?? undefined"
          :is-date-disabled="isDateDisabled"
          placeholder="选择日期与时间(可选)"
          class="datetime-input"
          @update:value="onScheduledUpdate"
        />
        <span class="hint">{{ windowHint }}</span>
      </label>

      <label class="field">
        <span class="field-head">最长运行(小时)</span>
        <NInputNumber
          :value="maxRuntimeHours"
          :min="0.1"
          :step="0.5"
          :precision="1"
          size="small"
          placeholder="留空 = 跑到预约结束"
          class="runtime-input"
          @update:value="onMaxRuntimeHoursUpdate"
        >
          <template #suffix>小时</template>
        </NInputNumber>
        <span class="hint">超过这个时长 agent 会自动停掉脚本</span>
      </label>
    </div>

    <NCheckbox
      v-if="showShareScript"
      :checked="value.shareScript"
      size="small"
      @update:checked="(v: boolean) => patch({ shareScript: v })"
    >
      这段脚本对每一段时段都生效(否则只在第一段跑)
    </NCheckbox>
  </div>
</template>

<style scoped>
.editor {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}
.editor.embedded {
  gap: var(--space-2);
}

.field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: var(--text-xs);
  color: var(--c-text-secondary);
}
.field-head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: var(--space-2);
}
.hint {
  font-size: 10px;
  color: var(--c-text-tertiary);
  font-weight: 400;
}
.bytes {
  align-self: flex-end;
  font-size: 10px;
  color: var(--c-text-tertiary);
  font-family: var(--font-mono, ui-monospace, monospace);
}
.bytes.warn {
  color: var(--c-danger, #dc2626);
  font-weight: 600;
}

.script-input :deep(textarea) {
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: 12px;
  line-height: 1.5;
}

.row {
  display: grid;
  grid-template-columns: 1fr 220px;
  gap: var(--space-3);
}
.datetime-input,
.runtime-input {
  width: 100%;
}
@media (max-width: 720px) {
  .row {
    grid-template-columns: 1fr;
  }
}
</style>

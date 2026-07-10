<script setup lang="ts">
/**
 * BottomPanel — Phase H inline reservation confirmation surface.
 *
 * Replaces ReserveConfirmModal. Appears when the user has any cell
 * selected in the grid. Per phase-h-reservation-redesign.md §2/§6:
 *   • Lists the selected ranges (one row per contiguous gpu span)
 *   • Mode toggle: exclusive | shared
 *   • When the selection forces shared (any "shared-remaining" cell
 *     in it), exclusive is locked out and the memory slider appears
 *   • Memory slider: one global value, applied to every selected
 *     range. Slider max = ``sharedMemoryCapMb`` (min of remaining MB
 *     across selected shared cells).
 *   • Buttons: Clear / Confirm
 *
 * Submit stays on page — the parent (PaReserve) clears the selection
 * + lets WS push patch the grid surfaces.
 */

import { computed, ref, watch } from 'vue';
import { NButton, NCheckbox, NInput, NInputNumber, NRadio, NRadioGroup, NSlider } from 'naive-ui';
import { ChevronDown, ChevronRight } from 'lucide-vue-next';
import type { PropType } from 'vue';

export interface BottomPanelDraft {
  gpuId: number;
  startIso: string;
  endIso: string;
  gpuLabel: string;
}

export interface BottomPanelPayload {
  mode: 'exclusive' | 'shared';
  gpuMemoryMb: number | null;
  /** Phase H — advanced section. All optional; when ``script`` is null
   * the reservation is a pure time slot. */
  script: string | null;
  scriptScheduledStartAt: string | null;
  scriptMaxRuntimeSeconds: number | null;
  shareScript: boolean;
}

const SCRIPT_MAX_BYTES = 4096;

const props = defineProps({
  drafts: { type: Array as PropType<BottomPanelDraft[]>, default: () => [] },
  totalGpuHours: { type: Number, default: 0 },
  /** True when the parent's selection contains a shared-remaining
   * cell — mode is forced to shared, the exclusive radio is disabled. */
  sharedForced: { type: Boolean, default: false },
  /** Max GB the user can declare per GPU for this selection. When
   * sharedForced is true this caps the slider; otherwise the slider
   * still uses this as the upper bound (full GPU memory). */
  sharedMemoryCapMb: { type: Number, default: 24576 },
  submitting: { type: Boolean, default: false },
  hasConflicts: { type: Boolean, default: false },
});

const emit = defineEmits<{
  (e: 'clear'): void;
  (e: 'confirm', payload: BottomPanelPayload): void;
}>();

const mode = ref<'exclusive' | 'shared'>('exclusive');
const memoryMb = ref<number>(Math.min(8192, props.sharedMemoryCapMb));

// Advanced (collapsed by default)
const advancedOpen = ref(false);
const scriptEnabled = ref(false);
const script = ref<string>('');
const scriptScheduledStartAt = ref<string | null>(null);
const scriptMaxRuntimeSeconds = ref<number | null>(null);
const shareScript = ref<boolean>(true);

const scriptBytes = computed(() => new TextEncoder().encode(script.value).byteLength);
const scriptByteWarn = computed(() => scriptBytes.value > SCRIPT_MAX_BYTES);

// When the user clears the selection, reset advanced fields too
watch(
  () => props.drafts.length,
  (n) => {
    if (n === 0) {
      scriptEnabled.value = false;
      script.value = '';
      scriptScheduledStartAt.value = null;
      scriptMaxRuntimeSeconds.value = null;
      shareScript.value = true;
      advancedOpen.value = false;
    }
  },
);

const draftsRangeIso = computed(() => {
  if (props.drafts.length === 0)
    return { earliest: null as string | null, latest: null as string | null };
  let e = props.drafts[0]!.startIso;
  let l = props.drafts[0]!.endIso;
  for (const d of props.drafts) {
    if (d.startIso < e) e = d.startIso;
    if (d.endIso > l) l = d.endIso;
  }
  return { earliest: e, latest: l };
});

watch(
  () => props.sharedForced,
  (next) => {
    if (next) {
      mode.value = 'shared';
      memoryMb.value = Math.min(memoryMb.value, props.sharedMemoryCapMb);
      if (memoryMb.value <= 0) memoryMb.value = Math.min(4096, props.sharedMemoryCapMb);
    }
  },
);

watch(
  () => props.sharedMemoryCapMb,
  (cap) => {
    if (memoryMb.value > cap) memoryMb.value = Math.max(1024, cap);
  },
);

const memoryGb = computed({
  get: () => Math.round((memoryMb.value / 1024) * 10) / 10,
  set: (v: number) => {
    memoryMb.value = Math.round(v * 1024);
  },
});

const sliderMaxGb = computed(() => Math.max(1, Math.floor(props.sharedMemoryCapMb / 1024)));

const totalShareDeclaredGbH = computed(() =>
  mode.value === 'shared' ? Math.round(memoryGb.value * props.totalGpuHours * 10) / 10 : 0,
);

const canSubmit = computed(
  () =>
    props.drafts.length > 0 &&
    !props.hasConflicts &&
    (!scriptEnabled.value || (script.value.trim().length > 0 && !scriptByteWarn.value)),
);

function onConfirm(): void {
  if (!canSubmit.value) return;
  emit('confirm', {
    mode: mode.value,
    gpuMemoryMb: mode.value === 'shared' ? memoryMb.value : null,
    script: scriptEnabled.value ? script.value : null,
    scriptScheduledStartAt: scriptEnabled.value ? scriptScheduledStartAt.value : null,
    scriptMaxRuntimeSeconds: scriptEnabled.value ? scriptMaxRuntimeSeconds.value : null,
    shareScript: scriptEnabled.value ? shareScript.value : false,
  });
}

function shortGpuRange(d: BottomPanelDraft): string {
  const fmt = (iso: string): string => {
    const d2 = new Date(iso);
    const hh = d2.getHours().toString().padStart(2, '0');
    const mm = d2.getMinutes().toString().padStart(2, '0');
    return `${hh}:${mm}`;
  };
  return `${fmt(d.startIso)}–${fmt(d.endIso)}`;
}

function dayChip(iso: string): string {
  const d = new Date(iso);
  const m = (d.getMonth() + 1).toString().padStart(2, '0');
  const dd = d.getDate().toString().padStart(2, '0');
  return `${m}/${dd}`;
}
</script>

<template>
  <Transition name="slide-up">
    <section v-if="drafts.length > 0" class="panel" role="region" aria-label="待确认预约">
      <header class="panel-head">
        <div class="head-left">
          <span class="count">
            <strong>{{ drafts.length }}</strong>
            段 · 共 <strong>{{ totalGpuHours.toFixed(0) }}</strong> GPU 时
          </span>
        </div>
        <div class="head-right">
          <NRadioGroup v-model:value="mode" size="small">
            <NRadio value="exclusive" :disabled="sharedForced">独享整张 GPU</NRadio>
            <NRadio value="shared">共享一部分显存</NRadio>
          </NRadioGroup>
        </div>
      </header>

      <ul class="draft-list">
        <li v-for="(d, i) in drafts" :key="`${d.gpuId}-${d.startIso}-${i}`" class="draft-row">
          <span class="day-chip">{{ dayChip(d.startIso) }}</span>
          <span class="time-range mono">{{ shortGpuRange(d) }}</span>
          <span class="gpu-label">{{ d.gpuLabel }}</span>
        </li>
      </ul>

      <Transition name="fade">
        <div v-if="mode === 'shared'" class="shared-block">
          <div class="shared-head">
            <span class="shared-question">你每张卡大约用多少显存?</span>
            <span class="shared-budget mono"
              ><strong>{{ memoryGb }}</strong> GB</span
            >
          </div>
          <NSlider
            v-model:value="memoryGb"
            :min="1"
            :max="sliderMaxGb"
            :step="1"
            :tooltip="false"
            class="shared-slider"
          />
          <div class="shared-hint">
            <span>当前选区每张卡最多可申 {{ sliderMaxGb }} GB</span>
            <span v-if="totalShareDeclaredGbH > 0">
              · {{ drafts.length }} 段 × {{ memoryGb }} GB = {{ totalShareDeclaredGbH }} GB·h 声明额
              (不计费)
            </span>
          </div>
        </div>
      </Transition>

      <!-- Advanced — startup script + scheduled trigger + max runtime -->
      <div class="advanced">
        <button
          class="advanced-toggle"
          type="button"
          :aria-expanded="advancedOpen"
          @click="advancedOpen = !advancedOpen"
        >
          <component
            :is="advancedOpen ? ChevronDown : ChevronRight"
            :size="13"
            :stroke-width="1.75"
          />
          <span>高级 · 自动运行脚本 (cron)</span>
          <span v-if="scriptEnabled" class="advanced-badge">已启用</span>
        </button>
        <Transition name="fade">
          <div v-if="advancedOpen" class="advanced-body">
            <NCheckbox v-model:checked="scriptEnabled" size="small">
              到约定时间自动跑一段脚本
            </NCheckbox>

            <Transition name="fade">
              <div v-if="scriptEnabled" class="advanced-fields">
                <label class="adv-label">
                  <span class="adv-label-head">
                    脚本内容 <span class="adv-hint">bash · 最大 4 KB</span>
                  </span>
                  <NInput
                    v-model:value="script"
                    type="textarea"
                    :autosize="{ minRows: 4, maxRows: 10 }"
                    placeholder="#!/usr/bin/env bash&#10;cd ~/proj && python train.py --epochs 5"
                    class="adv-script"
                  />
                  <span class="adv-bytes" :class="{ warn: scriptByteWarn }">
                    {{ scriptBytes }} / 4096 字节
                  </span>
                </label>

                <div class="adv-row">
                  <label class="adv-label">
                    <span class="adv-label-head">触发时间(可选)</span>
                    <NInput
                      v-model:value="scriptScheduledStartAt"
                      placeholder="2026-06-08T14:00:00"
                      size="small"
                      clearable
                    />
                    <span class="adv-hint">
                      留空 = 预约开始时立即触发;非空必须在
                      {{ draftsRangeIso.earliest?.slice(0, 16) }} ~
                      {{ draftsRangeIso.latest?.slice(0, 16) }} 之间
                    </span>
                  </label>

                  <label class="adv-label">
                    <span class="adv-label-head">最长运行 (秒)</span>
                    <NInputNumber
                      v-model:value="scriptMaxRuntimeSeconds"
                      :min="60"
                      size="small"
                      placeholder="留空 = 跑到预约结束"
                      class="adv-num"
                    />
                  </label>
                </div>

                <NCheckbox v-model:checked="shareScript" size="small">
                  这段脚本对每一段时段都生效(否则只在第一段跑)
                </NCheckbox>
              </div>
            </Transition>
          </div>
        </Transition>
      </div>

      <footer class="panel-foot">
        <div class="foot-left">
          <span v-if="hasConflicts" class="warn">⚠ 选区有冲突,请改时段</span>
          <span v-else class="hint">可继续点选格子,准备好再约定</span>
        </div>
        <div class="foot-right">
          <NButton size="small" :disabled="submitting" @click="$emit('clear')">清除</NButton>
          <NButton
            size="small"
            type="primary"
            :loading="submitting"
            :disabled="!canSubmit || submitting"
            @click="onConfirm"
          >
            约定 {{ drafts.length }} 段 ✓
          </NButton>
        </div>
      </footer>
    </section>
  </Transition>
</template>

<style scoped>
.panel {
  /* Phase J — anchored below the grid, not sticky-stuck to the viewport.
   * Earlier the panel followed the user's scroll position (sticky bottom)
   * but the new layout has the grid inside an elevated work-card, so a
   * sticky panel reads as detached and chases the scroll unpredictably.
   * Plain block flow keeps it locked one slot below the table. */
  margin-top: var(--space-4);
  background: var(--c-bg-elevated);
  border: 1px solid var(--c-border-default);
  border-radius: var(--radius-lg);
  padding: var(--space-4) var(--space-5);
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  box-shadow: var(--shadow-md, 0 6px 16px rgba(0, 0, 0, 0.06));
}
.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}
.count {
  font-size: var(--text-sm);
  color: var(--c-text-secondary);
}
.count strong {
  color: var(--c-text-primary);
}

.draft-list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: var(--space-2) var(--space-3);
  max-height: 96px;
  overflow-y: auto;
}
.draft-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: 4px var(--space-2);
  background: var(--c-bg-sunken);
  border-radius: var(--radius-sm);
  font-size: var(--text-xs);
}
.day-chip {
  font-size: 11px;
  color: var(--c-text-tertiary);
  font-weight: 500;
}
.time-range {
  color: var(--c-text-primary);
  font-weight: 500;
}
.gpu-label {
  color: var(--c-text-secondary);
  margin-left: auto;
  font-size: 11px;
}

.shared-block {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-4);
  background: var(--c-bg-sunken);
  border-radius: var(--radius-md);
  border: 1px dashed var(--c-border-default);
}

.advanced {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
.advanced-toggle {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: transparent;
  border: 0;
  padding: 2px 0;
  cursor: pointer;
  color: var(--c-text-secondary);
  font-size: var(--text-xs);
  font-family: inherit;
  align-self: flex-start;
}
.advanced-toggle:hover {
  color: var(--c-text-primary);
}
.advanced-badge {
  margin-left: 4px;
  padding: 1px 6px;
  border-radius: 999px;
  background: color-mix(in oklab, var(--c-accent) 18%, transparent);
  color: var(--c-accent);
  font-size: 10px;
  font-weight: 600;
}
.advanced-body {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-4);
  background: var(--c-bg-sunken);
  border-radius: var(--radius-md);
  border: 1px dashed var(--c-border-default);
}
.advanced-fields {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  margin-top: var(--space-2);
}
.adv-label {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: var(--text-xs);
  color: var(--c-text-secondary);
}
.adv-label-head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: var(--space-2);
}
.adv-hint {
  font-size: 10px;
  color: var(--c-text-tertiary);
  font-weight: 400;
}
.adv-script :deep(textarea) {
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: 12px;
  line-height: 1.5;
}
.adv-bytes {
  align-self: flex-end;
  font-size: 10px;
  color: var(--c-text-tertiary);
  font-family: var(--font-mono, ui-monospace, monospace);
}
.adv-bytes.warn {
  color: var(--c-danger);
  font-weight: 600;
}
.adv-row {
  display: grid;
  grid-template-columns: 1fr 220px;
  gap: var(--space-3);
}
.adv-num {
  width: 100%;
}
@media (max-width: 720px) {
  .adv-row {
    grid-template-columns: 1fr;
  }
}
.shared-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.shared-question {
  font-size: var(--text-sm);
  color: var(--c-text-primary);
  font-weight: 500;
}
.shared-budget {
  font-size: var(--text-base);
  color: var(--c-text-primary);
}
.shared-slider {
  --n-fill-color: var(--c-accent);
}
.shared-hint {
  font-size: var(--text-xs);
  color: var(--c-text-tertiary);
  display: flex;
  gap: var(--space-1);
  flex-wrap: wrap;
}

.panel-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}
.foot-left .hint {
  font-size: var(--text-xs);
  color: var(--c-text-tertiary);
}
.foot-left .warn {
  font-size: var(--text-xs);
  color: var(--c-danger);
}
.foot-right {
  display: flex;
  gap: var(--space-2);
}

.slide-up-enter-active,
.slide-up-leave-active {
  transition:
    transform 200ms ease,
    opacity 200ms ease;
}
.slide-up-enter-from,
.slide-up-leave-to {
  transform: translateY(8px);
  opacity: 0;
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 180ms ease;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>

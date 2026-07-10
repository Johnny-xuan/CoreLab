<script setup lang="ts">
/**
 * GridCell — single reservation slot.
 *
 * Was an inline ``h()`` render-function in ReservationGrid.vue. Now an
 * SFC so the cell render reads as template instead of VNode soup. The
 * pure presentation helpers (label, tooltip, class, inline style,
 * toggleable predicate) live in gridStyles.ts and are shared with the
 * Legend swatch.
 *
 * Two render modes:
 *   - normal mode: `<button>` for an interactive reservation slot
 *   - placeholder mode: `<div>` hatched filler, kept here so the
 *     `.cell-*` style block stays self-contained in this SFC.
 */
import { computed } from 'vue';
import { X } from 'lucide-vue-next';

import type { DerivedCell } from './gridUtils';
import {
  cellClassMap,
  cellShortLabel,
  isCellToggleable,
  sharedGradientStyle,
  tooltipForCell,
} from './gridStyles';

const props = withDefaults(
  defineProps<{
    cell?: DerivedCell;
    conflictKeys?: Set<string>;
    serverOffline?: boolean;
    placeholder?: boolean;
  }>(),
  {
    cell: undefined,
    conflictKeys: () => new Set<string>(),
    serverOffline: false,
    placeholder: false,
  },
);

const emit = defineEmits<{
  (e: 'toggle', cell: DerivedCell): void;
  (e: 'cancel', cell: DerivedCell): void;
}>();

const toggleable = computed(
  () => props.cell !== undefined && isCellToggleable(props.cell, props.serverOffline),
);
const classMap = computed(() => {
  if (props.cell === undefined) return {};
  return cellClassMap(props.cell, {
    conflictKeys: props.conflictKeys,
    serverOffline: props.serverOffline,
  });
});
const style = computed(() => (props.cell !== undefined ? sharedGradientStyle(props.cell) : {}));
const label = computed(() => (props.cell !== undefined ? cellShortLabel(props.cell) : ''));
const tooltip = computed(() => (props.cell !== undefined ? tooltipForCell(props.cell) : ''));

function onClick(): void {
  if (!toggleable.value || props.cell === undefined) return;
  emit('toggle', props.cell);
}

function onKeyDown(ev: KeyboardEvent): void {
  if (ev.key === 'Enter' || ev.key === ' ') {
    ev.preventDefault();
    onClick();
  }
}

function onCancel(ev: MouseEvent): void {
  ev.stopPropagation();
  if (props.cell !== undefined) emit('cancel', props.cell);
}
</script>

<template>
  <div v-if="placeholder" class="cell cell-placeholder" aria-hidden="true" role="presentation" />
  <button
    v-else
    type="button"
    :class="classMap"
    :style="style"
    :title="tooltip"
    :tabindex="toggleable ? 0 : -1"
    :aria-disabled="toggleable ? 'false' : 'true'"
    @click="onClick"
    @keydown="onKeyDown"
  >
    <span v-if="label !== ''" class="cell-text">{{ label }}</span>
    <button
      v-if="cell !== undefined && cell.state === 'mine-scheduled'"
      type="button"
      class="cell-cancel-btn"
      title="取消预约"
      @click="onCancel"
    >
      <X :size="10" :stroke-width="2" />
    </button>
  </button>
</template>

<style scoped>
.cell {
  position: relative;
  border: 0;
  border-right: 1px solid var(--c-border-subtle);
  border-bottom: 1px solid var(--c-border-subtle);
  background: var(--c-bg-canvas);
  cursor: pointer;
  padding: 0;
  font: inherit;
  color: inherit;
  display: flex;
  align-items: center;
  justify-content: center;
  transition:
    background-color 80ms ease,
    box-shadow 80ms ease;
}
.cell:focus-visible {
  outline: 2px solid var(--c-accent);
  outline-offset: -2px;
}

.cell-daytime {
  background: var(--c-bg-canvas);
}
.cell:not(.cell-daytime) {
  background: color-mix(in oklab, var(--c-bg-canvas) 85%, #e2e8f0);
}

.cell-idle:hover {
  background: color-mix(in oklab, var(--c-bg-canvas) 80%, var(--c-accent));
}

.cell-selecting {
  background: color-mix(in oklab, var(--c-bg-canvas) 70%, var(--c-accent));
  box-shadow: inset 0 0 0 2px var(--c-accent);
}

/* shared-remaining background is set inline by sharedGradientStyle()
 * — a left→right split where the left segment width = used / total %.
 * This single-colour rule is the fallback when gpuTotalMb is unknown. */
.cell-shared-remaining {
  background-color: color-mix(in oklab, var(--c-bg-canvas) 85%, #facc15);
}
.cell-shared-remaining:hover {
  filter: brightness(0.96);
}

/* "Mine" cells: green block with theme-aware text colour. */
.cell-mine-scheduled {
  background: color-mix(in oklab, #16a34a 30%, var(--c-bg-canvas));
  color: var(--c-text-primary);
  font-weight: 600;
  cursor: default;
}
.cell-mine-active {
  background: color-mix(in oklab, #16a34a 50%, var(--c-bg-canvas));
  color: var(--c-text-primary);
  font-weight: 700;
  cursor: default;
}

/* "Others" cells: high-contrast block with inverse text. */
.cell-others-scheduled,
.cell-others-active {
  background: color-mix(in oklab, var(--c-text-primary) 55%, var(--c-bg-canvas));
  color: var(--c-text-inverse);
  cursor: not-allowed;
  font-weight: 500;
}
.cell-others-active {
  background: color-mix(in oklab, var(--c-text-primary) 75%, var(--c-bg-canvas));
}

/* Past slots: opacity-fade. The NOW red line carries the temporal cue. */
.cell-disabled-past {
  cursor: not-allowed;
  color: var(--c-text-tertiary);
  opacity: 0.6;
}

.cell-server-offline {
  background: repeating-linear-gradient(
    45deg,
    color-mix(in oklab, #94a3b8 20%, var(--c-bg-canvas)) 0 6px,
    var(--c-bg-canvas) 6px 12px
  );
  cursor: not-allowed;
  color: var(--c-text-tertiary);
}

/* Placeholder column padding: when this page has < 7 GPUs, the extra
 * columns render as a calm hatched fill so the 7-wide footprint stays
 * the same shape on every page. */
.cell-placeholder {
  background: repeating-linear-gradient(
    45deg,
    color-mix(in oklab, var(--c-bg-sunken) 70%, transparent) 0 5px,
    transparent 5px 10px
  );
  cursor: not-allowed;
  pointer-events: none;
  opacity: 0.55;
}

.cell-conflict {
  box-shadow: inset 0 0 0 2px var(--c-danger);
}

.cell-cross-night::after {
  content: '';
  position: absolute;
  top: 2px;
  right: 2px;
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: var(--c-accent);
}

.cell-text {
  font-size: 10px;
  letter-spacing: -0.02em;
  pointer-events: none;
}

.cell-cancel-btn {
  position: absolute;
  top: 1px;
  right: 1px;
  width: 14px;
  height: 14px;
  display: none;
  align-items: center;
  justify-content: center;
  border: 0;
  background: rgba(220, 38, 38, 0.85);
  color: #fff;
  border-radius: 3px;
  cursor: pointer;
  padding: 0;
}
.cell-mine-scheduled:hover .cell-cancel-btn {
  display: inline-flex;
}
</style>

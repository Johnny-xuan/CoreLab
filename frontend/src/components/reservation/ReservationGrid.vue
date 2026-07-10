<script setup lang="ts">
/**
 * ReservationGrid — Phase H v0.3.
 *
 * Time × GPU grid for one day, 1-hour slot granularity (24 rows).
 * Per phase-h-reservation-redesign.md:
 *   • Click cell = toggle selection (no drag-frame, no Ctrl/Shift)
 *   • Cell states: idle / selecting / shared-remaining / mine-* / others-* / past
 *   • Server-grouped column headers (header row 1 spans co-server cols)
 *   • Fixed-width columns (GPU 96px / time 56px) — grid never stretches
 *     to fill its parent, so a single GPU column doesn't blow up
 *   • NOW red line + early-morning + late-night muted band
 *   • Mine cells expose a hover × to cancel directly from the grid
 */

import { computed, onUnmounted, ref } from 'vue';
import type { PropType } from 'vue';
import { Moon, Sun } from 'lucide-vue-next';

import {
  DAYTIME_END_HOUR,
  DAYTIME_START_HOUR,
  SLOTS_PER_DAY,
  buildGrid,
  cellKey,
  isDaytimeSlot,
  slotLabel,
  type DerivedCell,
} from './gridUtils';
import GridCell from './GridCell.vue';
import NowLine from './NowLine.vue';
import type { ReservationRead } from '@/api/reservations';
import type { GpuRead, ServerRead } from '@/api/servers';

export interface GpuColumn {
  gpu: GpuRead;
  server: ServerRead;
}
/** A blank column that keeps the grid's horizontal footprint fixed
 * when the page has fewer GPUs than ``GPUS_PER_PAGE`` — renders as a
 * disabled hatched slot so single-GPU pages still look like the full
 * 7-wide grid. */
export interface PlaceholderColumn {
  placeholder: true;
  key: string;
}
export type GridColumn = GpuColumn | PlaceholderColumn;

function isPlaceholder(c: GridColumn): c is PlaceholderColumn {
  return (c as PlaceholderColumn).placeholder === true;
}

const props = defineProps({
  dayIso: { type: String, required: true },
  columns: { type: Array as PropType<GridColumn[]>, default: () => [] },
  reservations: { type: Array as PropType<ReservationRead[]>, default: () => [] },
  currentUserId: { type: Number, required: true },
  selecting: { type: Object as PropType<Set<string>>, required: true },
  conflictKeys: { type: Object as PropType<Set<string>>, default: () => new Set<string>() },
  serverOffline: { type: Boolean, default: false },
});

function colKey(col: GridColumn): string {
  return isPlaceholder(col) ? col.key : `g-${col.gpu.id}`;
}

const emit = defineEmits<{
  (e: 'update:selecting', value: Set<string>): void;
  (e: 'cancelReservation', reservationId: number): void;
}>();

const realColumns = computed<GpuColumn[]>(() =>
  props.columns.filter((c): c is GpuColumn => !isPlaceholder(c)),
);
const gpuIds = computed(() => realColumns.value.map((c) => c.gpu.id));
const gpuTotalMemoryMb = computed<Record<number, number>>(() => {
  const out: Record<number, number> = {};
  for (const c of realColumns.value) out[c.gpu.id] = c.gpu.memory_total_mb ?? 0;
  return out;
});

const nowTick = ref(new Date());
const nowTimer = setInterval(() => {
  nowTick.value = new Date();
}, 30_000);
onUnmounted(() => clearInterval(nowTimer));

const grid = computed(() =>
  buildGrid({
    dayIso: props.dayIso,
    gpuIds: gpuIds.value,
    reservations: props.reservations,
    selecting: props.selecting,
    currentUserId: props.currentUserId,
    now: nowTick.value,
    gpuTotalMemoryMb: gpuTotalMemoryMb.value,
  }),
);

interface ServerHeaderSpan {
  key: string;
  hostname: string;
  span: number;
  status: ServerRead['status'];
  placeholder?: boolean;
}
const serverHeaderSpans = computed<ServerHeaderSpan[]>(() => {
  const out: ServerHeaderSpan[] = [];
  let cur: ServerHeaderSpan | null = null;
  let idx = 0;
  for (const col of props.columns) {
    if (isPlaceholder(col)) {
      if (cur !== null && cur.placeholder === true) {
        cur.span += 1;
      } else {
        cur = {
          key: `ph-${idx}`,
          hostname: '',
          span: 1,
          status: 'offline' as ServerRead['status'],
          placeholder: true,
        };
        out.push(cur);
      }
      idx += 1;
      continue;
    }
    if (cur !== null && cur.placeholder !== true && cur.key === `s-${col.server.id}`) {
      cur.span += 1;
    } else {
      cur = {
        key: `s-${col.server.id}`,
        hostname: col.server.hostname ?? `server-${col.server.id}`,
        span: 1,
        status: col.server.status,
      };
      out.push(cur);
    }
    idx += 1;
  }
  return out;
});

function onCellToggle(cell: DerivedCell): void {
  const next = new Set(props.selecting);
  const key = cellKey(cell.gpuId, cell.slotIndex);
  if (next.has(key)) next.delete(key);
  else next.add(key);
  emit('update:selecting', next);
}

function onCellCancel(cell: DerivedCell): void {
  const mine = cell.reservations.find((r) => r.user_id === props.currentUserId);
  if (mine === undefined) return;
  emit('cancelReservation', mine.id);
}

const TIME_COL_PX = 64;
const ROW_PX = 28;

function blankCell(gpuId: number, slotIndex: number): DerivedCell {
  return { gpuId, slotIndex, state: 'idle', reservations: [] };
}
</script>

<template>
  <div class="grid" role="grid" aria-label="预约网格">
    <!-- header row 1: server hostnames spanning their GPU columns -->
    <div class="head-row server-row" role="row">
      <div class="time-corner" />
      <div
        v-for="span in serverHeaderSpans"
        :key="span.key"
        class="server-head"
        :class="{ 'server-head-placeholder': span.placeholder }"
        :style="{ gridColumn: `span ${span.span}` }"
        :data-status="span.status"
      >
        <span class="server-hostname mono">{{ span.hostname }}</span>
      </div>
    </div>

    <!-- header row 2: GPU labels -->
    <div class="head-row gpu-row" role="row">
      <div class="time-corner" />
      <div
        v-for="col in columns"
        :key="colKey(col)"
        class="gpu-head"
        :class="{ 'gpu-head-placeholder': isPlaceholder(col) }"
      >
        <span class="gpu-label">{{ isPlaceholder(col) ? '—' : `GPU ${col.gpu.gpu_index}` }}</span>
      </div>
    </div>

    <!-- body grid -->
    <div class="body" :style="{ '--cols': columns.length }">
      <template v-for="slotIndex in SLOTS_PER_DAY" :key="slotIndex">
        <div
          class="time-cell"
          :class="{
            'time-daytime': isDaytimeSlot(slotIndex - 1),
            'time-pre-dawn': slotIndex - 1 < DAYTIME_START_HOUR,
            'time-late-night': slotIndex - 1 >= DAYTIME_END_HOUR,
          }"
        >
          <component
            :is="isDaytimeSlot(slotIndex - 1) ? Sun : Moon"
            class="time-icon"
            :size="9"
            :stroke-width="1.75"
          />
          <span class="time-label mono">{{ slotLabel(slotIndex - 1) }}</span>
        </div>
        <template v-for="col in columns" :key="`c-${colKey(col)}-${slotIndex - 1}`">
          <GridCell v-if="isPlaceholder(col)" placeholder />
          <GridCell
            v-else
            :cell="
              grid.get(cellKey(col.gpu.id, slotIndex - 1)) ?? blankCell(col.gpu.id, slotIndex - 1)
            "
            :conflict-keys="conflictKeys"
            :server-offline="serverOffline"
            @toggle="onCellToggle"
            @cancel="onCellCancel"
          />
        </template>
      </template>

      <NowLine :day-iso="dayIso" :row-height-px="ROW_PX" :time-col-width-px="TIME_COL_PX" />
    </div>
  </div>
</template>

<style scoped>
.grid {
  --row-px: 28px;
  --col-px: 96px;
  --time-px: 64px;
  display: flex;
  flex-direction: column;
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-md);
  overflow: hidden;
  background: var(--c-bg-canvas);
  font-size: 11px;
  position: relative;
  width: fit-content;
  max-width: 100%;
}

.head-row {
  display: grid;
  grid-template-columns: var(--time-px) repeat(v-bind('columns.length'), var(--col-px));
  border-bottom: 1px solid var(--c-border-subtle);
  background: var(--c-bg-elevated);
}
.time-corner {
  background: var(--c-bg-sunken);
  border-right: 1px solid var(--c-border-subtle);
}

.server-row .server-head {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 6px var(--space-2);
  border-right: 1px solid var(--c-border-subtle);
  background: var(--c-bg-sunken);
}
.server-row .server-head:last-child {
  border-right: 0;
}
.server-hostname {
  font-size: 11px;
  color: var(--c-text-secondary);
  font-weight: 600;
}
.server-head[data-status='offline'] .server-hostname {
  color: var(--c-danger);
}

.gpu-row .gpu-head {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 6px 0 8px;
  border-right: 1px solid var(--c-border-subtle);
  border-bottom: 1px solid var(--c-border-subtle);
}
.gpu-row .gpu-head:last-child {
  border-right: 0;
}
.gpu-label {
  font-size: 11px;
  color: var(--c-text-primary);
  font-weight: 500;
}

.body {
  position: relative;
  display: grid;
  grid-template-columns: var(--time-px) repeat(var(--cols), var(--col-px));
  grid-auto-rows: var(--row-px);
}

.time-cell {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 5px;
  padding: 0 8px;
  border-right: 1px solid var(--c-border-subtle);
  border-bottom: 1px solid var(--c-border-subtle);
  background: var(--c-bg-sunken);
}
.time-cell.time-pre-dawn,
.time-cell.time-late-night {
  background: color-mix(in oklab, var(--c-bg-sunken) 65%, #cbd5e1);
}
.time-label {
  font-size: 10px;
  color: var(--c-text-tertiary);
}
/* Sun / Moon stay in the same tertiary grey as the time label —
 * symbol contrast is enough to read day↔night without a colour shift. */
.time-icon {
  color: var(--c-text-tertiary);
  opacity: 0.55;
  flex-shrink: 0;
}

/* Placeholder column header styling — the cell-* + .now-* /
 * .cell-cancel-btn / .cell-text styles all live in GridCell.vue and
 * NowLine.vue now (self-contained sub-components). */
.gpu-head-placeholder .gpu-label,
.server-head-placeholder .server-hostname {
  color: var(--c-text-tertiary);
  opacity: 0.5;
}
.server-head-placeholder {
  background: var(--c-bg-elevated);
}

.gpu-head:last-child {
  border-right: 0;
}
</style>

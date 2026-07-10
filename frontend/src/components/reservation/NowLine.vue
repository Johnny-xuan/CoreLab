<script setup lang="ts">
/**
 * NowLine — the horizontal red marker that runs through the grid at
 * the current local time, with a small "NOW HH:MM" chip on the left.
 *
 * Self-contained: ticks its own minute timer; parent only needs to
 * give it the day being rendered + the grid's per-slot row height.
 * Returns nothing when "now" falls outside the rendered day.
 */
import { computed, onUnmounted, ref } from 'vue';

const props = withDefaults(
  defineProps<{
    dayIso: string;
    rowHeightPx?: number;
    timeColWidthPx?: number;
    tickMs?: number;
  }>(),
  {
    rowHeightPx: 28,
    timeColWidthPx: 64,
    tickMs: 30_000,
  },
);

const nowTick = ref(new Date());
const timer = setInterval(() => {
  nowTick.value = new Date();
}, props.tickMs);
onUnmounted(() => clearInterval(timer));

const state = computed(() => {
  const now = nowTick.value;
  const dayStart = new Date(`${props.dayIso}T00:00:00`);
  const hoursIntoDay = (now.getTime() - dayStart.getTime()) / 3_600_000;
  if (hoursIntoDay < 0 || hoursIntoDay >= 24) return null;
  const hh = now.getHours().toString().padStart(2, '0');
  const mm = now.getMinutes().toString().padStart(2, '0');
  return { top: hoursIntoDay * props.rowHeightPx, label: `现在 ${hh}:${mm}` };
});
</script>

<template>
  <div
    v-if="state !== null"
    class="now-line"
    :style="{ top: `${state.top}px`, left: `${timeColWidthPx}px` }"
  >
    <span class="now-chip" :style="{ left: `-${timeColWidthPx}px`, width: `${timeColWidthPx}px` }">
      {{ state.label }}
    </span>
  </div>
</template>

<style scoped>
.now-line {
  position: absolute;
  right: 0;
  height: 0;
  border-top: 1.5px solid #dc2626;
  pointer-events: none;
  z-index: 3;
}
.now-chip {
  position: absolute;
  top: -8px;
  text-align: center;
  background: #dc2626;
  color: #fff;
  font-size: 10px;
  padding: 1px 0;
  font-weight: 600;
  letter-spacing: 0.02em;
}
</style>

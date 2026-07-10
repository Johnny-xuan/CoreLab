<script setup lang="ts">
/**
 * ReservationLegend — Phase H v0.3.
 *
 * Decoder ring for the grid's cell colours. Swatches mirror the
 * cell-* styles in ReservationGrid.vue at a small scale so users can
 * tell at a glance what each shade means without hover-tooltipping
 * every cell.
 *
 * The shared-cell gradient is bound to the SAME constants the grid
 * cells render — see gridStyles.ts — so any colour tweak there flows
 * straight into the legend swatch.
 */
import { SHARED_FREE_COLOR, SHARED_USED_COLOR } from './gridStyles';

const sharedSwatchStyle = {
  backgroundImage: `linear-gradient(to right, ${SHARED_USED_COLOR} 0 50%, ${SHARED_FREE_COLOR} 50% 100%)`,
};
</script>

<template>
  <div class="legend">
    <span class="legend-title">图例</span>
    <div class="legend-item">
      <span class="sw sw-idle" />
      <span>空闲</span>
    </div>
    <div class="legend-item">
      <span class="sw sw-selecting" />
      <span>已选</span>
    </div>
    <div class="legend-item">
      <span class="sw" :style="sharedSwatchStyle" />
      <span>共享 · 左侧已占 / 右侧剩余</span>
    </div>
    <div class="legend-item">
      <span class="sw sw-mine" />
      <span>你的预约</span>
    </div>
    <div class="legend-item">
      <span class="sw sw-others" />
      <span>他人占用</span>
    </div>
  </div>
</template>

<style scoped>
.legend {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex-wrap: wrap;
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--c-border-subtle);
  border-radius: var(--radius-md);
  background: var(--c-bg-elevated);
}

.legend-title {
  font-size: var(--text-xs);
  color: var(--c-text-secondary);
  font-weight: 600;
  letter-spacing: 0.02em;
}

.legend-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: var(--text-xs);
  color: var(--c-text-secondary);
}

.sw {
  width: 14px;
  height: 14px;
  border-radius: 3px;
  border: 1px solid var(--c-border-subtle);
  display: inline-block;
  flex-shrink: 0;
}

.sw-idle {
  background: var(--c-bg-canvas);
}

.sw-selecting {
  background: color-mix(in oklab, var(--c-bg-canvas) 70%, var(--c-accent));
  box-shadow: inset 0 0 0 1.5px var(--c-accent);
}

/* shared swatch is rendered inline from gridStyles.ts SHARED_*_COLOR
 * — keeping the gradient single-source with the grid cells. */
.sw-mine {
  background: color-mix(in oklab, #16a34a 30%, var(--c-bg-canvas));
  border-color: color-mix(in oklab, #16a34a 50%, var(--c-bg-canvas));
}

.sw-others {
  background: color-mix(in oklab, var(--c-text-primary) 45%, var(--c-bg-canvas));
  border-color: color-mix(in oklab, var(--c-text-primary) 30%, transparent);
}
</style>

/**
 * Pure presentation helpers for the reservation grid.
 *
 * Anything that maps a DerivedCell to "what the user sees" — label,
 * tooltip, inline style, class map — lives here so the Vue component
 * and the Legend swatch share a single source of truth. No DOM, no
 * Vue reactivity; unit-testable in isolation.
 */
import { cellKey, isDaytimeSlot, type DerivedCell } from './gridUtils';

/** Saturated amber for the "used" half of the shared-cell gradient. */
export const SHARED_USED_COLOR = 'color-mix(in oklab, var(--c-bg-canvas) 55%, #f59e0b)';
/** Pale amber for the "remaining" half — sits softer than the used side. */
export const SHARED_FREE_COLOR = 'color-mix(in oklab, var(--c-bg-canvas) 92%, #facc15)';

/** Whether the cell can be toggled by a click — controls cursor + tabindex.
 * Centralised so the grid + tooltip layer + keyboard handler all agree. */
export function isCellToggleable(cell: DerivedCell, serverOffline: boolean): boolean {
  if (serverOffline) return false;
  if (cell.state === 'disabled-past') return false;
  if (cell.state === 'others-active' || cell.state === 'others-scheduled') return false;
  if (cell.state === 'mine-active') return false;
  return true;
}

/** Short text rendered inside the cell button. Empty string = no label. */
export function cellShortLabel(cell: DerivedCell): string {
  if (cell.state === 'shared-remaining') {
    const total = cell.gpuTotalMb ?? 0;
    if (total <= 0) {
      const remGb = Math.round((cell.sharedRemainingMb ?? 0) / 1024);
      return `剩${remGb}G`;
    }
    const remPct = Math.round(((cell.sharedRemainingMb ?? 0) / total) * 100);
    return `剩${remPct}%`;
  }
  if (cell.state === 'mine-scheduled' || cell.state === 'mine-active') return '你';
  if (cell.state === 'others-scheduled' || cell.state === 'others-active') {
    return cell.reservations.length === 1 ? `#${cell.reservations[0]?.user_id ?? ''}` : '占';
  }
  return '';
}

/** Native title= tooltip — long-form explanation of the cell state. */
export function tooltipForCell(cell: DerivedCell): string {
  if (cell.state === 'disabled-past') return '已过期';
  if (cell.state === 'idle') return '空闲 · 单击选中';
  if (cell.state === 'selecting') return '已选 · 再点取消';
  if (cell.state === 'shared-remaining') {
    const total = cell.gpuTotalMb ?? 0;
    const remMb = cell.sharedRemainingMb ?? 0;
    if (total <= 0) {
      const remGb = (remMb / 1024).toFixed(0);
      return `共享有余 · 剩 ${remGb} GB · 单击选中(锁共享模式)`;
    }
    const remPct = Math.round((remMb / total) * 100);
    const remGb = (remMb / 1024).toFixed(1);
    const totalGb = (total / 1024).toFixed(0);
    return `共享有余 · 剩 ${remPct}%(${remGb} / ${totalGb} GB)· 单击选中(锁共享模式)`;
  }
  if (cell.state === 'mine-scheduled') return '你的预约 · 计划中(hover 出 × 可取消)';
  if (cell.state === 'mine-active') return '你的预约 · 进行中';
  if (cell.state === 'others-scheduled') {
    const names = cell.reservations.map((r) => `#${r.user_id}`).join(', ');
    return `他人计划中(${names})`;
  }
  if (cell.state === 'others-active') {
    const names = cell.reservations.map((r) => `#${r.user_id}`).join(', ');
    return `他人进行中(${names})`;
  }
  return '';
}

/** Inline style for the shared-remaining cell — split-gradient progress
 * bar whose hard stop reflects used/total memory ratio. Returns {} for
 * other states (CSS class default applies). */
export function sharedGradientStyle(cell: DerivedCell): Record<string, string> {
  if (cell.state !== 'shared-remaining') return {};
  const total = cell.gpuTotalMb ?? 0;
  if (total <= 0) return {};
  const usedPct = Math.max(0, Math.min(100, Math.round(((cell.sharedUsedMb ?? 0) / total) * 100)));
  return {
    backgroundImage: `linear-gradient(to right, ${SHARED_USED_COLOR} 0%, ${SHARED_USED_COLOR} ${usedPct}%, ${SHARED_FREE_COLOR} ${usedPct}%, ${SHARED_FREE_COLOR} 100%)`,
  };
}

/** Class map for the cell button — drives CSS state styling. */
export function cellClassMap(
  cell: DerivedCell,
  opts: { conflictKeys: Set<string>; serverOffline: boolean },
): Record<string, boolean> {
  const isConflict = opts.conflictKeys.has(cellKey(cell.gpuId, cell.slotIndex));
  return {
    cell: true,
    [`cell-${cell.state}`]: true,
    'cell-conflict': isConflict,
    'cell-cross-night': cell.isCrossNight === true,
    'cell-daytime': isDaytimeSlot(cell.slotIndex),
    'cell-server-offline': opts.serverOffline,
  };
}

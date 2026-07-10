/**
 * Pure helpers for the reservation grid.
 *
 * Phase H (docs/phase-start/phase-h-reservation-redesign.md §3):
 * 1-hour slot granularity — 24 rows per day. Kept separate from the
 * Vue component so the slot math + cell-state derivation can be
 * unit-tested without a DOM.
 *
 * The "day" the grid renders is anchored to a single local date
 * (YYYY-MM-DD in the browser's local TZ); each row is a 60-minute
 * slot, 24 per day. A slot is identified by its zero-based
 * ``slotIndex`` (00:00 = 0).
 */

import type { ReservationRead } from '@/api/reservations';

export const SLOT_MINUTES = 60;
export const SLOTS_PER_DAY = 24;
/** Local hour at which the "daytime" band starts (rows ≥ this draw on
 * the bright background; rows below draw on the muted overnight band). */
export const DAYTIME_START_HOUR = 8;
/** Local hour at which the daytime band ends (rows < this stay bright). */
export const DAYTIME_END_HOUR = 22;

export interface SlotKey {
  gpuId: number;
  slotIndex: number;
}

export type CellState =
  | 'idle'
  | 'selecting'
  | 'mine-scheduled'
  | 'mine-active'
  | 'others-scheduled'
  | 'others-active'
  /** Phase H §4 — at least one covering reservation is shared and
   * there is still GPU memory left on this cell. The cell can still be
   * selected; doing so locks the bottom panel into shared mode. */
  | 'shared-remaining'
  | 'locked'
  | 'disabled-past';

export interface DerivedCell {
  gpuId: number;
  slotIndex: number;
  state: CellState;
  /** Reservation rows that touch this slot — for tooltip lookup. */
  reservations: ReservationRead[];
  /** Phase 7 FU-23 — true when at least one covering reservation
   * belongs to a group_id that also has cells on the adjacent day.
   * Driven by the ``crossNightGroupIds`` opt to ``buildGrid``. */
  isCrossNight?: boolean;
  /** Phase H §4 — sum of declared memory across covering shared rows. */
  sharedUsedMb?: number;
  /** Phase H §4 — total GPU memory; needed so the cell + bottom panel
   * can both render "剩 N GB". 0 means unknown. */
  gpuTotalMb?: number;
  /** Phase H §4 — convenience: max(0, gpuTotalMb - sharedUsedMb). */
  sharedRemainingMb?: number;
}

/** Anchor a day's UTC bounds given a local YYYY-MM-DD date string. */
export function dayBounds(dayIso: string): { start: Date; end: Date } {
  // dayIso interpreted in local time (matches how the user picks "Today").
  const start = new Date(`${dayIso}T00:00:00`);
  const end = new Date(start.getTime() + SLOTS_PER_DAY * SLOT_MINUTES * 60_000);
  return { start, end };
}

/** Convert a slotIndex on a given day to absolute [start, end) ISO strings. */
export function slotToIsoRange(
  dayIso: string,
  slotIndex: number,
): { startIso: string; endIso: string } {
  const { start } = dayBounds(dayIso);
  const slotStart = new Date(start.getTime() + slotIndex * SLOT_MINUTES * 60_000);
  const slotEnd = new Date(slotStart.getTime() + SLOT_MINUTES * 60_000);
  return { startIso: slotStart.toISOString(), endIso: slotEnd.toISOString() };
}

/** Render label for the left axis (00:00, 01:00, … 23:00). */
export function slotLabel(slotIndex: number): string {
  const totalMinutes = slotIndex * SLOT_MINUTES;
  const h = Math.floor(totalMinutes / 60)
    .toString()
    .padStart(2, '0');
  const m = (totalMinutes % 60).toString().padStart(2, '0');
  return `${h}:${m}`;
}

/** True for slot rows that fall inside the bright local-time daytime
 * band (DAYTIME_START_HOUR ≤ hour < DAYTIME_END_HOUR). The grid uses
 * this to keep the early-morning / late-night band visually quieter
 * per Phase H redesign §3.8. */
export function isDaytimeSlot(slotIndex: number): boolean {
  return slotIndex >= DAYTIME_START_HOUR && slotIndex < DAYTIME_END_HOUR;
}

function reservationCoversSlot(res: ReservationRead, dayStart: Date, slotIndex: number): boolean {
  const slotStart = new Date(dayStart.getTime() + slotIndex * SLOT_MINUTES * 60_000);
  const slotEnd = new Date(slotStart.getTime() + SLOT_MINUTES * 60_000);
  const resStart = new Date(res.start_at);
  const resEnd = new Date(res.end_at);
  // half-open overlap test
  return resStart < slotEnd && resEnd > slotStart;
}

/** Build the {gpuId, slotIndex} → DerivedCell map for one day.
 *
 * ``crossNightGroupIds`` (Phase 7 FU-23): the set of ``group_id``
 * values that span this day and an adjacent day. Cells whose covering
 * reservation belongs to such a group get ``isCrossNight: true`` so the
 * ReservationGrid can paint a unified treatment + tooltip across both
 * day renders.
 */
export function buildGrid(opts: {
  dayIso: string;
  gpuIds: number[];
  reservations: ReservationRead[];
  selecting: Set<string>;
  currentUserId: number;
  now?: Date;
  crossNightGroupIds?: Set<string>;
  /** Phase H §4 — per-gpu total memory (MB). Lets cells render
   * "剩 N GB" and lets the bottom panel cap the slider correctly. */
  gpuTotalMemoryMb?: Record<number, number>;
}): Map<string, DerivedCell> {
  const { dayIso, gpuIds, reservations, selecting, currentUserId } = opts;
  const crossNightGroupIds = opts.crossNightGroupIds ?? new Set<string>();
  const gpuTotalMemoryMb = opts.gpuTotalMemoryMb ?? {};
  const now = opts.now ?? new Date();
  const { start } = dayBounds(dayIso);
  const cells = new Map<string, DerivedCell>();

  for (const gpuId of gpuIds) {
    const gpuTotalMb = gpuTotalMemoryMb[gpuId] ?? 0;
    for (let slotIndex = 0; slotIndex < SLOTS_PER_DAY; slotIndex += 1) {
      const key = cellKey(gpuId, slotIndex);
      const slotStart = new Date(start.getTime() + slotIndex * SLOT_MINUTES * 60_000);
      const covers = reservations.filter(
        (r) => r.gpu_id === gpuId && reservationCoversSlot(r, start, slotIndex),
      );

      const sharedRows = covers.filter((r) => r.gpu_memory_mb !== null);
      const exclusiveRows = covers.filter((r) => r.gpu_memory_mb === null);
      const sharedUsedMb = sharedRows.reduce((acc, r) => acc + (r.gpu_memory_mb ?? 0), 0);
      const sharedRemainingMb = Math.max(0, gpuTotalMb - sharedUsedMb);
      const sharedHasRoom = gpuTotalMb > 0 && sharedRows.length > 0 && sharedRemainingMb > 0;

      let state: CellState;
      // A slot is reservable only while its start is in the future — once
      // ``now`` passes ``slotStart``, the backend will reject (start_at <
      // now) and the cell would just lead to "选区有冲突" on click. Grey
      // it out at the same boundary so the grid agrees with the API.
      if (slotStart <= now) {
        state = 'disabled-past';
      } else if (covers.length === 0) {
        state = selecting.has(key) ? 'selecting' : 'idle';
      } else {
        // Prefer mine, prefer active over scheduled.
        const mine = covers.filter((r) => r.user_id === currentUserId);
        const others = covers.filter((r) => r.user_id !== currentUserId);
        if (mine.length > 0) {
          state = mine.some((r) => r.status === 'active') ? 'mine-active' : 'mine-scheduled';
        } else if (selecting.has(key)) {
          state = 'selecting';
        } else if (exclusiveRows.length === 0 && sharedHasRoom) {
          // Pure-shared and has room → cell is still claimable.
          state = 'shared-remaining';
        } else {
          state = others.some((r) => r.status === 'active') ? 'others-active' : 'others-scheduled';
        }
      }

      const isCrossNight = covers.some(
        (r) => r.group_id !== null && crossNightGroupIds.has(r.group_id),
      );

      cells.set(key, {
        gpuId,
        slotIndex,
        state,
        reservations: covers,
        isCrossNight,
        sharedUsedMb,
        gpuTotalMb,
        sharedRemainingMb,
      });
    }
  }
  return cells;
}

/** Phase 7 FU-23 — return the set of group_ids whose member rows
 * touch BOTH ``dayIso`` and ``nextDayIso``. Rows without a group_id
 * never qualify (single-row reservations are by definition same-day).
 */
export function crossNightGroupIds(
  reservations: ReservationRead[],
  dayIso: string,
  nextDayIso: string,
): Set<string> {
  const { start: today_start, end: today_end } = dayBounds(dayIso);
  const { start: tomorrow_start, end: tomorrow_end } = dayBounds(nextDayIso);
  const inToday = new Set<string>();
  const inNext = new Set<string>();
  for (const r of reservations) {
    if (r.group_id === null) continue;
    const rs = new Date(r.start_at);
    const re = new Date(r.end_at);
    if (rs < today_end && re > today_start) inToday.add(r.group_id);
    if (rs < tomorrow_end && re > tomorrow_start) inNext.add(r.group_id);
  }
  const out = new Set<string>();
  for (const g of inToday) {
    if (inNext.has(g)) out.add(g);
  }
  return out;
}

export function cellKey(gpuId: number, slotIndex: number): string {
  return `${gpuId}:${slotIndex}`;
}

/** Collapse a set of selecting cell keys into contiguous (gpu, slotRange) draft items. */
export interface SelectionDraft {
  gpuId: number;
  firstSlot: number;
  lastSlot: number;
}

export function selectionDrafts(selecting: Set<string>): SelectionDraft[] {
  // Group by gpuId, sort slots, walk runs.
  const byGpu = new Map<number, number[]>();
  for (const k of selecting) {
    const [g, s] = k.split(':').map(Number);
    if (g === undefined || s === undefined) continue;
    if (!Number.isFinite(g) || !Number.isFinite(s)) continue;
    const slots = byGpu.get(g) ?? [];
    slots.push(s);
    byGpu.set(g, slots);
  }
  const drafts: SelectionDraft[] = [];
  for (const [gpuId, slots] of byGpu.entries()) {
    slots.sort((a, b) => a - b);
    const first = slots[0];
    if (first === undefined) continue;
    let runStart: number = first;
    let prev: number = first;
    for (let i = 1; i < slots.length; i += 1) {
      const s = slots[i];
      if (s === undefined) continue;
      if (s === prev + 1) {
        prev = s;
        continue;
      }
      drafts.push({ gpuId, firstSlot: runStart, lastSlot: prev });
      runStart = s;
      prev = s;
    }
    drafts.push({ gpuId, firstSlot: runStart, lastSlot: prev });
  }
  return drafts;
}

/** Map a draft to the ISO request body shape. */
export function draftToRange(
  dayIso: string,
  draft: SelectionDraft,
): { gpuId: number; startIso: string; endIso: string } {
  const { startIso } = slotToIsoRange(dayIso, draft.firstSlot);
  const { endIso } = slotToIsoRange(dayIso, draft.lastSlot);
  return { gpuId: draft.gpuId, startIso, endIso };
}

/** Format selected-total GPU-hours from the selecting set. */
export function selectionGpuHours(selecting: Set<string>): number {
  return (selecting.size * SLOT_MINUTES) / 60;
}

/** Phase H §6 — when the user has any "shared-remaining" cell in their
 * selection, the bottom panel locks into shared mode. This returns the
 * minimum per-cell remaining memory across the selection so the global
 * memory slider can cap correctly. Returns ``null`` when no shared
 * cells are selected. */
export function sharedSelectionMemoryCapMb(
  selecting: Set<string>,
  cells: Map<string, DerivedCell>,
): number | null {
  let cap: number | null = null;
  for (const key of selecting) {
    const cell = cells.get(key);
    if (cell === undefined) continue;
    if (cell.state !== 'shared-remaining') continue;
    const remaining = cell.sharedRemainingMb ?? 0;
    if (remaining <= 0) continue;
    cap = cap === null ? remaining : Math.min(cap, remaining);
  }
  return cap;
}

/** Phase H §6 — true when at least one selected cell is a shared-remaining
 * cell, forcing the bottom panel into shared mode. */
export function selectionForcesShared(
  selecting: Set<string>,
  cells: Map<string, DerivedCell>,
): boolean {
  for (const key of selecting) {
    if (cells.get(key)?.state === 'shared-remaining') return true;
  }
  return false;
}

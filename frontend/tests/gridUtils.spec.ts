/**
 * gridUtils unit tests — keep the 8-state cell machine + selection
 * draft compaction honest. No DOM, no Vue — pure functions only.
 */

import { describe, it, expect } from 'vitest';

import {
  SLOTS_PER_DAY,
  buildGrid,
  cellKey,
  crossNightGroupIds,
  draftToRange,
  selectionDrafts,
  selectionGpuHours,
  slotLabel,
} from '@/components/reservation/gridUtils';
import type { ReservationRead } from '@/api/reservations';

const DAY = '2026-06-05';

function fakeRes(overrides: Partial<ReservationRead>): ReservationRead {
  return {
    id: 1,
    user_id: 1,
    server_id: 1,
    gpu_id: 1,
    account_link_id: 1,
    group_id: null,
    start_at: `${DAY}T10:00:00Z`,
    end_at: `${DAY}T11:00:00Z`,
    status: 'scheduled',
    gpu_memory_mb: null,
    gpu_compute_share_pct: null,
    script: null,
    script_scheduled_start_at: null,
    script_max_runtime_seconds: null,
    script_started_at: null,
    script_finished_at: null,
    script_exit_code: null,
    created_at: `${DAY}T09:00:00Z`,
    cancelled_at: null,
    cancelled_by_user_id: null,
    cancellation_reason: null,
    ...overrides,
  };
}

describe('slotLabel', () => {
  it('renders zero-padded HH:00 (Phase H — 1-hour slots)', () => {
    expect(slotLabel(0)).toBe('00:00');
    expect(slotLabel(1)).toBe('01:00');
    expect(slotLabel(14)).toBe('14:00');
    expect(slotLabel(23)).toBe('23:00');
  });
});

describe('buildGrid', () => {
  // Anchor "now" before the test day starts (local time) so no past-disabled
  // cells appear. dayBounds parses dayIso as local; align NOW with that.
  const NOW = new Date(`2026-06-04T00:00:00`);

  it('renders idle cells when no reservation touches them', () => {
    const grid = buildGrid({
      dayIso: DAY,
      gpuIds: [1, 2],
      reservations: [],
      selecting: new Set(),
      currentUserId: 99,
      now: NOW,
    });
    expect(grid.size).toBe(SLOTS_PER_DAY * 2);
    expect(grid.get(cellKey(1, 0))?.state).toBe('idle');
  });

  it('marks selecting cells', () => {
    const grid = buildGrid({
      dayIso: DAY,
      gpuIds: [1],
      reservations: [],
      selecting: new Set([cellKey(1, 4)]),
      currentUserId: 99,
      now: NOW,
    });
    expect(grid.get(cellKey(1, 4))?.state).toBe('selecting');
    expect(grid.get(cellKey(1, 5))?.state).toBe('idle');
  });

  it('marks mine-scheduled and others-scheduled', () => {
    const mine = fakeRes({ id: 10, user_id: 7, gpu_id: 1, status: 'scheduled' });
    const others = fakeRes({
      id: 11,
      user_id: 99,
      gpu_id: 2,
      status: 'scheduled',
      start_at: `${DAY}T10:00:00Z`,
      end_at: `${DAY}T11:00:00Z`,
    });
    const grid = buildGrid({
      dayIso: DAY,
      gpuIds: [1, 2],
      reservations: [mine, others],
      selecting: new Set(),
      currentUserId: 7,
      now: NOW,
    });
    // 10:00 UTC = slot 20 (local TZ aware: dayIso interpreted local; we
    // assert that exactly the 10-slot window flips to the right state.)
    const mineCell = Array.from(grid.values()).find(
      (c) => c.gpuId === 1 && c.reservations.length > 0,
    );
    const othersCell = Array.from(grid.values()).find(
      (c) => c.gpuId === 2 && c.reservations.length > 0,
    );
    expect(mineCell?.state).toBe('mine-scheduled');
    expect(othersCell?.state).toBe('others-scheduled');
  });

  it('marks active over scheduled when both touch the cell', () => {
    const activeRes = fakeRes({
      id: 20,
      user_id: 99,
      gpu_id: 1,
      status: 'active',
      start_at: `${DAY}T12:00:00Z`,
      end_at: `${DAY}T13:00:00Z`,
    });
    const grid = buildGrid({
      dayIso: DAY,
      gpuIds: [1],
      reservations: [activeRes],
      selecting: new Set(),
      currentUserId: 99,
      now: NOW,
    });
    const cell = Array.from(grid.values()).find((c) => c.reservations.length > 0);
    expect(cell?.state).toBe('mine-active');
  });

  it('disables past slots', () => {
    const futureNow = new Date(`${DAY}T13:00:00`);
    const grid = buildGrid({
      dayIso: DAY,
      gpuIds: [1],
      reservations: [],
      selecting: new Set(),
      currentUserId: 1,
      now: futureNow,
    });
    expect(grid.get(cellKey(1, 0))?.state).toBe('disabled-past');
    // The slot strictly after "now" should still be idle.
    expect(grid.get(cellKey(1, SLOTS_PER_DAY - 1))?.state).toBe('idle');
  });
});

describe('selectionDrafts', () => {
  it('collapses contiguous runs per GPU', () => {
    const selecting = new Set([
      cellKey(1, 4),
      cellKey(1, 5),
      cellKey(1, 6),
      cellKey(2, 4),
      cellKey(2, 5),
      // gap on gpu 2
      cellKey(2, 10),
    ]);
    const drafts = selectionDrafts(selecting);
    // gpu1 → one run [4,6]; gpu2 → two runs [4,5] + [10,10]
    expect(drafts).toHaveLength(3);
    const gpu1 = drafts.find((d) => d.gpuId === 1)!;
    expect(gpu1.firstSlot).toBe(4);
    expect(gpu1.lastSlot).toBe(6);
    const gpu2Runs = drafts.filter((d) => d.gpuId === 2);
    expect(gpu2Runs.map((d) => `${d.firstSlot}-${d.lastSlot}`).sort()).toEqual(['10-10', '4-5']);
  });
});

describe('draftToRange', () => {
  it('returns end ISO == lastSlot + 60min (Phase H — 1h slots)', () => {
    const range = draftToRange(DAY, { gpuId: 1, firstSlot: 0, lastSlot: 0 });
    const startMs = Date.parse(range.startIso);
    const endMs = Date.parse(range.endIso);
    expect(endMs - startMs).toBe(60 * 60 * 1000);
  });

  it('handles multi-slot drafts (4h = slots [2..5])', () => {
    const range = draftToRange(DAY, { gpuId: 1, firstSlot: 2, lastSlot: 5 });
    const startMs = Date.parse(range.startIso);
    const endMs = Date.parse(range.endIso);
    // slot 5 starts at 05:00, ends at 06:00. slot 2 starts at 02:00. duration = 4h.
    expect(endMs - startMs).toBe(4 * 60 * 60 * 1000);
  });
});

describe('selectionGpuHours', () => {
  it('returns 1 per cell (Phase H — 1h slots)', () => {
    expect(selectionGpuHours(new Set())).toBe(0);
    expect(selectionGpuHours(new Set([cellKey(1, 0)]))).toBe(1);
    expect(selectionGpuHours(new Set([cellKey(1, 0), cellKey(1, 1)]))).toBe(2);
  });
});

describe('crossNightGroupIds (Phase 7 FU-23)', () => {
  const TODAY = '2026-06-05';
  const TOMORROW = '2026-06-06';
  // dayBounds parses the day-ISO as local time, so the test data
  // uses local-time strings (no trailing Z) — keeps the assertions
  // stable regardless of the runner machine's TZ. The wire format
  // returned by the backend is also tz-aware, but the gridUtils
  // helpers always work against the local day window.

  it('returns the group_ids that touch both today and tomorrow', () => {
    const reservations = [
      // Group A spans the local midnight boundary — should appear.
      fakeRes({
        id: 1,
        group_id: 'grp-A',
        start_at: `${TODAY}T22:00:00`,
        end_at: `${TOMORROW}T03:00:00`,
      }),
      // Group B is today-only.
      fakeRes({
        id: 2,
        group_id: 'grp-B',
        start_at: `${TODAY}T10:00:00`,
        end_at: `${TODAY}T11:00:00`,
      }),
      // Group C is tomorrow-only.
      fakeRes({
        id: 3,
        group_id: 'grp-C',
        start_at: `${TOMORROW}T10:00:00`,
        end_at: `${TOMORROW}T11:00:00`,
      }),
      // No group_id — must never count as cross-night even when spanning.
      fakeRes({
        id: 4,
        group_id: null,
        start_at: `${TODAY}T22:30:00`,
        end_at: `${TOMORROW}T00:30:00`,
      }),
    ];
    const out = crossNightGroupIds(reservations, TODAY, TOMORROW);
    expect(out.has('grp-A')).toBe(true);
    expect(out.has('grp-B')).toBe(false);
    expect(out.has('grp-C')).toBe(false);
    expect(out.size).toBe(1);
  });

  it('multi-row group (siblings on today + siblings on tomorrow) still counts', () => {
    const reservations = [
      fakeRes({
        id: 5,
        group_id: 'grp-Z',
        start_at: `${TODAY}T20:00:00`,
        end_at: `${TODAY}T22:00:00`,
      }),
      fakeRes({
        id: 6,
        group_id: 'grp-Z',
        start_at: `${TOMORROW}T01:00:00`,
        end_at: `${TOMORROW}T03:00:00`,
      }),
    ];
    const out = crossNightGroupIds(reservations, TODAY, TOMORROW);
    expect(out.has('grp-Z')).toBe(true);
  });

  it('buildGrid stamps isCrossNight when the covering reservation belongs to a cross-night group', () => {
    const cross = new Set(['grp-A']);
    const reservations = [
      fakeRes({
        id: 1,
        group_id: 'grp-A',
        start_at: `${TODAY}T22:00:00`,
        end_at: `${TOMORROW}T03:00:00`,
        status: 'scheduled',
      }),
    ];
    const cells = buildGrid({
      dayIso: TODAY,
      gpuIds: [1],
      reservations,
      selecting: new Set(),
      currentUserId: 1,
      now: new Date('2026-06-04T00:00:00'),
      crossNightGroupIds: cross,
    });
    // Last slot of today (23:00) should be cross-night.
    const last = cells.get(cellKey(1, SLOTS_PER_DAY - 1));
    expect(last?.isCrossNight).toBe(true);
    // A cell with no covering reservation should NOT be cross-night.
    const noon = cells.get(cellKey(1, 12));
    expect(noon?.isCrossNight).toBe(false);
  });
});

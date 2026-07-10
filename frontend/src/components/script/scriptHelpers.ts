/**
 * Pure helpers shared by Script* components.
 *
 * Derives the UI-facing script status (which adds a "scheduled" view-only
 * state that doesn't exist on the backend) and formats the various
 * duration / size / SSH-command strings.
 *
 * Backend script_status is null OR one of running/completed/failed/killed
 * (see `ck_res_script_status` in models/reservation.py). The UI needs one
 * extra view state — "script attached but agent hasn't fired yet" — so the
 * derivation maps (script != null && script_status === null) → 'scheduled'.
 */

import type { ReservationRead } from '@/api/reservations';

export type ScriptUIStatus =
  | 'none' // reservation has no script attached
  | 'scheduled' // script attached, agent hasn't dispatched yet
  | 'running'
  | 'completed'
  | 'failed'
  | 'killed';

export function deriveScriptUIStatus(r: ReservationRead): ScriptUIStatus {
  if (r.script === null) return 'none';
  if (r.script_status === 'running') return 'running';
  if (r.script_status === 'completed') return 'completed';
  if (r.script_status === 'failed') return 'failed';
  if (r.script_status === 'killed') return 'killed';
  return 'scheduled';
}

/** "2h 17min" / "8min 32s" / "45s". Returns "—" for non-positive input. */
export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return '—';
  const s = Math.floor(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return `${h}h ${m}min`;
  if (m > 0) return `${m}min ${sec}s`;
  return `${sec}s`;
}

/** Computes elapsed seconds between two ISO timestamps; ``end`` defaults
 * to "now" so running scripts can be displayed live. */
export function elapsedSeconds(startedAtIso: string, endIso: string | null = null): number {
  const start = Date.parse(startedAtIso);
  if (!Number.isFinite(start)) return 0;
  const end = endIso === null ? Date.now() : Date.parse(endIso);
  if (!Number.isFinite(end)) return 0;
  return Math.max(0, (end - start) / 1000);
}

/** "4.2 KB" / "1.3 MB" / "732 B". */
export function formatBytes(bytes: number | null): string {
  if (bytes === null || !Number.isFinite(bytes) || bytes < 0) return '—';
  if (bytes < 1024) return `${bytes} B`;
  const kb = bytes / 1024;
  if (kb < 1024) return `${kb.toFixed(1)} KB`;
  return `${(kb / 1024).toFixed(1)} MB`;
}

/** Format an ISO timestamp as a short local "06/08 14:00" string. */
export function formatLocalDateTimeShort(iso: string | null): string {
  if (iso === null) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  const mm = (d.getMonth() + 1).toString().padStart(2, '0');
  const dd = d.getDate().toString().padStart(2, '0');
  const hh = d.getHours().toString().padStart(2, '0');
  const min = d.getMinutes().toString().padStart(2, '0');
  return `${mm}/${dd} ${hh}:${min}`;
}

/** Builds the SSH command the user can paste into their terminal to tail
 * the complete log on the agent host. The platform keeps only a bounded
 * recent-output tail for quick inspection. */
export function buildSshTailCommand(
  linuxUsername: string,
  hostname: string,
  logPath: string | null,
): string {
  if (logPath === null) return `ssh ${linuxUsername}@${hostname}`;
  return `ssh ${linuxUsername}@${hostname} 'tail -f ${logPath}'`;
}

/** Same as buildSshTailCommand but cats the whole file once (useful for
 * finished scripts where ``tail -f`` would block forever). */
export function buildSshCatCommand(
  linuxUsername: string,
  hostname: string,
  logPath: string | null,
): string {
  if (logPath === null) return `ssh ${linuxUsername}@${hostname}`;
  return `ssh ${linuxUsername}@${hostname} 'cat ${logPath}'`;
}

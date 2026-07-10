/**
 * Shared display-time helpers (v2 polish).
 *
 * Raw API timestamps (ISO strings, sometimes with microseconds) must never
 * reach the screen. Use ``timeAgo`` for "how fresh" readings and
 * ``formatDateTime`` when the exact wall-clock moment matters.
 */

/** Parse an ISO-ish timestamp; returns null when invalid/absent. */
function parseTs(value: string | null | undefined): Date | null {
  if (!value) return null;
  // Match the rest of the app: bare timestamps parse as local time.
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** "刚刚 / N 分钟前 / N 小时前 / N 天前 / 2026-06-01"。 */
export function timeAgo(value: string | null | undefined): string {
  const d = parseTs(value);
  if (d === null) return '—';
  const s = Math.max(0, (Date.now() - d.getTime()) / 1000);
  if (s < 60) return '刚刚';
  if (s < 3600) return `${Math.floor(s / 60)} 分钟前`;
  if (s < 86400) return `${Math.floor(s / 3600)} 小时前`;
  if (s < 86400 * 30) return `${Math.floor(s / 86400)} 天前`;
  return formatDate(value);
}

/** "2026-06-01" (local time). */
export function formatDate(value: string | null | undefined): string {
  const d = parseTs(value);
  if (d === null) return '—';
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

/** "2026-06-01 16:01" (local time, no seconds, no microseconds). */
export function formatDateTime(value: string | null | undefined): string {
  const d = parseTs(value);
  if (d === null) return '—';
  const p = (n: number) => String(n).padStart(2, '0');
  return `${formatDate(value)} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

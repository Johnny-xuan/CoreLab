/**
 * Pull a human-readable error string out of an axios error.
 *
 * Backend errors follow two shapes:
 *   1. `detail: string` — render that directly
 *   2. `detail: { code: string, message?: string }` — render `"CODE: message"`
 *
 * Anything else (network error, non-axios throw) falls back to the
 * caller-supplied label.
 *
 * Replaces ~15 copy-paste functions previously scattered through views/;
 * the wide-net superset shape is used so every call site gets the
 * code+message rendering "for free" without behaviour regressions.
 */
import { AxiosError } from 'axios';

export function extractDetail(err: unknown, fallback: string): string {
  if (err instanceof AxiosError) {
    const detail = err.response?.data?.detail;
    if (typeof detail === 'string') return detail;
    if (detail !== null && typeof detail === 'object') {
      const obj = detail as { code?: string; message?: string };
      if (obj.code !== undefined) return `${obj.code}${obj.message ? ': ' + obj.message : ''}`;
    }
  }
  return fallback;
}

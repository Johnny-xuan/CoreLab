/**
 * Capability admin form validation (Phase 9 P9-12).
 *
 * Dangerous capabilities — currently ``useradd`` / ``userdel`` /
 * ``gpu.kill_process`` per docs/04 §6 — require a justification note
 * of at least 10 characters before they can be enabled. The backend
 * enforces the same rule (422 ``CAPABILITY_NOTES_TOO_SHORT``); we
 * pre-validate client-side so the user gets immediate feedback.
 */

export const MIN_DANGEROUS_NOTES_CHARS = 10;

export interface DangerousNotesResult {
  ok: boolean;
  reason: 'ok' | 'notes_too_short' | 'not_required';
}

export function validateDangerousNotes(
  notes: string,
  isDangerous: boolean,
  enabling: boolean,
): DangerousNotesResult {
  if (!enabling || !isDangerous) return { ok: true, reason: 'not_required' };
  const trimmed = (notes ?? '').trim();
  if (trimmed.length < MIN_DANGEROUS_NOTES_CHARS) {
    return { ok: false, reason: 'notes_too_short' };
  }
  return { ok: true, reason: 'ok' };
}

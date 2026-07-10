/**
 * validateDangerousNotes — Phase 9 P9-12 helper.
 *
 * The Server Detail capabilities tab pre-validates dangerous-capability
 * notes before issuing the PUT. Backend enforces the same 10-char
 * floor, so this is purely a UX shortcut.
 */

import { describe, it, expect } from 'vitest';

import { MIN_DANGEROUS_NOTES_CHARS, validateDangerousNotes } from '@/utils/capabilityValidation';

describe('validateDangerousNotes', () => {
  it('passes when not enabling (toggling off)', () => {
    const r = validateDangerousNotes('', true, false);
    expect(r.ok).toBe(true);
    expect(r.reason).toBe('not_required');
  });

  it('passes for a non-dangerous capability regardless of notes', () => {
    const r = validateDangerousNotes('', false, true);
    expect(r.ok).toBe(true);
    expect(r.reason).toBe('not_required');
  });

  it('rejects when enabling a dangerous capability with empty notes', () => {
    const r = validateDangerousNotes('', true, true);
    expect(r.ok).toBe(false);
    expect(r.reason).toBe('notes_too_short');
  });

  it('rejects when enabling a dangerous capability with <10 char notes', () => {
    const r = validateDangerousNotes('short', true, true);
    expect(r.ok).toBe(false);
  });

  it('rejects when notes trim() < 10 even if raw length is >=10', () => {
    const r = validateDangerousNotes('   short   ', true, true);
    expect(r.ok).toBe(false);
  });

  it('accepts when enabling a dangerous capability with >=10 char notes', () => {
    const r = validateDangerousNotes('approved by lab manager', true, true);
    expect(r.ok).toBe(true);
    expect(r.reason).toBe('ok');
  });

  it('exports the canonical 10-char floor', () => {
    expect(MIN_DANGEROUS_NOTES_CHARS).toBe(10);
  });
});

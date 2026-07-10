/**
 * PoliciesTab smoke test — mount + per-key schema-aware threshold +
 * cap×policy warning banner (P9-10/11).
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import { NConfigProvider, NMessageProvider } from 'naive-ui';
import { h } from 'vue';

const listMock = vi.fn();
const switchMock = vi.fn();
const updateMock = vi.fn();

vi.mock('@/api/policies', async () => {
  const actual = await vi.importActual<typeof import('@/api/policies')>('@/api/policies');
  return {
    ...actual,
    listPolicy: (...args: unknown[]) => listMock(...args),
    switchProfile: (...args: unknown[]) => switchMock(...args),
    updatePolicy: (...args: unknown[]) => updateMock(...args),
  };
});

import PoliciesTab from '@/components/policies/PoliciesTab.vue';
import { POLICY_KEYS } from '@/api/policies';
import type { CapabilityRead } from '@/api/servers';

function seedRows() {
  return POLICY_KEYS.map((k, i) => ({
    id: i + 1,
    policy_key: k,
    enabled: true,
    severity: 'notify' as const,
    threshold_value:
      k === 'memory_overuse'
        ? { value: 20, unit: 'pct' }
        : k === 'gpu_hang'
          ? { util_zero_seconds: 600, mem_floor_mb: 1024 }
          : k === 'gpu_temp_high'
            ? { value: 85, unit: 'celsius' }
            : null,
    grace_period_seconds: 300,
    notify_admin: false,
    notes: null,
    updated_at: '2026-06-05T10:00:00',
    updated_by_user_id: 1,
  }));
}

function mountTab(caps: CapabilityRead[] = []) {
  setActivePinia(createPinia());
  return mount({
    components: { NConfigProvider, NMessageProvider, PoliciesTab },
    render() {
      return h(NConfigProvider, null, {
        default: () =>
          h(NMessageProvider, null, {
            default: () => h(PoliciesTab, { serverId: 1, capabilities: caps, canEdit: true }),
          }),
      });
    },
  });
}

describe('PoliciesTab', () => {
  beforeEach(() => {
    listMock.mockReset();
    switchMock.mockReset();
    updateMock.mockReset();
  });

  it('renders all 8 policy_key rows after load', async () => {
    listMock.mockResolvedValueOnce(seedRows());
    const wrapper = mountTab();
    await flushPromises();
    for (const k of POLICY_KEYS) {
      expect(wrapper.text()).toContain(k);
    }
  });

  it('shows the threshold field labels for the 3 keys that have one', async () => {
    listMock.mockResolvedValueOnce(seedRows());
    const wrapper = mountTab();
    await flushPromises();
    expect(wrapper.text()).toContain('阈值 %(0-100)');
    expect(wrapper.text()).toContain('阈值 °C(0-200)');
    expect(wrapper.text()).toContain('util_zero_seconds');
    expect(wrapper.text()).toContain('mem_floor_mb');
    // The 5 no-threshold keys should show the muted label at least once.
    expect(wrapper.text()).toContain('无阈值');
  });

  it('renders the cap-off warning banner when gpu.kill_process is disabled', async () => {
    listMock.mockResolvedValueOnce(seedRows());
    const caps: CapabilityRead[] = [
      {
        id: 1,
        server_id: 1,
        capability_key: 'gpu.kill_process',
        is_enabled: false,
        is_dangerous: true,
        notes: null,
        updated_at: '2026-06-05T10:00:00',
        updated_by_user_id: 1,
      },
    ];
    const wrapper = mountTab(caps);
    await flushPromises();
    expect(wrapper.text()).toContain('gpu.kill_process');
    expect(wrapper.text()).toContain('自动降级');
  });
});

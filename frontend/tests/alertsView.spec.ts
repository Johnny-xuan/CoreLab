/**
 * AlertsView smoke test — mount + empty/non-empty rendering.
 *
 * `listAlerts` is mocked. Resolve flow is covered indirectly by the
 * backend integration tests (P8-11/P8-14 + P9 ingest).
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import { NConfigProvider, NDialogProvider, NMessageProvider } from 'naive-ui';
import { h } from 'vue';
import { createRouter, createMemoryHistory } from 'vue-router';

const listMock = vi.fn(async () => [] as unknown[]);

vi.mock('@/api/alerts', () => ({
  listAlerts: (...args: unknown[]) => listMock(...args),
  resolveAlert: vi.fn(),
}));

import AlertsView from '@/views/AlertsView.vue';

async function mountView() {
  setActivePinia(createPinia());
  const stub = { template: '<div/>' };
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/lab/alerts', name: 'lab-alerts', component: AlertsView },
      { path: '/login', name: 'login', component: stub },
      { path: '/me/dashboard', name: 'dashboard', component: stub },
      { path: '/servers', name: 'servers', component: stub },
      { path: '/me/profile', name: 'my-profile', component: stub },
      { path: '/me/all-reservations', name: 'all-reservations', component: stub },
      { path: '/me/usage', name: 'my-usage', component: stub },
      { path: '/account-link-requests', name: 'account-link-requests', component: stub },
      { path: '/me/accounts/claim', name: 'claim-account', component: stub },
      { path: '/lab/audit', name: 'lab-audit', component: stub },
    ],
  });
  await router.push('/lab/alerts');
  await router.isReady();
  return mount(
    {
      components: { NConfigProvider, NMessageProvider, NDialogProvider, AlertsView },
      render() {
        return h(NConfigProvider, null, {
          default: () =>
            h(NMessageProvider, null, {
              default: () => h(NDialogProvider, null, { default: () => h(AlertsView) }),
            }),
        });
      },
    },
    { global: { plugins: [router] } },
  );
}

describe('AlertsView', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listMock.mockReset();
  });

  it('renders header + filters and the empty state when no alerts', async () => {
    listMock.mockResolvedValueOnce([]);
    const wrapper = await mountView();
    await flushPromises();
    expect(wrapper.text()).toContain('告警');
    expect(wrapper.text()).toContain('起始时间(ISO8601)');
    expect(wrapper.text()).toContain('服务器 id');
    expect(wrapper.text()).toContain('数量上限');
    expect(wrapper.text()).toContain('暂无告警');
  });

  it('renders a row when listAlerts returns one', async () => {
    listMock.mockResolvedValueOnce([
      {
        id: 11,
        server_id: 2,
        gpu_id: 0,
        reservation_id: null,
        event_type: 'compliance.preempt_others_reservation',
        severity: 'warn',
        payload: { policy_key: 'preempt_others_reservation' },
        notified_user_ids: [3, 7],
        is_resolved: false,
        resolved_at: null,
        resolved_by_user_id: null,
        resolution_note: null,
        created_at: '2026-06-05T10:00:00',
      },
    ]);
    const wrapper = await mountView();
    await flushPromises();
    expect(wrapper.text()).toContain('compliance.preempt_others_reservation');
    expect(wrapper.text()).toContain('warn');
    expect(wrapper.text()).toContain('解决');
  });
});

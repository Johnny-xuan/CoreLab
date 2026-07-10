/**
 * AuditLogsView smoke test — mount + filter rendering + initial reload.
 *
 * The view's `onMounted` calls `listAuditLogs` so we mock that module
 * to return a deterministic page. Deeper filter / pagination behaviour
 * is covered by Phase 9 backend integration tests against the real
 * /audit-logs endpoint.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import { NConfigProvider, NMessageProvider } from 'naive-ui';
import { h } from 'vue';
import { createRouter, createMemoryHistory } from 'vue-router';

vi.mock('@/api/auditLogs', () => ({
  listAuditLogs: vi.fn(async () => ({
    items: [
      {
        id: 1,
        actor: { id: 5, username: 'alice' },
        action: 'reservation.create',
        target_type: 'reservation',
        target_id: 100,
        target_server_id: 1,
        payload: { server_id: 1 },
        ip_address: '127.0.0.1',
        result: 'ok',
        created_at: '2026-06-05T10:00:00',
      },
    ],
    page: 1,
    size: 20,
    total: 1,
    total_pages: 1,
  })),
  getAuditLog: vi.fn(),
}));

import AuditLogsView from '@/views/AuditLogsView.vue';

async function mountView() {
  setActivePinia(createPinia());
  const stub = { template: '<div/>' };
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/lab/audit', name: 'lab-audit', component: AuditLogsView },
      { path: '/login', name: 'login', component: stub },
      { path: '/me/dashboard', name: 'dashboard', component: stub },
      { path: '/servers', name: 'servers', component: stub },
      { path: '/me/profile', name: 'my-profile', component: stub },
      { path: '/me/all-reservations', name: 'all-reservations', component: stub },
      { path: '/me/usage', name: 'my-usage', component: stub },
      { path: '/account-link-requests', name: 'account-link-requests', component: stub },
      { path: '/me/accounts/claim', name: 'claim-account', component: stub },
      { path: '/lab/alerts', name: 'lab-alerts', component: stub },
    ],
  });
  await router.push('/lab/audit');
  await router.isReady();
  return mount(
    {
      components: { NConfigProvider, NMessageProvider, AuditLogsView },
      render() {
        return h(NConfigProvider, null, {
          default: () => h(NMessageProvider, null, { default: () => h(AuditLogsView) }),
        });
      },
    },
    { global: { plugins: [router] } },
  );
}

describe('AuditLogsView', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders the page title + 6 filter group labels', async () => {
    const wrapper = await mountView();
    await flushPromises();
    expect(wrapper.text()).toContain('审计日志');
    for (const label of [
      '操作者用户 id',
      '操作',
      '目标类型',
      '目标服务器 id',
      '起始时间(ISO8601)',
      '结束时间(ISO8601)',
    ]) {
      expect(wrapper.text()).toContain(label);
    }
  });

  it('shows the row fetched from listAuditLogs on mount', async () => {
    const wrapper = await mountView();
    await flushPromises();
    expect(wrapper.text()).toContain('reservation.create');
    expect(wrapper.text()).toContain('alice');
    expect(wrapper.text()).toContain('共 1 条');
  });
});

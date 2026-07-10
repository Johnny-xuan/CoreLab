/**
 * MyScriptsView smoke test — keeps the user-facing script-log contract
 * aligned with the platform log-tail feature.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import { NConfigProvider, NDialogProvider, NMessageProvider } from 'naive-ui';
import { h } from 'vue';
import { createRouter, createMemoryHistory } from 'vue-router';

const apiMocks = vi.hoisted(() => ({
  listReservationsForPa: vi.fn(async () => []),
  modifyReservation: vi.fn(),
  cancelReservation: vi.fn(),
  getServer: vi.fn(async () => ({
    id: 80,
    lab_id: 1,
    hostname: 'gpu-a',
    display_name: null,
    ip_address: null,
    os_info: null,
    kernel_version: null,
    cpu_model: null,
    cpu_cores: null,
    memory_total_mb: null,
    agent_version: null,
    status: 'online',
    last_heartbeat_at: null,
    max_reservation_hours: null,
    is_active: true,
    created_at: '2026-06-01T00:00:00',
    approved_at: null,
    approved_by_user_id: null,
  })),
}));

vi.mock('@/api/reservations', () => ({
  listReservationsForPa: apiMocks.listReservationsForPa,
  modifyReservation: apiMocks.modifyReservation,
  cancelReservation: apiMocks.cancelReservation,
}));

vi.mock('@/api/servers', () => ({
  getServer: apiMocks.getServer,
}));

import MyScriptsView from '@/views/MyScripts.vue';
import { useWorkspaceStore } from '@/stores/workspace';

async function mountView() {
  setActivePinia(createPinia());
  const workspace = useWorkspaceStore();
  workspace.links = [
    {
      id: 10,
      user_id: 2,
      physical_account_id: 200,
      source: 'admin_declared',
      proof_evidence: {},
      established_at: '2026-06-01T00:00:00',
      is_active: true,
      revoked_at: null,
      revoke_reason: null,
    },
  ];
  workspace.pas = new Map([
    [
      200,
      {
        id: 200,
        server_id: 80,
        linux_username: 'alice',
        uid: null,
        gid: null,
        home_directory: null,
        default_shell: null,
        source: 'agent_created',
        is_active: true,
        created_at: '2026-06-01T00:00:00',
        created_by_user_id: null,
        notes: null,
        last_seen_at: null,
      },
    ],
  ]);

  const stub = { template: '<div />' };
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/me/accounts/:pa_id/scripts', name: 'pa-scripts', component: MyScriptsView },
      { path: '/me/accounts/:pa_id/reserve', name: 'pa-reserve', component: stub },
    ],
  });
  await router.push('/me/accounts/200/scripts');
  await router.isReady();

  return mount(
    {
      components: { NConfigProvider, NMessageProvider, NDialogProvider, MyScriptsView },
      render() {
        return h(NConfigProvider, null, {
          default: () =>
            h(NMessageProvider, null, {
              default: () => h(NDialogProvider, null, { default: () => h(MyScriptsView) }),
            }),
        });
      },
    },
    {
      global: {
        plugins: [router],
        stubs: {
          AppLayout: { template: '<main><slot /></main>' },
          ScriptDetailsCard: { template: '<section class="script-details-stub" />' },
          ScriptEditor: { template: '<div />' },
        },
      },
    },
  );
}

describe('MyScriptsView', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMocks.listReservationsForPa.mockResolvedValue([]);
  });

  it('describes platform log-tail viewing instead of SSH-only log access', async () => {
    const wrapper = await mountView();
    await flushPromises();

    const text = wrapper.text();
    expect(text).toContain('平台可查看最近输出');
    expect(text).toContain('完整日志保留在 agent 主机');
    expect(text).not.toContain('日志在 agent 主机上,用 SSH 查看');
  });
});

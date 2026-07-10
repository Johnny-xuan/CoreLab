/**
 * LinkRequestsTab smoke test — mount + scope-by-server filtering
 * (Phase 9 P9-14).
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import { NConfigProvider, NMessageProvider } from 'naive-ui';
import { h } from 'vue';

const listPendingMock = vi.fn();
const approveMock = vi.fn();
const denyMock = vi.fn();
const listPasMock = vi.fn();

vi.mock('@/api/accountLinkRequests', () => ({
  listPending: (...args: unknown[]) => listPendingMock(...args),
  approve: (...args: unknown[]) => approveMock(...args),
  deny: (...args: unknown[]) => denyMock(...args),
}));

vi.mock('@/api/physicalAccounts', () => ({
  listPas: (...args: unknown[]) => listPasMock(...args),
}));

import LinkRequestsTab from '@/components/serverDetail/LinkRequestsTab.vue';

function mountTab(canEdit = true) {
  setActivePinia(createPinia());
  return mount({
    components: { NConfigProvider, NMessageProvider, LinkRequestsTab },
    render() {
      return h(NConfigProvider, null, {
        default: () =>
          h(NMessageProvider, null, {
            default: () => h(LinkRequestsTab, { serverId: 7, canEdit }),
          }),
      });
    },
  });
}

describe('LinkRequestsTab', () => {
  beforeEach(() => {
    listPendingMock.mockReset();
    listPasMock.mockReset();
    approveMock.mockReset();
    denyMock.mockReset();
  });

  it('renders the empty state when no pending row matches this server', async () => {
    listPendingMock.mockResolvedValueOnce([
      {
        id: 1,
        requester_user_id: 9,
        physical_account_id: 99,
        status: 'pending',
        request_note: null,
        decided_by_user_id: null,
        decided_at: null,
        decision_note: null,
        created_at: '2026-06-05',
        updated_at: '2026-06-05',
      },
    ]);
    // The pending row points at pa #99, but this server only owns #11.
    listPasMock.mockResolvedValueOnce([
      { id: 11, server_id: 7, linux_username: 'alice', source: 'admin_manual_register' },
    ]);
    const wrapper = mountTab();
    await flushPromises();
    expect(wrapper.text()).toContain('本 server 没有 pending');
  });

  it('shows only the pending rows for PAs that belong to this server', async () => {
    listPendingMock.mockResolvedValueOnce([
      {
        id: 1,
        requester_user_id: 9,
        physical_account_id: 11,
        status: 'pending',
        request_note: 'please',
        decided_by_user_id: null,
        decided_at: null,
        decision_note: null,
        created_at: '2026-06-05',
        updated_at: '2026-06-05',
      },
      {
        id: 2,
        requester_user_id: 10,
        physical_account_id: 99, // belongs to a different server
        status: 'pending',
        request_note: null,
        decided_by_user_id: null,
        decided_at: null,
        decision_note: null,
        created_at: '2026-06-05',
        updated_at: '2026-06-05',
      },
    ]);
    listPasMock.mockResolvedValueOnce([
      { id: 11, server_id: 7, linux_username: 'alice', source: 'admin_manual_register' },
    ]);
    const wrapper = mountTab();
    await flushPromises();
    expect(wrapper.text()).toContain('alice');
    expect(wrapper.text()).toContain('please');
    expect(wrapper.text()).not.toContain('user #10');
  });

  it('hides approve/deny buttons when canEdit=false', async () => {
    listPendingMock.mockResolvedValueOnce([
      {
        id: 1,
        requester_user_id: 9,
        physical_account_id: 11,
        status: 'pending',
        request_note: null,
        decided_by_user_id: null,
        decided_at: null,
        decision_note: null,
        created_at: '2026-06-05',
        updated_at: '2026-06-05',
      },
    ]);
    listPasMock.mockResolvedValueOnce([
      { id: 11, server_id: 7, linux_username: 'alice', source: 'admin_manual_register' },
    ]);
    const wrapper = mountTab(false);
    await flushPromises();
    expect(wrapper.text()).toContain('alice');
    expect(wrapper.text()).not.toContain('通过');
    expect(wrapper.text()).not.toContain('拒绝');
  });
});

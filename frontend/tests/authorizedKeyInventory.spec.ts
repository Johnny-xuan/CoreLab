import { describe, it, expect } from 'vitest';
import { mount } from '@vue/test-utils';
import { NConfigProvider } from 'naive-ui';
import { h } from 'vue';

import AuthorizedKeyInventory from '@/components/serverDetail/AuthorizedKeyInventory.vue';
import type {
  AuthorizedKeyInventoryEntry,
  AuthorizedKeyReadbackResponse,
} from '@/api/physicalAccounts';

const baseEntry: AuthorizedKeyInventoryEntry = {
  entry_id: 101,
  physical_account_id: 22,
  linux_username: 'ivy_lab',
  ssh_public_key_id: 33,
  fingerprint_sha256: 'SHA256:inventoryfailed012345678901234567890123',
  key_type: 'ssh-ed25519',
  key_comment: 'workstation',
  key_is_active: true,
  pushed_for_user_id: 44,
  pushed_for_username: 'ivy',
  pushed_for_display_name: 'Ivy',
  pushed_by_user_id: 1,
  pushed_by_username: 'adm',
  pushed_by_display_name: 'Admin',
  pushed_at: '2026-07-04T12:00:00Z',
  is_active: false,
  removed_at: null,
  removed_by_user_id: null,
  removed_by_username: null,
  removed_by_display_name: null,
  status: 'push_failed',
  can_retry: true,
};

function mountInventory(
  entries: AuthorizedKeyInventoryEntry[],
  readbacks?: Record<number, AuthorizedKeyReadbackResponse>,
) {
  return mount({
    components: { NConfigProvider, AuthorizedKeyInventory },
    render() {
      return h(NConfigProvider, null, {
        default: () => h(AuthorizedKeyInventory, { entries, readbacks }),
      });
    },
  });
}

describe('AuthorizedKeyInventory', () => {
  it('renders an empty state for servers without CoreLab-managed keys', () => {
    const wrapper = mountInventory([]);
    expect(wrapper.text()).toContain('还没有 CoreLab 管理的 key');
  });

  it('shows retry action for push-failed entries', async () => {
    const wrapper = mountInventory([baseEntry]);

    expect(wrapper.text()).toContain('ivy_lab');
    expect(wrapper.text()).toContain('待重推');
    expect(wrapper.text()).toContain('workstation');

    const retryButton = wrapper.findAll('button').find((button) => button.text().includes('重推'));
    expect(retryButton).toBeTruthy();
    await retryButton!.trigger('click');
    const component = wrapper.findComponent(AuthorizedKeyInventory);
    expect(component.emitted('retry')?.[0]?.[0]).toMatchObject({
      entry_id: 101,
      physical_account_id: 22,
    });
  });

  it('does not offer retry for removed entries', () => {
    const wrapper = mountInventory([
      {
        ...baseEntry,
        status: 'removed',
        can_retry: false,
        removed_at: '2026-07-04T13:00:00Z',
      },
    ]);

    expect(wrapper.text()).toContain('已撤销');
    expect(wrapper.findAll('button').some((button) => button.text().includes('重推'))).toBe(false);
  });

  it('emits readback and renders live host comparison', async () => {
    const activeEntry: AuthorizedKeyInventoryEntry = {
      ...baseEntry,
      is_active: true,
      status: 'active',
      can_retry: false,
    };
    const wrapper = mountInventory([activeEntry], {
      22: {
        physical_account_id: 22,
        server_id: 9,
        linux_username: 'ivy_lab',
        ok: true,
        error: null,
        authorized_keys_path: '/home/ivy_lab/.ssh/authorized_keys',
        line_count: 2,
        invalid_line_count: 0,
        host_keys: [
          {
            line_number: 1,
            fingerprint_sha256: activeEntry.fingerprint_sha256,
            key_type: 'ssh-ed25519',
            comment: 'corelab:user=44',
          },
        ],
        managed_entries: [
          {
            entry_id: activeEntry.entry_id,
            ssh_public_key_id: activeEntry.ssh_public_key_id,
            fingerprint_sha256: activeEntry.fingerprint_sha256,
            key_type: activeEntry.key_type,
            key_comment: activeEntry.key_comment,
            pushed_for_user_id: activeEntry.pushed_for_user_id,
            pushed_for_username: activeEntry.pushed_for_username,
            pushed_for_display_name: activeEntry.pushed_for_display_name,
            pushed_at: activeEntry.pushed_at,
            present_on_host: true,
          },
        ],
        unknown_host_keys: [
          {
            line_number: 2,
            fingerprint_sha256: 'SHA256:unknownhostkey012345678901234567890123',
            key_type: 'ssh-ed25519',
            comment: 'manual laptop',
          },
        ],
        mock_warning: null,
      },
    });

    expect(wrapper.text()).toContain('host 存在');
    expect(wrapper.text()).toContain('未知 1');
    expect(wrapper.text()).toContain('SHA256:unknownhostkey');

    const readButton = wrapper
      .findAll('button')
      .find((button) => button.text().includes('读取 host'));
    expect(readButton).toBeTruthy();
    await readButton!.trigger('click');
    const component = wrapper.findComponent(AuthorizedKeyInventory);
    expect(component.emitted('readback')?.[0]?.[0]).toMatchObject({
      entry_id: 101,
      physical_account_id: 22,
    });
  });
});

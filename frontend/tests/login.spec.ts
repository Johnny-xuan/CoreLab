/**
 * Login view smoke test.
 *
 * Phase 2: the form now drives a real auth store + router, so the test
 * wires both stubs around the component. We keep the assertions to
 * "the form renders + the submit button is present" — full success /
 * 401 / 403 paths are covered by the Phase 2 backend integration tests
 * via the actual /api/v1/auth/login endpoint.
 */

import { beforeEach, describe, it, expect, vi } from 'vitest';
import { flushPromises, mount } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import { NConfigProvider, NMessageProvider } from 'naive-ui';
import { h } from 'vue';
import { createRouter, createMemoryHistory } from 'vue-router';

import Login from '@/views/Login.vue';

const authApi = vi.hoisted(() => ({
  login: vi.fn(),
  logout: vi.fn(),
  fetchMe: vi.fn(),
}));

const usersApi = vi.hoisted(() => ({
  fetchMyGrants: vi.fn(),
}));

vi.mock('@/api/auth', () => authApi);
vi.mock('@/api/users', () => usersApi);

function mountLogin() {
  setActivePinia(createPinia());
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/login', name: 'login', component: Login },
      { path: '/me/dashboard', name: 'dashboard', component: { template: '<div/>' } },
    ],
  });
  return mount(
    {
      components: { NConfigProvider, NMessageProvider, Login },
      render() {
        return h(NConfigProvider, null, {
          default: () => h(NMessageProvider, null, { default: () => h(Login) }),
        });
      },
    },
    { global: { plugins: [router] } },
  );
}

describe('Login view', () => {
  beforeEach(() => {
    authApi.login.mockReset();
    authApi.logout.mockReset();
    authApi.fetchMe.mockReset();
    usersApi.fetchMyGrants.mockReset();
    usersApi.fetchMyGrants.mockResolvedValue([]);
  });

  it('renders the brand + form fields', () => {
    const wrapper = mountLogin();
    expect(wrapper.text()).toContain('CoreLab');
    expect(wrapper.text()).toContain('用户名');
    expect(wrapper.text()).toContain('密码');
    const inputs = wrapper.findAll('input');
    expect(inputs.length).toBeGreaterThanOrEqual(2);
  });

  it('mounts and finds the submit button', () => {
    const wrapper = mountLogin();
    const button = wrapper.find('button');
    expect(button.exists()).toBe(true);
    expect(button.text()).toContain('登录');
  });

  it('submits credentials once for one button activation', async () => {
    authApi.login.mockReturnValue(new Promise(() => {}));

    const wrapper = mountLogin();
    const inputs = wrapper.findAll('input');
    await inputs[0]!.setValue('test-admin');
    await inputs[1]!.setValue('not-a-real-password');

    await wrapper.find('button').trigger('click');
    await wrapper.find('form').trigger('submit');
    await flushPromises();

    expect(authApi.login).toHaveBeenCalledTimes(1);
  });
});

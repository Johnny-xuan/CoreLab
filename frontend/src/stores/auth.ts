/**
 * Auth store — persists access token + user snapshot in localStorage.
 *
 * Token is treated opaquely; the store does not introspect JWT claims.
 * The router and axios interceptor consult ``isAuthenticated`` and
 * ``token`` only; they never decode the JWT themselves.
 */

import { defineStore } from 'pinia';
import { computed, ref, watch } from 'vue';

import * as authApi from '@/api/auth';
import type { UserRead } from '@/api/auth';
import { fetchMyGrants } from '@/api/users';
import type { MyGrantItem } from '@/api/users';

const TOKEN_KEY = 'corelab.auth.token';
const USER_KEY = 'corelab.auth.user';
const EXPIRES_KEY = 'corelab.auth.expiresAt';

function safeGet(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function safeSet(key: string, value: string | null): void {
  try {
    if (value === null) {
      localStorage.removeItem(key);
    } else {
      localStorage.setItem(key, value);
    }
  } catch {
    // SSR or sandboxed test env without storage — ignore.
  }
}

function loadStoredUser(): UserRead | null {
  const raw = safeGet(USER_KEY);
  if (raw === null) {
    return null;
  }
  try {
    return JSON.parse(raw) as UserRead;
  } catch {
    return null;
  }
}

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string | null>(safeGet(TOKEN_KEY));
  const user = ref<UserRead | null>(loadStoredUser());
  const expiresAt = ref<string | null>(safeGet(EXPIRES_KEY));
  const serverAdminGrants = ref<MyGrantItem[]>([]);

  const isAuthenticated = computed(() => token.value !== null);
  const isLabAdmin = computed(() => user.value?.role === 'lab_admin');
  const isServerAdminOfAny = computed(() => isLabAdmin.value || serverAdminGrants.value.length > 0);

  watch(token, (v) => safeSet(TOKEN_KEY, v));
  watch(user, (v) => safeSet(USER_KEY, v === null ? null : JSON.stringify(v)));
  watch(expiresAt, (v) => safeSet(EXPIRES_KEY, v));

  async function login(username: string, password: string): Promise<UserRead> {
    const resp = await authApi.login(username, password);
    token.value = resp.access_token;
    user.value = resp.user;
    expiresAt.value = resp.expires_at;
    void loadGrants();
    return resp.user;
  }

  async function logout(): Promise<void> {
    try {
      await authApi.logout();
    } catch {
      // best-effort; clear local state regardless
    }
    clearSession();
  }

  async function refreshMe(): Promise<UserRead | null> {
    if (!isAuthenticated.value) {
      return null;
    }
    try {
      const fresh = await authApi.fetchMe();
      user.value = fresh;
      void loadGrants();
      return fresh;
    } catch {
      clearSession();
      return null;
    }
  }

  async function loadGrants(): Promise<void> {
    if (!isAuthenticated.value) {
      serverAdminGrants.value = [];
      return;
    }
    try {
      serverAdminGrants.value = await fetchMyGrants();
    } catch {
      // best-effort — sidebar will simply skip the manage groups.
      serverAdminGrants.value = [];
    }
  }

  function clearSession(): void {
    token.value = null;
    user.value = null;
    expiresAt.value = null;
    serverAdminGrants.value = [];
  }

  return {
    token,
    user,
    expiresAt,
    serverAdminGrants,
    isAuthenticated,
    isLabAdmin,
    isServerAdminOfAny,
    login,
    logout,
    refreshMe,
    loadGrants,
    clearSession,
  };
});

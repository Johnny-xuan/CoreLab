/**
 * Router with auth + setup gates.
 *
 * Three gates run in order:
 * 1. Setup gate — every non-/setup route checks /setup/status; if the
 *    lab isn't initialized, route to /setup. /setup itself is reachable
 *    only when uninitialized (otherwise redirect to /login).
 * 2. Public route bypass — /login, /register, /activate, /setup never need a token.
 * 3. Auth gate — protected routes require ``auth.isAuthenticated``.
 */

import { createRouter, createWebHistory } from 'vue-router';
import type { RouteRecordRaw } from 'vue-router';

import { getSetupStatus } from '@/api/setup';
import { useAuthStore } from '@/stores/auth';

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/Login.vue'),
    meta: { public: true },
  },
  {
    path: '/activate',
    name: 'activate',
    component: () => import('@/views/Activate.vue'),
    meta: { public: true },
  },
  {
    path: '/register',
    name: 'register',
    component: () => import('@/views/Activate.vue'),
    meta: { public: true },
  },
  {
    path: '/setup',
    name: 'setup',
    component: () => import('@/views/Setup.vue'),
    meta: { public: true, setupOnly: true },
  },
  {
    path: '/',
    redirect: { name: 'dashboard' },
  },
  {
    path: '/me/dashboard',
    name: 'dashboard',
    component: () => import('@/views/Dashboard.vue'),
  },
  {
    path: '/me/profile',
    name: 'my-profile',
    component: () => import('@/views/MyProfile.vue'),
  },
  {
    path: '/admin/overview',
    name: 'lab-overview',
    component: () => import('@/views/LabOverview.vue'),
    meta: { adminOnly: true, title: '实验室概览' },
  },
  {
    path: '/admin/users',
    name: 'admin-users',
    component: () => import('@/views/AdminUsers.vue'),
    meta: { adminOnly: true },
  },
  {
    path: '/admin/users/:id(\\d+)',
    name: 'admin-user-detail',
    component: () => import('@/views/AdminUserDetail.vue'),
    meta: { adminOnly: true, title: '用户详情' },
  },
  {
    path: '/admin/enrollment-tokens',
    name: 'admin-enrollment-tokens',
    component: () => import('@/views/AdminEnrollmentTokens.vue'),
    meta: { adminOnly: true },
  },
  {
    // Phase M v5 — domain / public-URL management. Lives off Overview
    // because URL management is a treatment action (add domain, see
    // tunnel command), not an observation, and Overview is now strictly
    // data-observation per Phase L's 观察 vs 治理 divide.
    path: '/admin/domain',
    name: 'admin-domain',
    component: () => import('@/views/AdminDomain.vue'),
    meta: { adminOnly: true, title: '域名' },
  },
  {
    path: '/servers',
    name: 'servers',
    component: () => import('@/views/Servers.vue'),
  },
  {
    path: '/servers/:id(\\d+)',
    name: 'server-detail',
    component: () => import('@/views/ServerDetail.vue'),
  },
  {
    path: '/me/accounts/claim',
    name: 'claim-account',
    component: () => import('@/views/ClaimAccount.vue'),
    meta: { title: '关联 Linux 账号' },
  },
  {
    path: '/me/accounts/:pa_id(\\d+)',
    name: 'pa-workspace',
    component: () => import('@/views/PaWorkspace.vue'),
    meta: { title: '工作区' },
  },
  {
    path: '/me/accounts/:pa_id(\\d+)/reserve',
    name: 'pa-reserve',
    component: () => import('@/views/PaReserve.vue'),
    meta: { title: '预约 GPU' },
  },
  {
    path: '/me/accounts/:pa_id(\\d+)/reservations',
    name: 'pa-reservations',
    component: () => import('@/views/PaReservations.vue'),
    meta: { title: '我的预约' },
  },
  {
    path: '/me/accounts/:pa_id(\\d+)/scripts',
    name: 'pa-scripts',
    component: () => import('@/views/MyScripts.vue'),
    meta: { title: '脚本' },
  },
  {
    path: '/me/accounts/:pa_id(\\d+)/templates',
    name: 'pa-templates',
    redirect: (to) => ({
      name: 'pa-scripts',
      params: { pa_id: to.params.pa_id },
    }),
  },
  {
    path: '/me/accounts/:pa_id(\\d+)/server',
    name: 'pa-server-status',
    component: () => import('@/views/PaServerStatus.vue'),
    meta: { title: '服务器状态' },
  },
  {
    // Phase H — Settings is now a tab inside PaWorkspace; keep the route
    // for backwards-compat links and redirect to the workspace ?tab=settings.
    path: '/me/accounts/:pa_id(\\d+)/settings',
    name: 'pa-settings',
    redirect: (to) => ({
      name: 'pa-workspace',
      params: { pa_id: to.params.pa_id },
      query: { tab: 'settings' },
    }),
  },
  {
    path: '/me/all-reservations',
    name: 'all-reservations',
    component: () => import('@/views/AllReservations.vue'),
    meta: { title: '全部预约' },
  },
  {
    path: '/me/reservations',
    redirect: { name: 'all-reservations' },
  },
  {
    path: '/me/usage',
    name: 'my-usage',
    component: () => import('@/views/MyUsage.vue'),
  },
  {
    path: '/account-link-requests',
    name: 'account-link-requests',
    component: () => import('@/views/AccountLinkRequests.vue'),
  },
  {
    path: '/me/account-links',
    name: 'my-account-links',
    component: () => import('@/views/MyAccountLinks.vue'),
    meta: { title: '我的账号关联' },
  },
  {
    path: '/manage/servers',
    name: 'managed-servers',
    component: () => import('@/views/ManagedServersList.vue'),
    meta: { title: '我管理的服务器' },
  },
  {
    path: '/manage/server/:server_id(\\d+)',
    name: 'manage-server',
    component: () => import('@/views/ManageServer.vue'),
    meta: { title: '管理服务器' },
  },
  {
    path: '/manage/server/:server_id(\\d+)/gpu/:gpu_index(\\d+)',
    name: 'manage-server-gpu',
    component: () => import('@/views/GpuUsage.vue'),
    meta: { title: 'GPU 用量' },
  },
  {
    path: '/manage/inbox',
    name: 'server-admin-inbox',
    component: () => import('@/views/ServerAdminInbox.vue'),
    meta: { title: '关联申请' },
  },
  {
    path: '/lab/audit',
    name: 'lab-audit',
    component: () => import('@/views/AuditLogsView.vue'),
  },
  {
    path: '/lab/alerts',
    name: 'lab-alerts',
    component: () => import('@/views/AlertsView.vue'),
  },
  {
    path: '/:pathMatch(.*)*',
    redirect: { name: 'dashboard' },
  },
];

const router = createRouter({
  history: createWebHistory(),
  routes,
});

// Cache the initialized status during this session — we only need to
// re-check after a /setup/init that flips it from false → true (the
// Setup wizard calls flushSetupCache() on success).
let initializedCache: boolean | null = null;

async function checkInitialized(force = false): Promise<boolean> {
  if (initializedCache !== null && !force) {
    return initializedCache;
  }
  try {
    const s = await getSetupStatus();
    initializedCache = s.initialized;
  } catch {
    // Fail-open: if /setup/status itself errors we don't want to lock
    // every route. The Setup wizard will surface a real error later.
    initializedCache = true;
  }
  return initializedCache;
}

export function flushSetupCache(): void {
  initializedCache = null;
}

router.beforeEach(async (to) => {
  const initialized = await checkInitialized();

  // Setup gate
  if (!initialized && to.name !== 'setup' && to.name !== 'activate' && to.name !== 'register') {
    return { name: 'setup' };
  }
  if (initialized && to.meta.setupOnly === true) {
    return { name: 'login' };
  }

  const auth = useAuthStore();

  if (to.meta.public === true) {
    if (to.name === 'login' && auth.isAuthenticated) {
      return { name: 'dashboard' };
    }
    return true;
  }

  if (!auth.isAuthenticated) {
    return { name: 'login', query: { redirect: to.fullPath } };
  }

  if (to.meta.adminOnly === true && !auth.isLabAdmin) {
    return { name: 'dashboard' };
  }

  return true;
});

export default router;

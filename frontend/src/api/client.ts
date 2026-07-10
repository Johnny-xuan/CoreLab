/**
 * Shared axios client.
 *
 * Attaches the bearer token from the auth store to every outbound
 * request and redirects to /login on 401. The interceptor reads the
 * store lazily so it works even before Pinia is mounted at import time
 * (test harnesses, SSR-style entry points).
 */

import axios, { AxiosError } from 'axios';

import router from '@/router';
import { useAuthStore } from '@/stores/auth';

export const apiClient = axios.create({
  baseURL: '/api/v1',
  timeout: 15_000,
  headers: {
    'Content-Type': 'application/json',
  },
  // FastAPI expects repeated params (`a=1&a=2`); axios defaults to `a[]=1`.
  paramsSerializer: { indexes: null },
});

apiClient.interceptors.request.use((config) => {
  const auth = useAuthStore();
  if (auth.token !== null) {
    config.headers.set('Authorization', `Bearer ${auth.token}`);
  }
  return config;
});

apiClient.interceptors.response.use(
  (resp) => resp,
  async (error: AxiosError) => {
    if (error.response?.status === 401) {
      const auth = useAuthStore();
      auth.clearSession();
      const current = router.currentRoute.value;
      if (current.name !== 'login' && current.meta.public !== true) {
        await router.push({ name: 'login', query: { redirect: current.fullPath } });
      }
    }
    throw error;
  },
);

export const probeClient = axios.create({
  baseURL: '/',
  timeout: 5_000,
});

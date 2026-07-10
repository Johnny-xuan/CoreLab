/** Auth + /me API helpers. */

import { apiClient } from './client';

export interface UserRead {
  id: number;
  lab_id: number;
  username: string;
  email: string;
  display_name: string;
  role: 'user' | 'lab_admin';
  is_active: boolean;
  /** True once the user set a password; false = invited but still pending. */
  is_activated: boolean;
  last_login_at: string | null;
  created_at: string;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
  expires_at: string;
  user: UserRead;
}

export async function login(username: string, password: string): Promise<LoginResponse> {
  const resp = await apiClient.post<LoginResponse>('/auth/login', { username, password });
  return resp.data;
}

export async function logout(): Promise<void> {
  await apiClient.post('/auth/logout');
}

export async function fetchMe(): Promise<UserRead> {
  const resp = await apiClient.get<UserRead>('/auth/me');
  return resp.data;
}

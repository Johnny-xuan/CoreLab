/** Setup wizard + activation flow API helpers. */

import { apiClient } from './client';

import type { UserRead } from './auth';

export interface SetupStatus {
  initialized: boolean;
}

export interface SetupInitRequest {
  lab_name: string;
  lab_slug: string;
  admin_username: string;
  admin_email: string;
  admin_display_name: string;
  admin_password: string;
}

export interface SetupInitResponse {
  lab_id: number;
  admin: UserRead;
}

export interface ActivateValidate {
  user_id: number | null;
  username: string | null;
  email: string | null;
  display_name: string | null;
  purpose: 'registration' | 'activation' | 'password_reset';
  role: 'user' | 'lab_admin';
}

export interface ActivateSubmit {
  username?: string;
  email?: string;
  display_name?: string;
  password: string;
  ssh_key_label?: string;
  ssh_key_public_key?: string;
}

export async function getSetupStatus(): Promise<SetupStatus> {
  const resp = await apiClient.get<SetupStatus>('/setup/status');
  return resp.data;
}

export async function suggestSlug(name: string): Promise<string> {
  const resp = await apiClient.get<{ slug: string }>('/setup/suggest-slug', {
    params: { name },
  });
  return resp.data.slug;
}

export async function setupInit(payload: SetupInitRequest): Promise<SetupInitResponse> {
  const resp = await apiClient.post<SetupInitResponse>('/setup/init', payload);
  return resp.data;
}

export async function validateActivationToken(token: string): Promise<ActivateValidate> {
  const resp = await apiClient.get<ActivateValidate>('/setup/activate/validate', {
    params: { token },
  });
  return resp.data;
}

export async function activate(token: string, payload: ActivateSubmit): Promise<UserRead> {
  const resp = await apiClient.post<UserRead>('/setup/activate', { token, ...payload });
  return resp.data;
}

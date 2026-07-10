/** SSH public key API helpers. */

import { apiClient } from './client';

export interface SshKeyRead {
  id: number;
  user_id: number;
  public_key: string;
  fingerprint_sha256: string;
  key_type: string;
  comment: string | null;
  is_active: boolean;
  created_at: string;
}

export interface SshKeyCreate {
  public_key: string;
  label?: string;
}

export interface SshKeyDeleteResponse {
  id: number;
  result: 'deleted' | 'already_inactive';
}

export async function listMyKeys(): Promise<SshKeyRead[]> {
  const resp = await apiClient.get<SshKeyRead[]>('/users/me/ssh-keys');
  return resp.data;
}

export async function addMyKey(payload: SshKeyCreate): Promise<SshKeyRead> {
  const resp = await apiClient.post<SshKeyRead>('/users/me/ssh-keys', payload);
  return resp.data;
}

export async function deleteMyKey(id: number): Promise<SshKeyDeleteResponse> {
  const resp = await apiClient.delete<SshKeyDeleteResponse>(`/users/me/ssh-keys/${id}`);
  return resp.data;
}

export async function listUserKeys(userId: number): Promise<SshKeyRead[]> {
  const resp = await apiClient.get<SshKeyRead[]>(`/users/${userId}/ssh-keys`);
  return resp.data;
}

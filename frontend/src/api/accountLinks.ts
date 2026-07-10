/** AccountLink + SSH challenge / PAM verify / upgrade API helpers. */

import { apiClient } from './client';

export type LinkSource =
  | 'ssh_challenge'
  | 'password_pam'
  | 'admin_prepared_then_ssh'
  | 'admin_declared';

export type RevokeReason =
  | 'self'
  | 'admin_force'
  | 'user_disabled'
  | 'pa_disabled'
  | 'upgraded_to_verified';

export interface AccountLinkRead {
  id: number;
  user_id: number;
  physical_account_id: number;
  source: LinkSource;
  proof_evidence: Record<string, unknown>;
  established_at: string;
  is_active: boolean;
  revoked_at: string | null;
  revoke_reason: RevokeReason | null;
}

export interface ChallengeIssued {
  challenge_id: string;
  nonce: string;
  expires_at: string;
  sign_command: string;
  signing_namespace: string;
}

export interface ChallengePayload {
  server_id: number;
  linux_username: string;
  ssh_public_key_id: number;
}

export interface VerifyPayload {
  challenge_id: string;
  signature_armored: string;
}

export interface VerifyResponse {
  account_link: AccountLinkRead;
  signer_fingerprint: string;
}

export interface PamVerifyPayload {
  server_id: number;
  linux_username: string;
  password: string;
}

export interface PamVerifyResponse {
  account_link: AccountLinkRead;
}

export interface UpgradePayload {
  challenge_id: string;
  signature_armored: string;
}

export interface UpgradeResponse {
  account_link: AccountLinkRead;
  signer_fingerprint: string;
  upgraded_from_link_id: number;
}

export async function listMyAccountLinks(includeHistory = false): Promise<AccountLinkRead[]> {
  const params = includeHistory ? { include_history: true } : undefined;
  return (await apiClient.get<AccountLinkRead[]>('/users/me/account-links', { params })).data;
}

export async function getAccountLink(linkId: number): Promise<AccountLinkRead> {
  return (await apiClient.get<AccountLinkRead>(`/account-links/${linkId}`)).data;
}

export async function createChallenge(payload: ChallengePayload): Promise<ChallengeIssued> {
  return (await apiClient.post<ChallengeIssued>('/account-links/challenge', payload)).data;
}

export async function verifyChallenge(payload: VerifyPayload): Promise<VerifyResponse> {
  return (await apiClient.post<VerifyResponse>('/account-links/verify', payload)).data;
}

export async function tryPassword(payload: PamVerifyPayload): Promise<PamVerifyResponse> {
  return (await apiClient.post<PamVerifyResponse>('/account-links/try', payload)).data;
}

export async function upgradeViaChallenge(
  linkId: number,
  payload: UpgradePayload,
): Promise<UpgradeResponse> {
  return (
    await apiClient.post<UpgradeResponse>(`/account-links/${linkId}/upgrade-via-challenge`, payload)
  ).data;
}

export async function revokeLink(
  linkId: number,
  reason: RevokeReason = 'self',
  revokeKey = true,
): Promise<AccountLinkRead> {
  return (
    await apiClient.post<AccountLinkRead>(`/account-links/${linkId}/revoke`, {
      reason,
      revoke_key: revokeKey,
    })
  ).data;
}

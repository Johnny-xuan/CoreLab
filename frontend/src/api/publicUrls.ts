/** Phase M v5 — /admin/public-urls + tunnel mode endpoints. */

import { apiClient } from './client';

export type PublicUrlKind =
  | 'lan'
  | 'public_ip'
  | 'custom_domain'
  | 'cloudflare_quick'
  | 'cloudflare_named';

export type PublicUrlSource = 'install_sh_probe' | 'manual_admin' | 'tunnel_runtime';

export type TunnelMode = 'none' | 'cloudflare_quick' | 'cloudflare_named';

export interface PublicUrlEntry {
  url: string;
  kind: PublicUrlKind;
  source: PublicUrlSource;
  verified_at: string | null;
  last_reachable_at: string | null;
  primary: boolean;
  reachable: boolean | null;
}

export interface PublicUrlsResponse {
  urls: PublicUrlEntry[];
  tunnel_mode: TunnelMode;
  tunnel_token_set: boolean;
}

export interface AddPublicUrlRequest {
  url: string;
  kind?: PublicUrlKind;
  make_primary?: boolean;
}

export interface DomainVerifyResponse {
  domain: string;
  resolved: string[];
  matches_expected: boolean;
  expected_any: string[];
}

export async function listPublicUrls(): Promise<PublicUrlsResponse> {
  const { data } = await apiClient.get<PublicUrlsResponse>('/admin/public-urls');
  return data;
}

export async function addPublicUrl(payload: AddPublicUrlRequest): Promise<PublicUrlsResponse> {
  const { data } = await apiClient.post<PublicUrlsResponse>('/admin/public-urls', payload);
  return data;
}

export async function removePublicUrl(url: string): Promise<PublicUrlsResponse> {
  const { data } = await apiClient.delete<PublicUrlsResponse>('/admin/public-urls', {
    params: { url },
  });
  return data;
}

export async function probePublicUrlsNow(): Promise<PublicUrlsResponse> {
  const { data } = await apiClient.post<PublicUrlsResponse>('/admin/public-urls/probe-now');
  return data;
}

export async function verifyDomain(domain: string): Promise<DomainVerifyResponse> {
  const { data } = await apiClient.post<DomainVerifyResponse>('/admin/public-urls/verify-domain', {
    domain,
  });
  return data;
}

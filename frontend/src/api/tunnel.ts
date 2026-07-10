/** Phase M v5 (post-review) — tunnel state is read-only from the web UI.
 *
 * Toggle / upgrade / disable are CLI actions; the Domain page shows the
 * corresponding shell commands instead of letting the admin click a
 * button that would change how the platform itself is reachable.
 */

import { apiClient } from './client';
import type { TunnelMode } from './publicUrls';

export interface TunnelStatusResponse {
  tunnel_mode: TunnelMode;
  tunnel_token_set: boolean;
}

export async function getTunnelStatus(): Promise<TunnelStatusResponse> {
  const { data } = await apiClient.get<TunnelStatusResponse>('/admin/tunnel/status');
  return data;
}

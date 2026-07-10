/** /api/v1/usage/me — typed wrapper. */

import { apiClient } from './client';

export interface UsageByServer {
  server_id: number;
  hostname: string;
  hours: number;
}

export interface UsageByPa {
  pa_id: number;
  linux_username: string;
  hostname: string;
  hours: number;
}

export interface UsageResponse {
  month: string;
  gpu_hours_used: number;
  completion_rate: number;
  reservation_count: number;
  by_server: UsageByServer[];
  by_pa: UsageByPa[];
  alerts_received: number;
  compliance_violations: number;
}

export async function getMyUsage(month: string): Promise<UsageResponse> {
  const resp = await apiClient.get<UsageResponse>('/usage/me', { params: { month } });
  return resp.data;
}

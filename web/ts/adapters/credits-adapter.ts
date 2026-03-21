/** Cost adapter — fetch cost summary and transparency info. */

import { apiFetch } from '../utils';

export interface CostBreakdown {
  token_cost_usd: number;
  infra_share_usd: number;
  subscription_share_usd: number;
  storage_cost_usd: number;
  subtotal_usd: number;
  margin_usd: number;
  total_usd: number;
  credits_charged: number;
  config_id: number;
}

export interface Transaction {
  id: number;
  timestamp: string;
  cost_usd: number;
  category: string;
  description: string;
  breakdown: CostBreakdown | null;
}

export interface CostSummary {
  total_cost_usd: number;
  cost_this_month_usd: number;
  cost_this_week_usd: number;
  total_briefings: number;
  recent_transactions: Transaction[];
}

export interface TransparencyInfo {
  token_cost_per_1k_input: number;
  token_cost_per_1k_output: number;
  infra_monthly_usd: number;
  subscriptions_monthly_usd: number;
  disk_cost_per_gb_monthly: number;
  estimated_monthly_briefings: number;
  margin_percent: number;
  usd_per_credit: number;
}

export async function fetchCostSummary(): Promise<CostSummary> {
  return apiFetch<CostSummary>('/user/credits');
}

export async function fetchTransparency(): Promise<TransparencyInfo | null> {
  return apiFetch<TransparencyInfo | null>('/transparency');
}

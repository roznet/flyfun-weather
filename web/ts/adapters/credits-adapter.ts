/** Credits adapter — fetch balance, transactions, and transparency info. */

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
  amount: number;
  balance_after: number;
  category: string;
  description: string;
  breakdown: CostBreakdown | null;
}

export interface CreditSummary {
  balance: number;
  recent_transactions: Transaction[];
  credits_used_today: number;
  credits_used_month: number;
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

export async function fetchCreditSummary(): Promise<CreditSummary> {
  return apiFetch<CreditSummary>('/user/credits');
}

export async function fetchTransparency(): Promise<TransparencyInfo | null> {
  return apiFetch<TransparencyInfo | null>('/transparency');
}

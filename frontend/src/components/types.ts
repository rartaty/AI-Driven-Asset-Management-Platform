export interface Position {
  symbol: string;
  name: string;
  category: string;
  shares: number;
  avg_price: number;
  current_price: number;
  unrealized_pnl: number;
}

export interface Activity {
  id?: string;
  timestamp: string;
  type: string;
  message?: string;
  title?: string;
  description?: string;
  reason?: string;
  is_loss?: boolean;
}

export interface ChartData {
  date: string;
  Trust: number;
  Long: number;
  Cash: number;
}

export interface PortfolioSummary {
  target_date: string;
  trust_value: number;
  long_value: number;
  short_value: number;
  cash_balance: number;
  total_value: number;
  accumulated_sweep: number;
  positions: Position[];
  recent_activity: Activity[];
  chart_data: ChartData[];
  is_mock: boolean;
}

export const formatYen = (amount: number) => {
  return new Intl.NumberFormat("ja-JP", { style: "currency", currency: "JPY" }).format(amount);
};

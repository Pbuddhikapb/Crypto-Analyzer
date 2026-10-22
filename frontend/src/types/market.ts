export interface Candle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface FVGBox {
  type: 'BULLISH' | 'BEARISH';
  top: number;
  bottom: number;
  ce?: number;
  candle_index: number;
}

export interface HistoricalBaseline {
  pattern: string;
  strategy_name?: string | null;
  win_rate: number | null;
  profit_factor: number | null;
  sample_trades: number | null;
  status: string;
  message?: string;
  expectancy_r?: number | null;
  bootstrap_ci?: [number, number] | null;
  matched_p_value?: number | null;
  verdict?: string | null;
  run_timestamp?: string | null;
  data_date_range?: string | null;
}

export interface TradeSetup {
  side: 'LONG' | 'SHORT';
  limit_entry: number;
  stop_loss: number;
  risk_dist: number;
  risk_pct: number;
  tp1: number;
  tp1_r?: number;
  be_trigger?: number;
  be_trigger_r?: number;
  be_price?: number;
  tp2: number;
  tp2_r?: number;
  stagnancy_bars?: number;
  rr_ratio: number;
  macro_bias: string;
  is_counter_trend: boolean;
  recommendation: string;
  historical_baseline?: HistoricalBaseline;
}

export interface ScannerOpportunity {
  symbol: string;
  current_price: number;
  trend: 'BULLISH' | 'BEARISH' | 'NEUTRAL';
  htf_trend: string;
  adx_14: number;
  volume_surge: number;
  confluence_score: number;
  is_chop: boolean;
  inflection_pattern?: string;
  historical_baseline?: HistoricalBaseline;
  trade_setup: TradeSetup | null;
}

export interface Indicators {
  ema_50: number | null;
  ema_200: number | null;
  rsi: number | null;
  trend: 'BULLISH' | 'BEARISH' | 'NEUTRAL';
  has_bullish_fvg: boolean;
  has_bearish_fvg: boolean;
  has_bullish_divergence: boolean;
  has_bearish_divergence: boolean;
  active_fvg_boxes: FVGBox[];
  adx_14?: number | null;
  atr_14?: number | null;
  is_chop?: boolean;
  trade_setup?: TradeSetup | null;
  fibonacci_levels?: {
    "0.0": number;
    "0.5": number;
    "0.618": number;
    "0.705": number;
    "0.786": number;
    "1.0": number;
    range_span: number;
  } | null;
  dealing_range?: {
    swing_high: number;
    swing_low: number;
    range_span: number;
    range_span_pct: number;
  } | null;
  pricing_zone?: string | null;
  market_structure?: {
    current_trend?: string;
    last_event?: string;
    last_event_side?: string;
    bars_since_last_event?: number;
    is_in_range?: boolean;
  } | null;
  order_blocks?: Array<{
    index: number;
    type: string;
    top: number;
    bottom: number;
    mean_threshold: number;
    status: string;
  }> | null;
  breaker_blocks?: Array<{
    origin_index: number;
    type: string;
    top: number;
    bottom: number;
    mean_threshold: number;
    status: string;
  }> | null;
  order_block_confluence?: {
    has_confluence: boolean;
    message?: string;
    block_type?: string | null;
    mean_threshold_tapped?: boolean;
  } | null;
}

export interface CandleResponse {
  symbol: string;
  timeframe: string;
  candles: Candle[];
  indicators: Indicators;
}

export interface MarketSymbol {
  symbol: string;
  futures_symbol: string;
  latest_price: number | null;
  has_open_position: boolean;
  trend: string;
  rsi: number | null;
}

export interface WhaleTrade {
  symbol: string;
  price: number;
  quantity: number;
  notional: number;
  side: 'BUY' | 'SELL';
  badge: 'DOLPHIN' | 'SHARK' | 'WHALE';
  timestamp: number;
}

export interface LiquidationEvent {
  symbol: string;
  liquidated_position: 'LONG' | 'SHORT';
  price: number;
  quantity: number;
  notional: number;
  timestamp: number;
  rolling_60s_volume: number;
  is_cascade: boolean;
}

export interface ActiveTrade {
  id?: number;
  symbol: string;
  side: 'LONG' | 'SHORT';
  entry_price: number;
  stop_loss: number;
  take_profit: number;
  timestamp: string;
  unrealized_pnl_pct?: number;
  unrealized_pnl_usd?: number;
  current_r_multiple?: number;
  current_price?: number;
  quantity?: number;
  notional_usd?: number;
  leverage?: number;
  initial_stop_loss?: number;
  tp1_price?: number;
  tp1_hit?: boolean;
  be_armed?: boolean;
  breakeven_set?: boolean;
  entry_bar?: number;
  risk_dist?: number;
  confluence_score?: number;
  bars_held?: number;
  max_stagnancy_bars?: number;
}

export interface PortfolioSummary {
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  win_rate_pct: number;
  total_net_return_pct: number;
  avg_r_multiple: number;
  symbols_traded: string[];
  active_positions_count: number;
  max_open_positions: number;
  active_trades: ActiveTrade[];
  account_equity_usd?: number;
  allocated_notional_usd?: number;
  portfolio_leverage?: number;
  available_equity_usd?: number;
  total_unrealized_pnl_usd?: number;
  current_equity_usd?: number;
}

export interface MicrostructureSummary {
  symbol: string;
  futures_symbol: string;
  is_futures_online: boolean;
  oi_regime: string;
  funding_rate: number;
  funding_apr_pct: number;
  funding_countdown: string;
  rolling_60s_liquidation_volume: number;
  is_cascade_alert: boolean;
  recent_whale_trades: WhaleTrade[];
}

export interface SystemHealth {
  status: string;
  data_source?: string;
  last_update_per_symbol?: Record<string, number>;
  last_update_times?: Record<string, number>;
  timeframe?: string;
  candle_interval_sec?: number;
  spot_connectivity: boolean;
  futures_connectivity: boolean;
  futures_status_message: string;
  active_symbols_count: number;
  db_status: string;
  connected_ws_clients: number;
}

export interface OrderBookLevel {
  price: number;
  qty: number;
  total: number;
}

export interface OrderBookDepthData {
  symbol: string;
  timestamp_ms: number;
  bids: OrderBookLevel[];
  asks: OrderBookLevel[];
  best_bid: number;
  best_ask: number;
  spread: number;
  spread_bps: number;
  mid_price: number;
  weighted_mid: number;
  imbalance_pct: number;
  bid_walls: { price: number; qty: number }[];
  ask_walls: { price: number; qty: number }[];
}

export type WSMessage =
  | { type: 'tick'; symbol: string; price: number; timestamp: number }
  | { type: 'candle'; symbol: string; candle: Candle; indicators: Indicators }
  | { type: 'whale_trade'; trade: WhaleTrade }
  | { type: 'liquidation'; event: LiquidationEvent }
  | { type: 'order_book_depth'; depth: OrderBookDepthData }
  | { type: 'trade_opened'; trade: ActiveTrade }
  | { type: 'trade_closed'; symbol: string }
  | { type: 'pong'; timestamp: number };

import {
  CandleResponse,
  MarketSymbol,
  MicrostructureSummary,
  PortfolioSummary,
  SystemHealth,
} from '../types/market';

const API_BASE = '/api';

export async function fetchHealth(): Promise<SystemHealth> {
  const res = await fetch(`${API_BASE}/health`);
  if (!res.ok) throw new Error(`HTTP ${res.status}: Failed to fetch health`);
  return res.json();
}

export async function fetchMarketSymbols(): Promise<MarketSymbol[]> {
  const res = await fetch(`${API_BASE}/market/symbols`);
  if (!res.ok) throw new Error(`HTTP ${res.status}: Failed to fetch market symbols`);
  return res.json();
}

export async function fetchCandles(
  symbol: string,
  timeframe: string = '1m',
  limit: number = 250
): Promise<CandleResponse> {
  const res = await fetch(
    `${API_BASE}/market/candles?symbol=${encodeURIComponent(symbol)}&timeframe=${encodeURIComponent(timeframe)}&limit=${limit}`
  );
  if (!res.ok) throw new Error(`HTTP ${res.status}: Failed to fetch candles for ${symbol}`);
  return res.json();
}

export async function fetchPortfolioSummary(): Promise<PortfolioSummary> {
  const res = await fetch(`${API_BASE}/portfolio/summary`);
  if (!res.ok) throw new Error(`HTTP ${res.status}: Failed to fetch portfolio summary`);
  return res.json();
}

export async function fetchMicrostructureSummary(symbol: string): Promise<MicrostructureSummary> {
  const res = await fetch(`${API_BASE}/microstructure/summary?symbol=${encodeURIComponent(symbol)}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}: Failed to fetch microstructure summary for ${symbol}`);
  return res.json();
}

export async function fetchScannerOpportunities(): Promise<import('../types/market').ScannerOpportunity[]> {
  const res = await fetch(`${API_BASE}/scanner/opportunities`);
  if (!res.ok) throw new Error(`HTTP ${res.status}: Failed to fetch scanner opportunities`);
  return res.json();
}

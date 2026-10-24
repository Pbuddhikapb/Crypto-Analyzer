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

export interface ScannerOpportunitiesMeta {
  opportunities: import('../types/market').ScannerOpportunity[];
  cacheAgeMs: number;
  fetchedAt: number;
}

export async function fetchScannerOpportunitiesWithMeta(): Promise<ScannerOpportunitiesMeta> {
  const res = await fetch(`${API_BASE}/scanner/opportunities`);
  if (!res.ok) throw new Error(`HTTP ${res.status}: Failed to fetch scanner opportunities`);
  const ageHeader = res.headers.get('x-cache-age-ms');
  const cacheAgeMs = ageHeader ? parseInt(ageHeader, 10) : 0;
  const opportunities = await res.json();
  return {
    opportunities,
    cacheAgeMs: isNaN(cacheAgeMs) ? 0 : cacheAgeMs,
    fetchedAt: Date.now(),
  };
}

export async function fetchScannerOpportunities(): Promise<import('../types/market').ScannerOpportunity[]> {
  const meta = await fetchScannerOpportunitiesWithMeta();
  return meta.opportunities;
}

export async function fetchMarketDepth(symbol: string): Promise<import('../types/market').OrderBookDepthData> {
  const res = await fetch(`${API_BASE}/market/depth?symbol=${encodeURIComponent(symbol)}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}: Failed to fetch market depth for ${symbol}`);
  return res.json();
}

export async function fetchTradeBracket(
  symbol: string,
  side: 'LONG' | 'SHORT',
  entryPrice: number,
  stopLoss: number
) {
  const res = await fetch(
    `${API_BASE}/trade/bracket?symbol=${encodeURIComponent(symbol)}&side=${encodeURIComponent(side)}&entry_price=${entryPrice}&stop_loss=${stopLoss}`
  );
  if (!res.ok) throw new Error(`HTTP ${res.status}: Failed to calculate trade bracket`);
  return res.json();
}

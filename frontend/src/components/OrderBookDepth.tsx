import React, { useEffect, useState } from 'react';
import { Layers, ShieldAlert } from 'lucide-react';
import { OrderBookDepthData } from '../types/market';
import { fetchMarketDepth } from '../state/api';

interface OrderBookDepthProps {
  symbol: string;
  latestPrice?: number | null;
}

export const OrderBookDepth: React.FC<OrderBookDepthProps> = ({ symbol, latestPrice }) => {
  const [depth, setDepth] = useState<OrderBookDepthData | null>(null);

  // 1. Initial REST pre-seed on symbol change
  useEffect(() => {
    let cancelled = false;
    fetchMarketDepth(symbol)
      .then((data) => {
        if (!cancelled && data.symbol === symbol) {
          setDepth(data);
        }
      })
      .catch(() => {});

    return () => {
      cancelled = true;
    };
  }, [symbol]);

  // 2. High-performance event listener isolated strictly to this component (zero App re-renders)
  useEffect(() => {
    const handleDepthUpdate = (event: Event) => {
      const customEvent = event as CustomEvent<OrderBookDepthData>;
      if (customEvent.detail && customEvent.detail.symbol === symbol) {
        setDepth(customEvent.detail);
      }
    };

    window.addEventListener('crypto-order-book-depth', handleDepthUpdate);
    return () => {
      window.removeEventListener('crypto-order-book-depth', handleDepthUpdate);
    };
  }, [symbol]);

  // Derived state from real depth, with graceful fallback
  const bids = depth?.bids && depth.bids.length > 0 ? depth.bids : [];
  const asks = depth?.asks && depth.asks.length > 0 ? depth.asks : [];
  const imbalancePct = depth ? Math.round(depth.imbalance_pct) : 0;

  const maxTotal = Math.max(
    bids[bids.length - 1]?.total || 1,
    asks[asks.length - 1]?.total || 1
  );

  const formatLevelPrice = (p: number) => {
    if (p >= 1000) return p.toFixed(2);
    if (p >= 1) return p.toFixed(3);
    return p.toFixed(4);
  };

  const hasBidWall = (depth?.bid_walls?.length || 0) > 0;
  const hasAskWall = (depth?.ask_walls?.length || 0) > 0;

  return (
    <div className="h-64 flex flex-col bg-dark-panel border-b border-dark-border text-xs select-none">
      <div className="px-3 py-1.5 bg-dark-card border-b border-dark-border flex items-center justify-between">
        <div className="flex items-center space-x-1.5 font-bold text-slate-200">
          <Layers size={13} className="text-laser-cyan" />
          <span>ORDER BOOK &amp; IMBALANCE</span>
        </div>
        <div className="flex items-center space-x-2">
          {hasBidWall && (
            <span className="flex items-center text-[9px] text-laser-green bg-laser-green/10 px-1 rounded font-mono">
              <ShieldAlert size={10} className="mr-0.5" /> BID WALL
            </span>
          )}
          {hasAskWall && (
            <span className="flex items-center text-[9px] text-crimson-red bg-crimson-red/10 px-1 rounded font-mono">
              <ShieldAlert size={10} className="mr-0.5" /> ASK WALL
            </span>
          )}
          {latestPrice != null && (
            <span className="text-[10px] text-laser-cyan font-mono font-semibold">
              ${latestPrice >= 1 ? latestPrice.toFixed(2) : latestPrice.toFixed(4)}
            </span>
          )}
          <span className="text-[10px] text-slate-400 font-mono">{symbol}</span>
        </div>
      </div>

      {/* Imbalance Meter */}
      <div className="px-3 py-1 bg-dark-bg/60 border-b border-dark-border/40 flex items-center justify-between text-[11px] font-mono">
        <span className="text-laser-green font-bold">BIDS {Math.max(5, Math.min(95, 50 + imbalancePct / 2))}%</span>
        <span className="text-slate-400">OBI: {imbalancePct > 0 ? `+${imbalancePct}%` : `${imbalancePct}%`}</span>
        <span className="text-crimson-red font-bold">ASKS {Math.max(5, Math.min(95, 50 - imbalancePct / 2))}%</span>
      </div>

      {/* Visual OBI bar */}
      <div className="h-1 w-full flex bg-dark-card">
        <div
          className="bg-laser-green transition-all duration-300"
          style={{ width: `${Math.max(5, Math.min(95, 50 + imbalancePct / 2))}%` }}
        />
        <div
          className="bg-crimson-red transition-all duration-300"
          style={{ width: `${Math.max(5, Math.min(95, 50 - imbalancePct / 2))}%` }}
        />
      </div>

      {/* Depth Table */}
      <div className="flex-1 grid grid-cols-2 text-[10px] font-mono divide-x divide-dark-border/40 overflow-hidden">
        {/* Asks (Top) */}
        <div className="flex flex-col justify-end p-1 space-y-0.5">
          <span className="text-[9px] text-slate-500 uppercase px-1">Top Asks ($)</span>
          {asks.slice(0, 5).reverse().map((a, i) => {
            const depthPct = Math.min(100, (a.total / maxTotal) * 100);
            return (
              <div
                key={i}
                className="relative flex items-center justify-between px-1 py-0.5 overflow-hidden"
              >
                <div
                  className="absolute right-0 top-0 bottom-0 bg-crimson-red/10 pointer-events-none"
                  style={{ width: `${depthPct}%` }}
                />
                <span className="text-crimson-red font-semibold relative z-10">
                  {formatLevelPrice(a.price)}
                </span>
                <span className="text-slate-400 relative z-10">{a.qty.toFixed(2)}</span>
              </div>
            );
          })}
        </div>

        {/* Bids (Bottom) */}
        <div className="flex flex-col justify-start p-1 space-y-0.5">
          <span className="text-[9px] text-slate-500 uppercase px-1">Top Bids ($)</span>
          {bids.slice(0, 5).map((b, i) => {
            const depthPct = Math.min(100, (b.total / maxTotal) * 100);
            return (
              <div
                key={i}
                className="relative flex items-center justify-between px-1 py-0.5 overflow-hidden"
              >
                <div
                  className="absolute left-0 top-0 bottom-0 bg-laser-green/10 pointer-events-none"
                  style={{ width: `${depthPct}%` }}
                />
                <span className="text-laser-green font-semibold relative z-10">
                  {formatLevelPrice(b.price)}
                </span>
                <span className="text-slate-400 relative z-10">{b.qty.toFixed(2)}</span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
};

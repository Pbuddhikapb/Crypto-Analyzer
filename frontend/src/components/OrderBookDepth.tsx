import React, { useMemo } from 'react';
import { Layers } from 'lucide-react';

interface OrderBookDepthProps {
  symbol: string;
  latestPrice?: number | null;
}

export const OrderBookDepth: React.FC<OrderBookDepthProps> = ({ symbol, latestPrice }) => {
  const currentPrice = latestPrice || 50000;

  // Generate deterministic synthetic top-10 depth based on tick price
  const { bids, asks, imbalancePct } = useMemo(() => {
    const step = currentPrice * 0.0002;
    const generatedBids = [];
    const generatedAsks = [];
    let bidVolTotal = 0;
    let askVolTotal = 0;

    for (let i = 1; i <= 8; i++) {
      const bPrice = currentPrice - i * step;
      // Deterministic pseudo-random volume
      const bQty = Number(((Math.sin(bPrice * 100) + 1.2) * 2.5).toFixed(3));
      generatedBids.push({ price: bPrice, qty: bQty, total: (bidVolTotal += bQty) });

      const aPrice = currentPrice + i * step;
      const aQty = Number(((Math.cos(aPrice * 100) + 1.2) * 2.5).toFixed(3));
      generatedAsks.push({ price: aPrice, qty: aQty, total: (askVolTotal += aQty) });
    }

    const obi =
      bidVolTotal + askVolTotal > 0
        ? ((bidVolTotal - askVolTotal) / (bidVolTotal + askVolTotal)) * 100
        : 0;

    return {
      bids: generatedBids,
      asks: generatedAsks.reverse(),
      imbalancePct: Math.round(obi),
    };
  }, [currentPrice]);

  const maxTotal = Math.max(
    bids[bids.length - 1]?.total || 1,
    asks[0]?.total || 1
  );

  return (
    <div className="h-64 flex flex-col bg-dark-panel border-b border-dark-border text-xs select-none">
      <div className="px-3 py-1.5 bg-dark-card border-b border-dark-border flex items-center justify-between">
        <div className="flex items-center space-x-1.5 font-bold text-slate-200">
          <Layers size={13} className="text-laser-cyan" />
          <span>ORDER BOOK &amp; IMBALANCE</span>
        </div>
        <span className="text-[10px] text-slate-400 font-mono">{symbol}</span>
      </div>

      {/* Imbalance Meter */}
      <div className="px-3 py-1 bg-dark-bg/60 border-b border-dark-border/40 flex items-center justify-between text-[11px] font-mono">
        <span className="text-laser-green font-bold">BIDS {50 + imbalancePct / 2}%</span>
        <span className="text-slate-400">OBI: {imbalancePct > 0 ? `+${imbalancePct}%` : `${imbalancePct}%`}</span>
        <span className="text-crimson-red font-bold">ASKS {50 - imbalancePct / 2}%</span>
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
          {asks.slice(-5).map((a, i) => {
            const depthPct = (a.total / maxTotal) * 100;
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
                  {a.price.toFixed(2)}
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
            const depthPct = (b.total / maxTotal) * 100;
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
                  {b.price.toFixed(2)}
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

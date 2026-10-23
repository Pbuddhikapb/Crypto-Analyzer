import React, { useEffect, useRef, useMemo } from 'react';
import { MarketSymbol, ScannerOpportunity } from '../types/market';
import { Flame, Zap } from 'lucide-react';

interface TopCoinsBarProps {
  symbols: MarketSymbol[];
  activeSymbol: string;
  onSelectSymbol: (symbol: string) => void;
  latestPrices: Record<string, number>;
  opportunities?: ScannerOpportunity[];
}

export const TopCoinsBar: React.FC<TopCoinsBarProps> = ({
  symbols,
  activeSymbol,
  onSelectSymbol,
  latestPrices,
  opportunities = [],
}) => {
  const prevPricesRef = useRef<Record<string, number>>({});
  const flashClassesRef = useRef<Record<string, string>>({});

  const oppMap = useMemo(() => {
    const map = new Map<string, ScannerOpportunity>();
    opportunities.forEach((o) => map.set(o.symbol, o));
    return map;
  }, [opportunities]);

  const formatPrice = (price?: number | null): string => {
    if (price === undefined || price === null || isNaN(price)) return '$0.00';
    const absP = Math.abs(price);
    if (absP === 0) return '$0.00';
    if (absP < 0.01) return `$${price.toFixed(6)}`;
    if (absP < 1.0) return `$${price.toFixed(4)}`;
    return `$${price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  };

  useEffect(() => {
    Object.entries(latestPrices).forEach(([sym, price]) => {
      const prev = prevPricesRef.current[sym];
      if (prev !== undefined && prev !== price) {
        flashClassesRef.current[sym] = price > prev ? 'flash-up' : 'flash-down';
      }
      prevPricesRef.current[sym] = price;
    });
  }, [latestPrices]);

  return (
    <div className="h-10 bg-dark-bg border-b border-dark-border px-3 flex items-center space-x-2 overflow-x-auto no-scrollbar text-xs select-none">
      {symbols.map((coin) => {
        const isActive = coin.symbol === activeSymbol;
        const currentPrice = latestPrices[coin.symbol] ?? coin.latest_price;
        const flashClass = flashClassesRef.current[coin.symbol] || '';
        const opp = oppMap.get(coin.symbol);

        return (
          <button
            key={coin.symbol}
            onClick={() => onSelectSymbol(coin.symbol)}
            className={`flex items-center space-x-1.5 px-2.5 py-1 rounded transition whitespace-nowrap border text-xs md:text-sm ${
              isActive
                ? 'bg-dark-card border-laser-cyan text-slate-100 shadow-[0_0_8px_rgba(0,242,254,0.25)]'
                : 'bg-dark-panel/60 border-dark-border text-slate-300 hover:border-slate-600 hover:bg-dark-panel'
            } ${flashClass}`}
          >
            <span className="font-semibold text-slate-100">{coin.symbol.replace('USDT', '')}</span>
            <span className="font-mono text-slate-200">{formatPrice(currentPrice)}</span>

            {/* Live Position Marker */}
            {coin.has_open_position && (
              <span className="text-[10px] bg-laser-green/20 text-laser-green border border-laser-green/50 px-1 rounded font-bold animate-pulse">
                ● LIVE
              </span>
            )}

            {/* Live Confluence Pill */}
            {opp && !coin.has_open_position && (
              <>
                {opp.confluence_score >= 80 ? (
                  <span className="text-[10px] bg-laser-green/20 text-laser-green border border-laser-green/40 px-1 py-0.2 rounded font-bold flex items-center space-x-0.5">
                    <Flame size={10} className="text-laser-green" />
                    <span>{opp.confluence_score.toFixed(0)}</span>
                  </span>
                ) : opp.confluence_score >= 65 ? (
                  <span className="text-[10px] bg-laser-cyan/20 text-laser-cyan border border-laser-cyan/40 px-1 py-0.2 rounded font-bold flex items-center space-x-0.5">
                    <Zap size={10} className="text-laser-cyan" />
                    <span>{opp.confluence_score.toFixed(0)}</span>
                  </span>
                ) : opp.is_chop ? (
                  <span className="text-[9px] bg-slate-800 text-slate-400 border border-slate-700 px-1 py-0.2 rounded font-mono">
                    CHOP
                  </span>
                ) : null}

                {/* Volume Surge Badge */}
                {opp.volume_surge >= 1.5 && (
                  <span className="text-[9px] bg-amber-warn/20 text-amber-warn border border-amber-warn/40 px-1 py-0.2 rounded font-bold">
                    {opp.volume_surge.toFixed(1)}x Vol
                  </span>
                )}
              </>
            )}

            {/* Direction Arrow */}
            {coin.trend && coin.trend !== 'NEUTRAL' && (
              <span
                className={`text-[9px] font-bold px-1 rounded ${
                  coin.trend === 'BULLISH'
                    ? 'text-laser-green bg-laser-green/10'
                    : 'text-crimson-red bg-crimson-red/10'
                }`}
              >
                {coin.trend === 'BULLISH' ? '▲' : '▼'}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
};

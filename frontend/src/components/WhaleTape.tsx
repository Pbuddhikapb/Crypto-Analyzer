import React from 'react';
import { WhaleTrade } from '../types/market';
import { ArrowUpRight, ArrowDownRight, Anchor } from 'lucide-react';

interface WhaleTapeProps {
  trades: WhaleTrade[];
}

export const WhaleTape: React.FC<WhaleTapeProps> = ({ trades }) => {
  const formatTime = (ts: number): string => {
    const d = new Date(ts > 10000000000 ? ts : ts * 1000);
    return d.toTimeString().split(' ')[0];
  };

  const formatNotional = (n: number): string => {
    if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(2)}M`;
    if (n >= 1_000) return `$${(n / 1_000).toFixed(1)}k`;
    return `$${n.toFixed(0)}`;
  };

  const getBadgeStyle = (badge: string) => {
    switch (badge) {
      case 'WHALE':
        return 'bg-purple-500/20 text-purple-300 border-purple-500/50 shadow-[0_0_8px_rgba(168,85,247,0.4)]';
      case 'SHARK':
        return 'bg-amber-500/20 text-amber-300 border-amber-500/50';
      default:
        return 'bg-sky-500/20 text-sky-300 border-sky-500/40';
    }
  };

  return (
    <div className="flex-1 flex flex-col bg-dark-panel border-b border-dark-border min-h-0 select-none">
      <div className="px-3 py-1.5 bg-dark-card border-b border-dark-border flex items-center justify-between text-xs">
        <div className="flex items-center space-x-1.5 font-bold text-slate-200">
          <Anchor size={13} className="text-laser-cyan" />
          <span>FILTERED WHALE TAPE</span>
          <span className="text-[10px] text-slate-400 font-normal">(&ge; $50k)</span>
        </div>
        <span className="text-[10px] text-slate-400 font-mono">{trades.length} TRADES</span>
      </div>

      <div className="flex-1 overflow-y-auto divide-y divide-dark-border/40 text-[11px] font-mono">
        {trades.length === 0 ? (
          <div className="h-full flex items-center justify-center text-slate-500 text-xs py-8">
            Monitoring aggTrade stream for large orders...
          </div>
        ) : (
          trades.map((t, idx) => {
            const isBuy = t.side === 'BUY';
            return (
              <div
                key={`${t.timestamp}-${idx}`}
                className="px-3 py-1.5 hover:bg-dark-hover flex items-center justify-between transition"
              >
                <div className="flex items-center space-x-2">
                  <span className="text-slate-500 text-[10px]">{formatTime(t.timestamp)}</span>
                  <span
                    className={`text-[9px] font-bold px-1 py-0.5 rounded border ${getBadgeStyle(
                      t.badge
                    )}`}
                  >
                    {t.badge}
                  </span>
                  <span className="font-semibold text-slate-300">{t.symbol.replace('USDT', '')}</span>
                  <div
                    className={`flex items-center space-x-0.5 font-bold ${
                      isBuy ? 'text-laser-green' : 'text-crimson-red'
                    }`}
                  >
                    {isBuy ? <ArrowUpRight size={13} /> : <ArrowDownRight size={13} />}
                    <span>{t.side}</span>
                  </div>
                </div>

                <div className="flex items-center space-x-3 text-right">
                  <span className="text-slate-400">{t.quantity.toFixed(3)}</span>
                  <span className={`font-bold ${isBuy ? 'text-laser-green' : 'text-crimson-red'}`}>
                    {formatNotional(t.notional)}
                  </span>
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
};

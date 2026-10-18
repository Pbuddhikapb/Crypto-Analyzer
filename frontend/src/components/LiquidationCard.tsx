import React from 'react';
import { LiquidationEvent } from '../types/market';
import { Flame, AlertTriangle } from 'lucide-react';

interface LiquidationCardProps {
  liquidations: LiquidationEvent[];
  rollingVolume: number;
  isCascadeAlert: boolean;
}

export const LiquidationCard: React.FC<LiquidationCardProps> = ({
  liquidations,
  rollingVolume,
  isCascadeAlert,
}) => {
  const formatTime = (ts: number): string => {
    const d = new Date(ts > 10000000000 ? ts : ts * 1000);
    return d.toTimeString().split(' ')[0];
  };

  const formatNotional = (n: number): string => {
    if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(2)}M`;
    if (n >= 1_000) return `$${(n / 1_000).toFixed(1)}k`;
    return `$${n.toFixed(0)}`;
  };

  return (
    <div className="flex-1 flex flex-col bg-dark-panel min-h-0 select-none">
      {/* Header */}
      <div className="px-3 py-1.5 bg-dark-card border-b border-dark-border flex items-center justify-between text-xs">
        <div className="flex items-center space-x-1.5 font-bold text-slate-200">
          <Flame size={13} className="text-crimson-red" />
          <span>LIQUIDATION RADAR</span>
        </div>
        <div className="flex items-center space-x-1.5 font-mono text-[11px]">
          <span className="text-slate-400">60s VOL:</span>
          <span className={`font-bold ${isCascadeAlert ? 'text-crimson-red' : 'text-slate-200'}`}>
            {formatNotional(rollingVolume)}
          </span>
        </div>
      </div>

      {/* Cascade Alert Banner */}
      {isCascadeAlert && (
        <div className="bg-crimson-red/20 border-b border-crimson-red/60 px-3 py-1.5 flex items-center space-x-2 text-crimson-red text-xs font-bold cascade-pulse">
          <AlertTriangle size={14} className="animate-bounce" />
          <span>CASCADE ALERT: 60s LIQUIDATIONS &gt; $1M FLUSH!</span>
        </div>
      )}

      {/* Events List */}
      <div className="flex-1 overflow-y-auto divide-y divide-dark-border/40 text-[11px] font-mono">
        {liquidations.length === 0 ? (
          <div className="h-full flex items-center justify-center text-slate-500 text-xs py-8">
            Monitoring !forceOrder stream for liquidation flushes...
          </div>
        ) : (
          liquidations.map((liq, idx) => {
            const isLongLiq = liq.liquidated_position === 'LONG';
            return (
              <div
                key={`${liq.timestamp}-${idx}`}
                className="px-3 py-1.5 hover:bg-dark-hover flex items-center justify-between transition"
              >
                <div className="flex items-center space-x-2">
                  <span className="text-slate-500 text-[10px]">{formatTime(liq.timestamp)}</span>
                  <span className="font-semibold text-slate-300">{liq.symbol.replace('USDT', '')}</span>
                  <span
                    className={`text-[9px] font-bold px-1.5 py-0.2 rounded border ${
                      isLongLiq
                        ? 'bg-crimson-red/20 text-crimson-red border-crimson-red/40'
                        : 'bg-laser-green/20 text-laser-green border-laser-green/40'
                    }`}
                  >
                    {isLongLiq ? 'LONG FLUSH' : 'SHORT SQUEEZE'}
                  </span>
                </div>

                <div className="flex items-center space-x-2 text-right">
                  <span className="text-slate-400">${liq.price.toLocaleString()}</span>
                  <span className="font-bold text-slate-200">{formatNotional(liq.notional)}</span>
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
};

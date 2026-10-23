import React, { useMemo } from 'react';
import { Indicators, MicrostructureSummary, PortfolioSummary } from '../types/market';
import { Compass, Gauge, Clock, Percent } from 'lucide-react';

interface ConfluencePanelProps {
  indicators: Indicators | null;
  microstructure: MicrostructureSummary | null;
  portfolio: PortfolioSummary | null;
  onOpenCockpit?: () => void;
}

export const ConfluencePanel: React.FC<ConfluencePanelProps> = ({
  indicators,
  microstructure,
  portfolio,
  onOpenCockpit,
}) => {
  // Compute Unified Confluence Score (0 to 100)
  const { score, label, colorClass } = useMemo(() => {
    let pts = 50; // Neutral baseline

    if (indicators) {
      // Trend: +/- 15
      if (indicators.trend === 'BULLISH') pts += 15;
      else if (indicators.trend === 'BEARISH') pts -= 15;

      // Momentum / RSI: +/- 10
      if (indicators.rsi !== null) {
        if (indicators.rsi < 35) pts += 10; // Oversold recovery
        else if (indicators.rsi > 65) pts -= 10; // Overbought exhaustion
      }

      // FVGs: +/- 10
      if (indicators.has_bullish_fvg) pts += 10;
      if (indicators.has_bearish_fvg) pts -= 10;

      // Divergences: +/- 15
      if (indicators.has_bullish_divergence) pts += 15;
      if (indicators.has_bearish_divergence) pts -= 15;
    }

    if (microstructure) {
      // OI Regime: +/- 10
      if (microstructure.oi_regime === 'LONG_BUILDUP') pts += 10;
      else if (microstructure.oi_regime === 'SHORT_BUILDUP') pts -= 10;
      else if (microstructure.oi_regime === 'SHORT_SQUEEZE') pts += 5;
      else if (microstructure.oi_regime === 'LONG_LIQUIDATION') pts -= 5;
    }

    const finalScore = Math.max(0, Math.min(100, pts));
    let lbl = 'NEUTRAL';
    let cls = 'text-slate-300 border-slate-600 bg-slate-800/40';

    if (finalScore >= 75) {
      lbl = 'STRONG BULLISH';
      cls = 'text-laser-green border-laser-green/50 bg-laser-green/15';
    } else if (finalScore >= 56) {
      lbl = 'BULLISH';
      cls = 'text-laser-cyan border-laser-cyan/50 bg-laser-cyan/15';
    } else if (finalScore <= 25) {
      lbl = 'STRONG BEARISH';
      cls = 'text-crimson-red border-crimson-red/50 bg-crimson-red/15';
    } else if (finalScore <= 45) {
      lbl = 'BEARISH';
      cls = 'text-amber-warn border-amber-warn/50 bg-amber-warn/15';
    }

    return { score: finalScore, label: lbl, colorClass: cls };
  }, [indicators, microstructure]);

  const activeTrade = portfolio?.active_trades?.[0];

  return (
    <footer className="h-8 bg-dark-panel border-t border-dark-border px-2 flex items-center justify-between text-xs font-mono select-none overflow-x-auto no-scrollbar">
      {/* Confluence Score & Regime */}
      <div className="flex items-center space-x-4">
        {/* Confluence Badge */}
        <div className="flex items-center space-x-2">
          <div className="flex items-center space-x-1 text-slate-400">
            <Gauge size={13} className="text-laser-cyan" />
            <span className="font-semibold">CONFLUENCE:</span>
          </div>
          <div className={`flex items-center space-x-1.5 px-2 py-0.5 rounded border ${colorClass}`}>
            <span className="font-bold text-sm">{score}</span>
            <span className="text-[10px] font-bold tracking-wide">[{label}]</span>
          </div>
        </div>

        {/* OI Regime */}
        <div className="hidden sm:flex items-center space-x-1.5">
          <Compass size={13} className="text-purple-smc" />
          <span className="text-slate-400">OI REGIME:</span>
          <span className="px-1.5 py-0.5 rounded bg-dark-card border border-dark-border text-slate-200 font-bold">
            {microstructure?.oi_regime || 'NEUTRAL'}
          </span>
        </div>

        {/* Pricing Zone */}
        {indicators?.pricing_zone && (
          <div className="hidden lg:flex items-center space-x-1.5">
            <span className="text-slate-400">ZONE:</span>
            <span
              className={`px-1.5 py-0.5 rounded border text-[10px] font-bold ${
                indicators.pricing_zone === 'OTE'
                  ? 'bg-amber-warn/20 text-amber-warn border-amber-warn/50'
                  : indicators.pricing_zone.includes('DISCOUNT')
                  ? 'bg-laser-green/20 text-laser-green border-laser-green/50'
                  : 'bg-crimson-red/20 text-crimson-red border-crimson-red/50'
              }`}
            >
              {indicators.pricing_zone}
            </span>
          </div>
        )}

        {/* Order Block / Breaker Block Status */}
        {indicators?.order_block_confluence?.has_confluence && (
          <div className="hidden xl:flex items-center space-x-1.5">
            <span className="text-slate-400">BLOCK:</span>
            <span className="px-1.5 py-0.5 rounded border text-[10px] font-bold bg-laser-cyan/20 text-laser-cyan border-laser-cyan/50">
              {indicators.order_block_confluence.block_type?.replace('_', ' ') || 'ORDER BLOCK'}
            </span>
          </div>
        )}

        {/* Funding Rate & APR */}
        <div className="hidden md:flex items-center space-x-1.5">
          <Percent size={13} className="text-amber-warn" />
          <span className="text-slate-400">FUNDING APR:</span>
          <span className="text-amber-warn font-semibold">
            {microstructure?.funding_apr_pct !== undefined
              ? `${microstructure.funding_apr_pct >= 0 ? '+' : ''}${microstructure.funding_apr_pct.toFixed(2)}%`
              : '0.00%'}
          </span>
          <span className="text-slate-500 flex items-center space-x-1">
            <Clock size={11} />
            <span>[{microstructure?.funding_countdown || '--:--:--'}]</span>
          </span>
        </div>
      </div>

      {/* Active Position / Portfolio Snapshot */}
      <div className="flex items-center space-x-3">
        {activeTrade ? (
          <button
            onClick={onOpenCockpit}
            className="flex items-center space-x-2 bg-dark-card hover:bg-dark-card/80 border border-dark-border hover:border-slate-500 px-2.5 py-0.5 rounded transition cursor-pointer"
            title="Click to view Active Trades Cockpit"
          >
            <span className="text-slate-400 font-medium">ACTIVE TRADE:</span>
            <span
              className={`font-bold px-1 rounded text-[10px] ${
                activeTrade.side === 'LONG'
                  ? 'bg-laser-green/20 text-laser-green border border-laser-green/30'
                  : 'bg-crimson-red/20 text-crimson-red border border-crimson-red/30'
              }`}
            >
              {activeTrade.side} {activeTrade.symbol}
            </span>
            <span className="text-slate-300">@ ${activeTrade.entry_price.toLocaleString()}</span>
            <span
              className={`font-bold ${
                (activeTrade.unrealized_pnl_pct || 0) >= 0 ? 'text-laser-green' : 'text-crimson-red'
              }`}
            >
              {(activeTrade.unrealized_pnl_pct || 0) >= 0 ? '+' : ''}
              {activeTrade.unrealized_pnl_pct?.toFixed(2)}%
            </span>
            {activeTrade.unrealized_pnl_usd !== undefined && (
              <span
                className={`text-[10px] font-bold ${
                  activeTrade.unrealized_pnl_usd >= 0 ? 'text-laser-green' : 'text-crimson-red'
                }`}
              >
                ({activeTrade.unrealized_pnl_usd >= 0 ? '+' : ''}${activeTrade.unrealized_pnl_usd.toFixed(2)})
              </span>
            )}
          </button>
        ) : (
          <div className="text-slate-500 text-[11px]">
            <span>NO ACTIVE POSITIONS (SCANNING SMC + MOMENTUM)</span>
          </div>
        )}
      </div>
    </footer>
  );
};

import React, { useMemo } from 'react';
import { Indicators, MicrostructureSummary, PortfolioSummary, ScannerOpportunity } from '../types/market';
import { Compass, Gauge, Clock, Percent, AlertTriangle } from 'lucide-react';
import { GLOSSARY_TOOLTIPS } from '../utils/tooltips';
import { computeVersionInfo } from '../utils/version';

interface ConfluencePanelProps {
  indicators: Indicators | null;
  microstructure: MicrostructureSummary | null;
  portfolio: PortfolioSummary | null;
  opportunity?: ScannerOpportunity | null;
  apiGitCommit?: string;
  onOpenCockpit?: () => void;
}

export const ConfluencePanel: React.FC<ConfluencePanelProps> = ({
  indicators,
  microstructure,
  portfolio,
  opportunity,
  apiGitCommit,
  onOpenCockpit,
}) => {
  const version = useMemo(() => {
    const ui = typeof __BUILD_COMMIT__ !== 'undefined' ? __BUILD_COMMIT__ : 'unknown';
    return computeVersionInfo(ui, apiGitCommit);
  }, [apiGitCommit]);
  // Use ONE unified confluence score (0 to 100) and display direction separately
  const { score, direction, scoreColorClass, directionColorClass } = useMemo(() => {
    // 1. Authoritative score from scanner opportunity or fallback indicator model
    let val = opportunity ? opportunity.confluence_score : 50.0;

    if (!opportunity && indicators) {
      // Consistent fallback model matching server logic
      let pts = 50.0;
      if (indicators.trend === 'BULLISH') pts += 15.0;
      else if (indicators.trend === 'BEARISH') pts += 15.0; // Confluence magnitude

      if (indicators.has_bullish_fvg || indicators.has_bearish_fvg) pts += 15.0;
      if (indicators.pricing_zone && indicators.pricing_zone !== 'EQUILIBRIUM') pts += 10.0;
      if (indicators.adx_14 && indicators.adx_14 >= 25.0) pts += 10.0;
      if (indicators.has_bullish_divergence || indicators.has_bearish_divergence) pts += 10.0;
      val = Math.max(5.0, Math.min(100.0, pts));
    }

    const dir = opportunity?.trend || indicators?.trend || 'NEUTRAL';

    let scoreCls = 'text-slate-300 border-slate-600 bg-slate-800/40';
    if (val >= 80) {
      scoreCls = 'text-laser-green border-laser-green/50 bg-laser-green/15';
    } else if (val >= 65) {
      scoreCls = 'text-laser-cyan border-laser-cyan/50 bg-laser-cyan/15';
    } else if (val >= 50) {
      scoreCls = 'text-amber-warn border-amber-warn/50 bg-amber-warn/15';
    }

    let dirCls = 'text-slate-400 border-dark-border bg-dark-card';
    if (dir === 'BULLISH') {
      dirCls = 'text-laser-green border-laser-green/40 bg-laser-green/10';
    } else if (dir === 'BEARISH') {
      dirCls = 'text-crimson-red border-crimson-red/40 bg-crimson-red/10';
    }

    return {
      score: Math.round(val),
      direction: dir,
      scoreColorClass: scoreCls,
      directionColorClass: dirCls,
    };
  }, [indicators, opportunity]);

  const activeTrade = portfolio?.active_trades?.[0];

  return (
    <footer className="h-8 bg-dark-panel border-t border-dark-border px-2 flex items-center justify-between text-xs font-mono select-none overflow-x-auto no-scrollbar">
      {/* Confluence Score & Regime */}
      <div className="flex items-center space-x-4">
        {/* Unified Confluence Badge with Separate Direction */}
        <div className="flex items-center space-x-2">
          <div className="flex items-center space-x-1 text-slate-400">
            <Gauge size={13} className="text-laser-cyan" />
            <span className="font-semibold">CONFLUENCE:</span>
          </div>
          <div className="flex items-center space-x-1.5">
            <div
              className={`flex items-center space-x-1 px-2 py-0.5 rounded border ${scoreColorClass}`}
              title="Unified Confluence Score (0-100 scale, identical to Radar)"
            >
              <span className="font-bold text-sm">{score}</span>
              <span className="text-[10px] text-slate-400">/100</span>
            </div>
            <div
              className={`px-1.5 py-0.5 rounded border text-[10px] font-bold tracking-wide ${directionColorClass}`}
              title={`Setup Direction: ${direction}`}
            >
              [{direction}]
            </div>
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
              title={indicators.pricing_zone === 'OTE' ? GLOSSARY_TOOLTIPS.OTE : undefined}
            >
              {indicators.pricing_zone}
            </span>
          </div>
        )}

        {/* Order Block / Breaker Block Status */}
        {indicators?.order_block_confluence?.has_confluence && (
          <div className="hidden xl:flex items-center space-x-1.5">
            <span className="text-slate-400">BLOCK:</span>
            <span
              className="px-1.5 py-0.5 rounded border text-[10px] font-bold bg-laser-cyan/20 text-laser-cyan border-laser-cyan/50"
              title={GLOSSARY_TOOLTIPS.OB}
            >
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

      {/* Active Position / Portfolio Snapshot & Version Status */}
      <div className="flex items-center space-x-3">
        {/* Version Status: UI abc1234 / API def5678 */}
        <div className="flex items-center font-mono text-[10px]">
          {version.isMismatch ? (
            <span
              className="text-crimson-red font-bold bg-crimson-red/15 border border-crimson-red/40 px-1.5 py-0.5 rounded flex items-center space-x-1"
              title="Frontend build commit does not match backend API commit"
            >
              <AlertTriangle size={11} />
              <span>{version.displayText}</span>
            </span>
          ) : (
            <span className="text-slate-500">
              {version.displayText}
            </span>
          )}
        </div>

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

import React, { useEffect, useState } from 'react';
import { ScannerOpportunity } from '../types/market';
import { fetchScannerOpportunities } from '../state/api';
import { Radar, RefreshCw, Zap, AlertCircle } from 'lucide-react';
import { formatPrice } from '../utils/formatters';
import { GLOSSARY_TOOLTIPS } from '../utils/tooltips';

interface OpportunityRadarProps {
  activeSymbol: string;
  onSelectSymbol: (symbol: string) => void;
}

export const OpportunityRadar: React.FC<OpportunityRadarProps> = ({
  activeSymbol,
  onSelectSymbol,
}) => {
  const [opportunities, setOpportunities] = useState<ScannerOpportunity[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(false);

  const loadData = () => {
    setIsLoading(true);
    fetchScannerOpportunities()
      .then((data) => setOpportunities(data))
      .catch(() => {})
      .finally(() => setIsLoading(false));
  };

  useEffect(() => {
    loadData();
    const interval = setInterval(loadData, 10000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="flex flex-col h-full bg-dark-panel text-slate-100 font-mono text-xs select-none p-3 space-y-2">
      {/* Header */}
      <div className="flex items-center justify-between pb-2 border-b border-dark-border">
        <div className="flex items-center space-x-2">
          <Radar size={15} className="text-laser-cyan animate-pulse" />
          <span className="font-bold text-slate-200 tracking-wider text-xs md:text-sm">SETUP SCANNER</span>
        </div>
        <button
          onClick={loadData}
          disabled={isLoading}
          className="text-slate-400 hover:text-slate-200 transition p-1"
          title="Refresh Opportunities"
        >
          <RefreshCw size={13} className={isLoading ? 'animate-spin' : ''} />
        </button>
      </div>

      <div className="text-[10px] text-slate-400">
        Pattern detections, not predictions. Check the evidence badge.
      </div>

      {/* List */}
      <div className="flex-1 overflow-y-auto space-y-2 no-scrollbar">
        {opportunities.length === 0 ? (
          <div className="text-center text-slate-500 py-8">
            <AlertCircle size={20} className="mx-auto mb-2 opacity-50" />
            <span>Scanning top Binance markets...</span>
          </div>
        ) : (
          opportunities.map((opp) => {
            const isSelected = opp.symbol === activeSymbol;
            const isLong = opp.trend === 'BULLISH';
            const setup = opp.trade_setup;
            const baseline = opp.historical_baseline;

            // Calibrate Confluence Score colors
            const scoreColor =
              opp.confluence_score >= 80
                ? 'bg-laser-green/20 text-laser-green border-laser-green/40'
                : opp.confluence_score >= 70
                ? 'bg-amber-warn/20 text-amber-warn border-amber-warn/40'
                : 'bg-slate-700/30 text-slate-400 border-slate-600/40';

            return (
              <div
                key={opp.symbol}
                onClick={() => onSelectSymbol(opp.symbol)}
                className={`p-1.5 rounded border transition cursor-pointer ${
                  isSelected
                    ? 'bg-sky-500/10 border-sky-400/50 shadow-md shadow-sky-500/10'
                    : 'bg-dark-card border-dark-border hover:bg-dark-card/80 hover:border-slate-600'
                }`}
              >
                {/* Header Row: Symbol, Direction, Evidence Badge, Confluence Score */}
                <div className="flex items-center justify-between">
                  <div className="flex items-center space-x-1.5">
                    <span className="font-bold text-[13px] text-slate-100">{opp.symbol}</span>
                    <span
                      className={`px-1 py-0.5 rounded text-[9px] font-bold ${
                        isLong
                          ? 'bg-laser-green/20 text-laser-green border border-laser-green/30'
                          : 'bg-crimson-red/20 text-crimson-red border border-crimson-red/30'
                      }`}
                    >
                      {opp.trend}
                    </span>
                    {opp.is_chop && (
                      <span className="px-1 py-0.5 rounded text-[8px] font-bold bg-amber-500/20 text-amber-300 border border-amber-500/30">
                        CHOP
                      </span>
                    )}
                  </div>

                  {/* Evidence badge BEFORE score, equally prominent */}
                  <div className="flex items-center space-x-1.5">
                    {baseline ? (
                      <span
                        className={`px-1.5 py-0.5 rounded border text-[11px] font-bold ${
                          baseline.status === 'UNTESTED'
                            ? 'bg-slate-800/80 text-slate-400 border-slate-600/40'
                            : baseline.status === 'NO VALIDATED EDGE'
                            ? 'bg-crimson-red/20 text-crimson-red border-crimson-red/50 shadow-sm'
                            : 'bg-emerald-500/20 text-emerald-400 border-emerald-500/50'
                        }`}
                        title={
                          baseline.status === 'UNTESTED'
                            ? `${baseline.pattern}: UNTESTED (No historical evidence)`
                            : `${baseline.pattern}: ${baseline.sample_trades ?? 0} trades | PF ${baseline.profit_factor ?? 'N/A'} | WR ${baseline.win_rate ?? 'N/A'}% | ${baseline.status}`
                        }
                      >
                        {baseline.status === 'NO VALIDATED EDGE' ? 'NO VALIDATED EDGE' : baseline.status}
                      </span>
                    ) : (
                      <span className="px-1.5 py-0.5 rounded border text-[11px] font-bold bg-slate-800/80 text-slate-400 border-slate-600/40">
                        UNTESTED
                      </span>
                    )}

                    <div
                      className={`flex items-center space-x-1 px-1.5 py-0.5 rounded border text-[11px] font-bold ${scoreColor}`}
                      title="Unified Confluence Score (0-100 scale, identical across Terminal)"
                    >
                      <Zap size={11} />
                      <span>{opp.confluence_score}/100</span>
                    </div>
                  </div>
                </div>

                {/* Inflection Pattern and Baseline Detail */}
                <div className="mt-1.5 flex flex-wrap items-center gap-1">
                  {opp.inflection_pattern && (
                    <span
                      className="px-1.5 py-0.5 rounded text-[9px] font-semibold bg-sky-500/15 text-sky-300 border border-sky-500/30"
                      title={
                        opp.inflection_pattern.includes('FVG')
                          ? GLOSSARY_TOOLTIPS.FVG
                          : opp.inflection_pattern.includes('OTE')
                          ? GLOSSARY_TOOLTIPS.OTE
                          : undefined
                      }
                    >
                      {opp.inflection_pattern}
                    </span>
                  )}
                  {baseline && baseline.status !== 'UNTESTED' && (
                    <span
                      className="px-1.5 py-0.5 rounded text-[9px] font-mono border bg-dark-bg/80 text-slate-400 border-dark-border"
                      title={GLOSSARY_TOOLTIPS.PF}
                    >
                      PF {baseline.profit_factor ?? 'N/A'} | WR {baseline.win_rate ?? 'N/A'}%
                    </span>
                  )}
                  {baseline?.run_timestamp && (
                    <span className="text-[8px] text-slate-500 font-mono">
                      stats from {new Date(baseline.run_timestamp).toLocaleDateString()}
                    </span>
                  )}
                </div>

                {/* Metrics Grid */}
                <div className="grid grid-cols-3 gap-1 mt-2 text-[10px] text-slate-400 pt-1.5 border-t border-dark-border/40">
                  <div>
                    <span className="block text-slate-500 text-[9px]">PRICE</span>
                    <span className="font-semibold text-slate-200">
                      ${formatPrice(opp.current_price, opp.current_price)}
                    </span>
                  </div>
                  <div>
                    <span className="block text-slate-500 text-[9px]">VOL SURGE</span>
                    <span
                      className={`font-semibold ${
                        opp.volume_surge >= 2.0 ? 'text-amber-warn' : 'text-slate-300'
                      }`}
                    >
                      {opp.volume_surge}x
                    </span>
                  </div>
                  <div>
                    <span className="block text-slate-500 text-[9px]">ADX</span>
                    <span
                      className={`font-semibold ${
                        opp.adx_14 >= 25 ? 'text-laser-green' : 'text-slate-400'
                      }`}
                    >
                      {opp.adx_14.toFixed(1)}
                    </span>
                  </div>
                </div>

                {/* Setup Bracket Preview */}
                {setup && (
                  <div className="mt-2 text-[10px] flex items-center justify-between bg-dark-bg/60 px-2 py-1 rounded border border-dark-border/60">
                    <span className="text-amber-warn font-semibold">
                      Limit: ${formatPrice(setup.limit_entry, opp.current_price)}
                    </span>
                    <div className="flex items-center space-x-1.5">
                      <span className="text-laser-green" title={GLOSSARY_TOOLTIPS.R}>
                        TP1: +0.75R
                      </span>
                      <span className="text-slate-500">|</span>
                      <span className="text-emerald-400 font-bold" title={GLOSSARY_TOOLTIPS.RR}>
                        1:{setup.rr_ratio} RR
                      </span>
                    </div>
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>
    </div>
  );
};

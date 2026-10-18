import React, { useEffect, useState } from 'react';
import { ScannerOpportunity } from '../types/market';
import { fetchScannerOpportunities } from '../state/api';
import { Radar, RefreshCw, Zap, AlertCircle } from 'lucide-react';

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
          <span className="font-bold text-slate-200 tracking-wider">TOP ALPHA SCANNER</span>
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
        Ranked by Confluence, Volume Surge, and SMC Gap Retest
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

            return (
              <div
                key={opp.symbol}
                onClick={() => onSelectSymbol(opp.symbol)}
                className={`p-2.5 rounded border transition cursor-pointer ${
                  isSelected
                    ? 'bg-sky-500/10 border-sky-400/50 shadow-md shadow-sky-500/10'
                    : 'bg-dark-card border-dark-border hover:bg-dark-card/80 hover:border-slate-600'
                }`}
              >
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
                  </div>

                  <div className="flex items-center space-x-1">
                    <Zap size={11} className="text-amber-warn" />
                    <span className="font-bold text-slate-200 text-[11px]">
                      {opp.confluence_score}/100
                    </span>
                  </div>
                </div>

                <div className="grid grid-cols-3 gap-1 mt-2 text-[10px] text-slate-400 pt-1.5 border-t border-dark-border/40">
                  <div>
                    <span className="block text-slate-500 text-[9px]">PRICE</span>
                    <span className="font-semibold text-slate-200">
                      ${opp.current_price.toLocaleString(undefined, { maximumFractionDigits: 4 })}
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

                {setup && (
                  <div className="mt-2 text-[10px] flex items-center justify-between bg-dark-bg/60 px-2 py-1 rounded border border-dark-border/60">
                    <span className="text-amber-warn font-semibold">
                      Limit Entry: ${setup.limit_entry.toLocaleString(undefined, { maximumFractionDigits: 4 })}
                    </span>
                    <span className="text-emerald-400 font-bold">1:{setup.rr_ratio} RR</span>
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

import React from 'react';
import { PortfolioSummary } from '../types/market';
import {
  ShieldAlert,
  DollarSign,
  PieChart,
  Layers,
  ArrowRight,
  ShieldCheck,
  AlertTriangle,
} from 'lucide-react';

interface ActiveTradeCockpitProps {
  portfolio: PortfolioSummary | null;
  latestPrices: Record<string, number>;
  onSelectSymbol: (symbol: string) => void;
  onSwitchToStaging: () => void;
}

export const ActiveTradeCockpit: React.FC<ActiveTradeCockpitProps> = ({
  portfolio,
  onSwitchToStaging,
}) => {
  const baseEquity = portfolio?.account_equity_usd ?? 10000.0;
  const maxPositions = portfolio?.max_open_positions ?? 3;
  const maxLeverage = 3.0;
  const riskPerTradePct = 1.0;
  const dollarRisk = baseEquity * (riskPerTradePct / 100.0);

  return (
    <div className="flex flex-col h-full bg-dark-panel text-slate-100 font-mono text-xs select-none p-3 space-y-3 overflow-y-auto no-scrollbar">
      {/* 1. Risk Sizing & Portfolio Capital HUD */}
      <div className="bg-dark-card border border-dark-border rounded-lg p-2.5 space-y-2.5">
        <div className="flex items-center justify-between pb-1.5 border-b border-dark-border/60">
          <div className="flex items-center space-x-1.5 text-slate-300">
            <PieChart size={14} className="text-laser-cyan" />
            <span className="font-bold tracking-wider text-[11px]">PORTFOLIO RISK SIZING</span>
          </div>
          <div className="flex items-center space-x-1.5">
            <span className="text-[10px] text-slate-400">EXECUTION:</span>
            <span className="text-[10px] font-bold px-1.5 py-0.5 rounded border bg-amber-500/20 text-amber-300 border-amber-400/40">
              EVIDENCE ONLY
            </span>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-2 text-[11px]">
          {/* Base Capital */}
          <div className="bg-dark-bg p-2 rounded border border-dark-border/80">
            <div className="text-[10px] text-slate-400 flex items-center space-x-1">
              <DollarSign size={11} className="text-slate-500" />
              <span>CAPITAL BASE</span>
            </div>
            <div className="text-[14px] font-bold text-slate-100 mt-0.5">
              ${baseEquity.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
            </div>
            <div className="text-[9px] text-slate-500 mt-0.5">Fixed 1% Risk Allocation</div>
          </div>

          {/* Dollar Risk per Trade */}
          <div className="bg-dark-bg p-2 rounded border border-dark-border/80">
            <div className="text-[10px] text-slate-400 flex items-center space-x-1">
              <ShieldCheck size={11} className="text-laser-cyan" />
              <span>RISK BUDGET (1R)</span>
            </div>
            <div className="text-[14px] font-bold text-laser-cyan mt-0.5">
              ${dollarRisk.toFixed(2)} USD
            </div>
            <div className="text-[9px] text-slate-500 mt-0.5">{riskPerTradePct}% of Portfolio Equity</div>
          </div>
        </div>

        {/* Risk Constraints Grid */}
        <div className="grid grid-cols-2 gap-2 text-[10px] pt-1 border-t border-dark-border/60">
          <div className="flex items-center justify-between text-slate-400">
            <span>MAX CONCURRENT SLOTS:</span>
            <span className="font-bold text-slate-200">0 / {maxPositions} USED</span>
          </div>
          <div className="flex items-center justify-between text-slate-400">
            <span>LEVERAGE CEILING:</span>
            <span className="font-bold text-slate-200">{maxLeverage.toFixed(2)}x MAX</span>
          </div>
        </div>
      </div>

      {/* 2. Paper Trading Disabled & Honest Status Banner */}
      <div className="flex-1 flex flex-col justify-between p-4 bg-dark-card/50 rounded-lg border border-dark-border/80 space-y-3">
        <div className="space-y-3">
          <div className="flex items-start space-x-3 p-3 rounded bg-amber-500/10 border border-amber-400/30 text-amber-300">
            <AlertTriangle size={18} className="shrink-0 mt-0.5 text-amber-400" />
            <div className="space-y-1">
              <div className="font-bold text-[12px] tracking-wide">PAPER TRADING DISABLED</div>
              <p className="text-[10px] text-slate-300 leading-relaxed font-sans">
                Automated paper trading is disabled. All codified strategy hypotheses failed empirical validation against matched random noise on historical development data (no hypothesis demonstrated positive net expectancy under institutional friction).
              </p>
            </div>
          </div>

          <div className="p-3 rounded bg-dark-bg/80 border border-dark-border/80 space-y-2">
            <div className="flex items-center space-x-1.5 text-slate-300 font-bold text-[11px]">
              <ShieldAlert size={13} className="text-crimson-red" />
              <span>TERMINAL STATUS: EVIDENCE-ONLY MODE</span>
            </div>
            <ul className="text-[10px] text-slate-400 space-y-1.5 list-disc list-inside">
              <li>Live Binance Spot & USD-M Futures market data streaming active.</li>
              <li>Microstructure order flow, funding, and liquidations monitored in real-time.</li>
              <li>Confluence radar reports empirical backtest evidence badges from <code className="text-slate-300">strategy_results.json</code>.</li>
              <li>Trade slips generate bracket geometry without automated execution.</li>
            </ul>
          </div>
        </div>

        <div className="pt-2 space-y-2">
          <button
            onClick={onSwitchToStaging}
            className="w-full py-2.5 rounded bg-sky-500/20 hover:bg-sky-500/30 text-sky-400 border border-sky-400/40 font-bold text-[11px] transition shadow-sm flex items-center justify-center space-x-2"
          >
            <Layers size={13} />
            <span>Open Trade Slip & Bracket Geometry</span>
            <ArrowRight size={13} />
          </button>
        </div>
      </div>
    </div>
  );
};

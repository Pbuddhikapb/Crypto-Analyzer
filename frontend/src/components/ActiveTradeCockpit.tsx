import React from 'react';
import { PortfolioSummary, ActiveTrade } from '../types/market';
import {
  ShieldCheck,
  TrendingUp,
  TrendingDown,
  Target,
  ExternalLink,
  DollarSign,
  PieChart,
  Layers,
  ArrowRight,
  Flame,
  Clock,
} from 'lucide-react';

interface ActiveTradeCockpitProps {
  portfolio: PortfolioSummary | null;
  latestPrices: Record<string, number>;
  onSelectSymbol: (symbol: string) => void;
  onSwitchToStaging: () => void;
}

export const ActiveTradeCockpit: React.FC<ActiveTradeCockpitProps> = ({
  portfolio,
  latestPrices,
  onSelectSymbol,
  onSwitchToStaging,
}) => {
  const activeTrades = portfolio?.active_trades || [];
  const baseEquity = portfolio?.account_equity_usd ?? 10000.0;
  const totalUnrealizedUsd = portfolio?.total_unrealized_pnl_usd ?? 0.0;
  const currentEquity = portfolio?.current_equity_usd ?? (baseEquity + totalUnrealizedUsd);
  const totalUnrealizedPct = (totalUnrealizedUsd / baseEquity) * 100.0;
  const activeCount = portfolio?.active_positions_count ?? activeTrades.length;
  const maxPositions = portfolio?.max_open_positions ?? 3;
  const portfolioLeverage = portfolio?.portfolio_leverage ?? 0.0;

  return (
    <div className="flex flex-col h-full bg-dark-panel text-slate-100 font-mono text-xs select-none p-3 space-y-3 overflow-y-auto no-scrollbar">
      {/* 1. Portfolio Capital HUD */}
      <div className="bg-dark-card border border-dark-border rounded-lg p-2.5 space-y-2">
        <div className="flex items-center justify-between pb-1.5 border-b border-dark-border/60">
          <div className="flex items-center space-x-1.5 text-slate-300">
            <PieChart size={14} className="text-laser-cyan" />
            <span className="font-bold tracking-wider text-[11px]">PORTFOLIO COCKPIT</span>
          </div>
          <div className="flex items-center space-x-1.5">
            <span className="text-[10px] text-slate-400">SLOTS:</span>
            <span
              className={`text-[10px] font-bold px-1.5 py-0.5 rounded border ${
                activeCount > 0
                  ? 'bg-laser-green/20 text-laser-green border-laser-green/40'
                  : 'bg-slate-800 text-slate-400 border-slate-700'
              }`}
            >
              {activeCount} / {maxPositions} USED
            </span>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-2 text-[11px]">
          {/* Total Equity */}
          <div className="bg-dark-bg p-2 rounded border border-dark-border/80">
            <div className="text-[10px] text-slate-400 flex items-center space-x-1">
              <DollarSign size={11} className="text-slate-500" />
              <span>CURRENT EQUITY</span>
            </div>
            <div className="text-[14px] font-bold text-slate-100 mt-0.5">
              ${currentEquity.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
            </div>
          </div>

          {/* Unrealized PnL */}
          <div className="bg-dark-bg p-2 rounded border border-dark-border/80">
            <div className="text-[10px] text-slate-400 flex items-center space-x-1">
              <TrendingUp size={11} className={totalUnrealizedUsd >= 0 ? 'text-laser-green' : 'text-crimson-red'} />
              <span>UNREALIZED PnL</span>
            </div>
            <div
              className={`text-[14px] font-bold mt-0.5 ${
                totalUnrealizedUsd >= 0 ? 'text-laser-green' : 'text-crimson-red'
              }`}
            >
              {totalUnrealizedUsd >= 0 ? '+' : ''}${totalUnrealizedUsd.toFixed(2)}
              <span className="text-[10px] ml-1 opacity-80">
                ({totalUnrealizedPct >= 0 ? '+' : ''}{totalUnrealizedPct.toFixed(2)}%)
              </span>
            </div>
          </div>
        </div>

        {/* Portfolio Leverage Bar */}
        <div className="pt-1 flex items-center justify-between text-[10px] text-slate-400">
          <span>PORTFOLIO LEVERAGE:</span>
          <span className="font-bold text-slate-200">
            {portfolioLeverage.toFixed(2)}x / 3.00x MAX
          </span>
        </div>
      </div>

      {/* 2. Active Positions List or Empty State */}
      {activeTrades.length === 0 ? (
        <div className="flex-1 flex flex-col items-center justify-center text-center p-6 bg-dark-card/50 rounded-lg border border-dark-border/60 border-dashed space-y-3">
          <div className="w-12 h-12 rounded-full bg-laser-cyan/10 border border-laser-cyan/30 flex items-center justify-center text-laser-cyan">
            <Layers size={22} className="opacity-80" />
          </div>
          <div>
            <div className="font-bold text-[13px] text-slate-200">No Positions Active</div>
            <div className="text-[11px] text-slate-400 max-w-[220px] mt-1 leading-snug">
              System is continuously scanning for high-confluence institutional setups.
            </div>
          </div>
          <button
            onClick={onSwitchToStaging}
            className="flex items-center space-x-1.5 px-3 py-1.5 rounded bg-sky-500/20 hover:bg-sky-500/30 text-sky-400 border border-sky-400/40 font-bold text-[11px] transition shadow-sm"
          >
            <span>Stage Next Trade Slip</span>
            <ArrowRight size={13} />
          </button>
        </div>
      ) : (
        <div className="space-y-3">
          {activeTrades.map((trade: ActiveTrade) => {
            const isLong = trade.side === 'LONG';
            const currentPrice = latestPrices[trade.symbol] ?? trade.current_price ?? trade.entry_price;
            const entry = trade.entry_price;
            const sl = trade.stop_loss;
            const initialSl = trade.initial_stop_loss || sl;
            const tp1 = trade.tp1_price || (isLong ? entry + (entry - initialSl) * 0.8 : entry - (initialSl - entry) * 0.8);
            const tp2 = trade.take_profit;
            const riskDist = Math.max(0.00001, Math.abs(entry - initialSl));
            const pnlUsd = trade.unrealized_pnl_usd ?? 0.0;
            const pnlPct = trade.unrealized_pnl_pct ?? 0.0;
            const rMultiple = trade.current_r_multiple ?? (isLong ? (currentPrice - entry) / riskDist : (entry - currentPrice) / riskDist);
            const barsHeld = trade.bars_held ?? 0;
            const maxBars = trade.max_stagnancy_bars ?? 12;
            const stagnancyPct = Math.min(100, (barsHeld / maxBars) * 100);

            // Compute Bracket Gauge percent (0% at SL, ~35% at Entry, ~65% at TP1, 100% at TP2)
            const span = Math.abs(tp2 - sl);
            const progressRaw = isLong ? (currentPrice - sl) / span : (sl - currentPrice) / span;
            const progressClamped = Math.max(0, Math.min(1, progressRaw)) * 100;

            return (
              <div
                key={`${trade.symbol}-${trade.timestamp}`}
                className="bg-dark-card border border-dark-border rounded-lg p-3 space-y-2.5 shadow-md hover:border-slate-600 transition"
              >
                {/* Position Header */}
                <div className="flex items-center justify-between pb-2 border-b border-dark-border/60">
                  <div className="flex items-center space-x-2">
                    <span className="font-bold text-[14px] text-slate-100">{trade.symbol}</span>
                    <span
                      className={`flex items-center space-x-1 px-1.5 py-0.5 rounded text-[10px] font-bold ${
                        isLong
                          ? 'bg-laser-green/20 text-laser-green border border-laser-green/40'
                          : 'bg-crimson-red/20 text-crimson-red border border-crimson-red/40'
                      }`}
                    >
                      {isLong ? <TrendingUp size={11} /> : <TrendingDown size={11} />}
                      <span>{trade.side}</span>
                    </span>
                    {trade.leverage && (
                      <span className="text-[10px] text-slate-400 bg-slate-800 px-1.5 py-0.5 rounded border border-slate-700">
                        {trade.leverage.toFixed(1)}x
                      </span>
                    )}
                  </div>

                  <button
                    onClick={() => onSelectSymbol(trade.symbol)}
                    className="flex items-center space-x-1 text-[11px] text-sky-400 hover:text-sky-300 font-bold transition px-1.5 py-0.5 rounded bg-sky-500/10 border border-sky-400/30"
                    title="Focus Chart on this Symbol"
                  >
                    <span>Chart</span>
                    <ExternalLink size={11} />
                  </button>
                </div>

                {/* Sizing & Entry Summary */}
                <div className="grid grid-cols-2 gap-2 text-[10px] text-slate-400">
                  <div>
                    <span>ENTRY: </span>
                    <span className="font-bold text-slate-200">${entry.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 4 })}</span>
                  </div>
                  <div className="text-right">
                    <span>MARK: </span>
                    <span className="font-bold text-slate-100">${currentPrice.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 4 })}</span>
                  </div>
                  <div>
                    <span>SIZE: </span>
                    <span className="font-bold text-slate-300">{trade.quantity ? trade.quantity.toFixed(3) : '--'} {trade.symbol.replace('USDT', '')}</span>
                  </div>
                  <div className="text-right">
                    <span>NOTIONAL: </span>
                    <span className="font-bold text-slate-300">${trade.notional_usd ? trade.notional_usd.toLocaleString(undefined, { maximumFractionDigits: 0 }) : '--'}</span>
                  </div>
                </div>

                {/* Live PnL Spotlight */}
                <div className="bg-dark-bg p-2 rounded border border-dark-border/80 flex items-center justify-between">
                  <div>
                    <div className="text-[10px] text-slate-400 font-semibold">LIVE RETURN</div>
                    <div
                      className={`text-[15px] font-bold ${
                        pnlUsd >= 0 ? 'text-laser-green' : 'text-crimson-red'
                      }`}
                    >
                      {pnlUsd >= 0 ? '+' : ''}${pnlUsd.toFixed(2)}
                      <span className="text-[11px] ml-1.5 font-normal">
                        ({pnlPct >= 0 ? '+' : ''}{pnlPct.toFixed(2)}%)
                      </span>
                    </div>
                  </div>

                  <div className="text-right">
                    <div className="text-[10px] text-slate-400 font-semibold">R-MULTIPLE</div>
                    <div
                      className={`text-[15px] font-bold ${
                        rMultiple >= 0 ? 'text-emerald-400' : 'text-crimson-red'
                      }`}
                    >
                      {rMultiple >= 0 ? '+' : ''}{rMultiple.toFixed(2)}R
                    </div>
                  </div>
                </div>

                {/* Visual R-Bracket Progress Gauge */}
                <div className="space-y-1 pt-1">
                  <div className="flex items-center justify-between text-[10px] text-slate-400">
                    <div className="flex items-center space-x-1">
                      <Target size={11} className="text-crimson-red" />
                      <span>SL (${sl.toLocaleString(undefined, { maximumFractionDigits: 2 })})</span>
                    </div>
                    {trade.be_armed && (
                      <div className="flex items-center space-x-0.5 text-laser-green font-bold bg-laser-green/10 px-1 rounded border border-laser-green/30 text-[9px]">
                        <ShieldCheck size={10} />
                        <span>BE ARMED</span>
                      </div>
                    )}
                    <div className="flex items-center space-x-1 text-emerald-400">
                      <span>TP2 (${tp2.toLocaleString(undefined, { maximumFractionDigits: 2 })})</span>
                      <Flame size={11} />
                    </div>
                  </div>

                  {/* Progress Track */}
                  <div className="relative w-full h-2.5 bg-dark-bg rounded-full border border-dark-border/80 overflow-hidden">
                    {/* Visual markers */}
                    <div
                      className="absolute top-0 bottom-0 left-0 bg-gradient-to-r from-crimson-red/60 via-amber-warn/60 to-laser-green transition-all duration-300"
                      style={{ width: `${progressClamped}%` }}
                    />
                    {/* Entry midline indicator at ~30% */}
                    <div
                      className="absolute top-0 bottom-0 w-0.5 bg-slate-300 opacity-60"
                      style={{ left: '30%' }}
                      title="Entry (0.0R)"
                    />
                  </div>

                  <div className="flex items-center justify-between text-[9px] text-slate-500 pt-0.5">
                    <span>-1.0R</span>
                    <span className="text-slate-400 font-semibold">0.0R (Entry)</span>
                    <span className={trade.tp1_hit ? 'text-laser-green font-bold' : ''}>
                      +0.8R (${tp1.toLocaleString(undefined, { maximumFractionDigits: 2 })}) {trade.tp1_hit ? '✓' : ''}
                    </span>
                    <span>+{((tp2 - entry) / riskDist).toFixed(1)}R</span>
                  </div>
                </div>

                {/* Kinetic Stagnancy Bar Countdown */}
                <div className="bg-dark-bg/60 p-2 rounded border border-dark-border/60 space-y-1">
                  <div className="flex items-center justify-between text-[10px]">
                    <div className="flex items-center space-x-1 text-slate-400">
                      <Clock size={11} className="text-sky-400" />
                      <span>KINETIC STAGNANCY:</span>
                    </div>
                    <span className="font-bold text-slate-200">
                      {barsHeld} / {maxBars} BARS HELD
                    </span>
                  </div>

                  <div className="w-full h-1.5 bg-dark-panel rounded-full overflow-hidden border border-dark-border/40">
                    <div
                      className={`h-full transition-all duration-300 ${
                        stagnancyPct > 80
                          ? 'bg-crimson-red'
                          : stagnancyPct > 50
                          ? 'bg-amber-warn'
                          : 'bg-sky-400'
                      }`}
                      style={{ width: `${stagnancyPct}%` }}
                    />
                  </div>

                  <div className="text-[9px] text-slate-500 flex items-center justify-between">
                    <span>{Math.max(0, maxBars - barsHeld)} bars before time-stop liquidation</span>
                    {barsHeld >= maxBars && (
                      <span className="text-crimson-red font-bold">STAGNANCY EXIT DUE</span>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};

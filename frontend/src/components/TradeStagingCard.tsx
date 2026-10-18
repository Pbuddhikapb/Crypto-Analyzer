import React, { useState, useMemo } from 'react';
import { TradeSetup, Indicators, ScannerOpportunity } from '../types/market';
import { ConfirmationChecklist } from './ConfirmationChecklist';
import { Calculator, Copy, Check, ShieldAlert, Target, TrendingUp, TrendingDown } from 'lucide-react';

interface TradeStagingCardProps {
  symbol: string;
  currentPrice?: number | null;
  tradeSetup?: TradeSetup | null;
  macroBias?: string;
  indicators?: Indicators | null;
  opportunity?: ScannerOpportunity | null;
}

export const TradeStagingCard: React.FC<TradeStagingCardProps> = ({
  symbol,
  currentPrice,
  tradeSetup,
  macroBias = 'NEUTRAL',
  indicators,
  opportunity,
}) => {
  const [dollarRisk, setDollarRisk] = useState<number>(100);
  const [leverage, setLeverage] = useState<number>(3);
  const [copied, setCopied] = useState<boolean>(false);

  // If no tradeSetup provided, use fallback based on currentPrice
  const setup = useMemo(() => {
    if (tradeSetup) return tradeSetup;
    const p = currentPrice || 100;
    const isLong = macroBias === 'BULLISH';
    const riskDist = p * 0.01;
    return {
      side: (isLong ? 'LONG' : 'SHORT') as 'LONG' | 'SHORT',
      limit_entry: p,
      stop_loss: isLong ? p - riskDist : p + riskDist,
      risk_dist: riskDist,
      risk_pct: 1.0,
      tp1: isLong ? p + (1.5 * riskDist) : p - (1.5 * riskDist),
      tp2: isLong ? p + (2.75 * riskDist) : p - (2.75 * riskDist),
      rr_ratio: 2.75,
      macro_bias: macroBias,
      is_counter_trend: false,
      recommendation: '🟢 MACRO-ALIGNED (Full institutional size approved)',
    };
  }, [tradeSetup, currentPrice, macroBias]);

  // Sizing Calculations
  const calculations = useMemo(() => {
    const riskDist = Math.max(setup.risk_dist, 0.00001);
    const qtyCoins = dollarRisk / riskDist;
    const notionalUsd = qtyCoins * setup.limit_entry;
    const requiredMargin = notionalUsd / leverage;
    const tp1Profit = (Math.abs(setup.tp1 - setup.limit_entry) * qtyCoins);
    const tp2Profit = (Math.abs(setup.tp2 - setup.limit_entry) * qtyCoins);

    return {
      qtyCoins,
      notionalUsd,
      requiredMargin,
      tp1Profit,
      tp2Profit,
    };
  }, [setup, dollarRisk, leverage]);

  const handleCopy = () => {
    const slip = `[${symbol} ${setup.side} TRADE SETUP]
Limit Entry: $${setup.limit_entry.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 4 })} (50% CE)
Stop Loss: $${setup.stop_loss.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 4 })} (-${setup.risk_pct.toFixed(2)}%)
Target 1: $${setup.tp1.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 4 })} (+1.5R)
Target 2: $${setup.tp2.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 4 })} (+${setup.rr_ratio}R Runner)
Risk/Reward: 1:${setup.rr_ratio}
Position Size: ${calculations.qtyCoins.toLocaleString(undefined, { maximumFractionDigits: 2 })} ${symbol.replace('USDT', '')} ($${calculations.notionalUsd.toLocaleString(undefined, { maximumFractionDigits: 2 })} notional)
Risk: $${dollarRisk} | Margin Required: $${calculations.requiredMargin.toFixed(2)} @ ${leverage}x`;

    navigator.clipboard.writeText(slip);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const isLong = setup.side === 'LONG';

  return (
    <div className="flex flex-col h-full bg-dark-panel text-slate-100 font-mono text-xs select-none overflow-y-auto p-3 space-y-3">
      {/* Header Badge */}
      <div className="flex items-center justify-between pb-2 border-b border-dark-border">
        <div className="flex items-center space-x-2">
          <Calculator size={15} className="text-laser-cyan" />
          <span className="font-bold text-slate-200 tracking-wider">TRADE STAGING SLIP</span>
        </div>
        <div
          className={`flex items-center space-x-1 px-2 py-0.5 rounded font-bold text-[11px] ${
            isLong
              ? 'bg-laser-green/20 text-laser-green border border-laser-green/40'
              : 'bg-crimson-red/20 text-crimson-red border border-crimson-red/40'
          }`}
        >
          {isLong ? <TrendingUp size={12} /> : <TrendingDown size={12} />}
          <span>LIMIT {setup.side}</span>
        </div>
      </div>

      {/* 6-Pillar Institutional Confirmation Checklist */}
      <ConfirmationChecklist
        symbol={symbol}
        side={setup.side}
        indicators={indicators || null}
        opportunity={opportunity}
        currentPrice={currentPrice}
      />

      {/* Recommendation & Counter-Trend Alert */}
      <div
        className={`px-2.5 py-1.5 rounded border text-[11px] flex items-center space-x-2 ${
          setup.is_counter_trend
            ? 'bg-amber-warn/15 border-amber-warn/40 text-amber-warn'
            : 'bg-laser-green/10 border-laser-green/30 text-laser-green'
        }`}
      >
        <ShieldAlert size={14} className="shrink-0" />
        <span className="font-semibold leading-tight">{setup.recommendation}</span>
      </div>

      {/* Risk & Leverage Inputs */}
      <div className="grid grid-cols-2 gap-2 bg-dark-card p-2.5 rounded border border-dark-border">
        <div>
          <label className="text-[10px] text-slate-400 font-semibold block mb-1">
            DOLLAR RISK ($)
          </label>
          <div className="flex items-center bg-dark-bg border border-dark-border rounded px-2 py-1">
            <span className="text-slate-500 mr-1">$</span>
            <input
              type="number"
              value={dollarRisk}
              onChange={(e) => setDollarRisk(Math.max(1, Number(e.target.value)))}
              className="bg-transparent text-slate-100 w-full outline-none font-bold"
            />
          </div>
        </div>
        <div>
          <div className="flex items-center justify-between mb-1">
            <label className="text-[10px] text-slate-400 font-semibold block">LEVERAGE</label>
            <span className="text-[9px] text-slate-500 font-mono">Max 3.0x</span>
          </div>
          <div className="flex items-center space-x-1">
            {[1, 2, 3].map((lev) => (
              <button
                key={lev}
                onClick={() => setLeverage(lev)}
                className={`flex-1 py-1 rounded text-[11px] font-bold border transition ${
                  leverage === lev
                    ? 'bg-sky-500/20 text-sky-400 border-sky-400/50'
                    : 'bg-dark-bg text-slate-400 border-dark-border hover:bg-dark-card'
                }`}
              >
                {lev}x
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Precise Price Targets Grid */}
      <div className="space-y-1.5 bg-dark-card p-2.5 rounded border border-dark-border">
        {/* Limit Entry */}
        <div className="flex items-center justify-between py-1 border-b border-dark-border/40">
          <span className="text-slate-400 text-[11px]">POST-ONLY LIMIT ENTRY:</span>
          <span className="text-amber-warn font-bold text-[13px]">
            ${setup.limit_entry.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 4 })}
          </span>
        </div>

        {/* Structural Stop Loss */}
        <div className="flex items-center justify-between py-1 border-b border-dark-border/40">
          <div className="flex items-center space-x-1 text-slate-400 text-[11px]">
            <Target size={12} className="text-crimson-red" />
            <span>STOP LOSS:</span>
          </div>
          <span className="text-crimson-red font-bold text-[13px]">
            ${setup.stop_loss.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 4 })}{' '}
            <span className="text-[10px] text-slate-400">(-{setup.risk_pct.toFixed(2)}%)</span>
          </span>
        </div>

        {/* Target 1 */}
        <div className="flex items-center justify-between py-1 border-b border-dark-border/40">
          <span className="text-slate-400 text-[11px]">TARGET 1 (1.5R):</span>
          <span className="text-laser-green font-bold text-[13px]">
            ${setup.tp1.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 4 })}{' '}
            <span className="text-[10px] text-emerald-400">(+${calculations.tp1Profit.toFixed(0)})</span>
          </span>
        </div>

        {/* Target 2 Runner */}
        <div className="flex items-center justify-between py-1">
          <span className="text-slate-400 text-[11px]">TARGET 2 ({setup.rr_ratio}R RUNNER):</span>
          <span className="text-emerald-400 font-bold text-[13px]">
            ${setup.tp2.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 4 })}{' '}
            <span className="text-[10px] text-emerald-400">(+${calculations.tp2Profit.toFixed(0)})</span>
          </span>
        </div>
      </div>

      {/* Sizing & Execution Parameters */}
      <div className="bg-dark-bg p-2.5 rounded border border-dark-border/80 space-y-1.5 text-[11px]">
        <div className="flex items-center justify-between">
          <span className="text-slate-400">POSITION SIZE:</span>
          <span className="text-slate-200 font-bold">
            {calculations.qtyCoins.toLocaleString(undefined, { maximumFractionDigits: 2 })} {symbol.replace('USDT', '')}
          </span>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-slate-400">NOTIONAL VALUE:</span>
          <span className="text-slate-200 font-bold">
            ${calculations.notionalUsd.toLocaleString(undefined, { maximumFractionDigits: 2 })}
          </span>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-slate-400">REQUIRED MARGIN:</span>
          <span className="text-laser-cyan font-bold">
            ${calculations.requiredMargin.toFixed(2)} USD
          </span>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-slate-400">RISK / REWARD RATIO:</span>
          <span className="text-emerald-400 font-bold">1 : {setup.rr_ratio}</span>
        </div>
      </div>

      {/* Copy Order Slip Button */}
      <button
        onClick={handleCopy}
        className={`w-full py-2 rounded font-bold flex items-center justify-center space-x-2 transition ${
          copied
            ? 'bg-laser-green text-dark-bg'
            : 'bg-sky-500 hover:bg-sky-400 text-dark-bg shadow-lg shadow-sky-500/20'
        }`}
      >
        {copied ? (
          <>
            <Check size={14} />
            <span>ORDER SLIP COPIED!</span>
          </>
        ) : (
          <>
            <Copy size={14} />
            <span>COPY ORDER SLIP FOR BINANCE</span>
          </>
        )}
      </button>
    </div>
  );
};

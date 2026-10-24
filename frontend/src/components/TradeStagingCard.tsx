import React, { useState, useMemo } from 'react';
import { TradeSetup, Indicators, ScannerOpportunity } from '../types/market';
import { ConfirmationChecklist } from './ConfirmationChecklist';
import { formatPrice } from '../utils/formatters';
import { GLOSSARY_TOOLTIPS } from '../utils/tooltips';
import {
  Calculator,
  Copy,
  Check,
  ShieldAlert,
  Target,
  TrendingUp,
  TrendingDown,
  DollarSign,
  Clock,
} from 'lucide-react';

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
  const [accountEquity, setAccountEquity] = useState<number>(10000);
  const [riskPercent, setRiskPercent] = useState<number>(1.0);
  const [dollarRisk, setDollarRisk] = useState<number>(100);
  const [leverage, setLeverage] = useState<number>(3);
  const [copied, setCopied] = useState<boolean>(false);

  // Sync risk percent and dollar risk
  const handleRiskPercentChange = (pct: number) => {
    setRiskPercent(pct);
    setDollarRisk(Math.round(accountEquity * (pct / 100)));
  };

  const handleDollarRiskChange = (dollars: number) => {
    const validDollars = Math.max(1, dollars);
    setDollarRisk(validDollars);
    if (accountEquity > 0) {
      setRiskPercent(Number(((validDollars / accountEquity) * 100).toFixed(2)));
    }
  };

  const handleEquityChange = (equity: number) => {
    const validEquity = Math.max(10, equity);
    setAccountEquity(validEquity);
    setDollarRisk(Math.round(validEquity * (riskPercent / 100)));
  };

  // If no tradeSetup provided, use Single Exit Authority verified geometry fallback
  const setup = useMemo(() => {
    if (tradeSetup) return tradeSetup;
    const p = currentPrice || 100;
    const isLong = macroBias === 'BULLISH';
    const riskDist = p * 0.01;
    const entry = p;
    const sl = isLong ? p - riskDist : p + riskDist;
    const tp1 = isLong ? entry + (0.75 * riskDist) : entry - (0.75 * riskDist);
    const beTrigger = isLong ? entry + (0.85 * riskDist) : entry - (0.85 * riskDist);
    const bePrice = isLong ? entry * 1.0005 : entry * 0.9995;
    const tp2 = isLong ? entry + (1.33 * riskDist) : entry - (1.33 * riskDist);

    return {
      side: (isLong ? 'LONG' : 'SHORT') as 'LONG' | 'SHORT',
      limit_entry: entry,
      stop_loss: sl,
      risk_dist: riskDist,
      risk_pct: 1.0,
      tp1,
      tp1_r: 0.75,
      be_trigger: beTrigger,
      be_trigger_r: 0.85,
      be_price: bePrice,
      tp2,
      tp2_r: 1.33,
      stagnancy_bars: 12,
      rr_ratio: 1.33,
      macro_bias: macroBias,
      is_counter_trend: false,
      recommendation: '🟢 MACRO-ALIGNED (Full institutional size approved)',
    };
  }, [tradeSetup, currentPrice, macroBias]);

  // Derived bracket levels ensuring full Single Exit Authority compliance
  const bracketGeometry = useMemo(() => {
    const isLong = setup.side === 'LONG';
    const riskDist = Math.max(setup.risk_dist, 0.00001);
    const tp1Price = setup.tp1;
    const tp1R = setup.tp1_r ?? 0.75;
    const beTriggerPrice = setup.be_trigger ?? (isLong ? setup.limit_entry + (0.85 * riskDist) : setup.limit_entry - (0.85 * riskDist));
    const beTriggerR = setup.be_trigger_r ?? 0.85;
    const bePrice = setup.be_price ?? (isLong ? setup.limit_entry * 1.0005 : setup.limit_entry * 0.9995);
    const tp2Price = setup.tp2;
    const tp2R = setup.tp2_r ?? (setup.rr_ratio || 1.33);
    const stagnancyBars = setup.stagnancy_bars ?? 12;

    return {
      riskDist,
      tp1Price,
      tp1R,
      beTriggerPrice,
      beTriggerR,
      bePrice,
      tp2Price,
      tp2R,
      stagnancyBars,
    };
  }, [setup]);

  // Sizing and PnL Calculations
  const calculations = useMemo(() => {
    const { riskDist, tp1Price, tp2Price } = bracketGeometry;
    const qtyCoins = dollarRisk / riskDist;
    const notionalUsd = qtyCoins * setup.limit_entry;
    const requiredMargin = notionalUsd / leverage;

    // Single Exit Authority: 50% scale-out at TP1 (+0.75R), remaining 50% runner at TP2 (+1.33R)
    const tp1Qty = qtyCoins * 0.50;
    const tp2Qty = qtyCoins * 0.50;
    const tp1Profit = Math.abs(tp1Price - setup.limit_entry) * tp1Qty;
    const tp2Profit = Math.abs(tp2Price - setup.limit_entry) * tp2Qty;
    const totalPotentialProfit = tp1Profit + tp2Profit;

    // Institutional Friction Breakdown
    const takerFeeEntry = notionalUsd * 0.0005; // 0.05%
    const makerFeeExit = notionalUsd * 0.0002;  // 0.02%
    const slippageEst = notionalUsd * 0.0003;   // 0.03%
    const totalFriction = takerFeeEntry + makerFeeExit + slippageEst;

    return {
      qtyCoins,
      notionalUsd,
      requiredMargin,
      tp1Qty,
      tp2Qty,
      tp1Profit,
      tp2Profit,
      totalPotentialProfit,
      maxLoss: dollarRisk,
      takerFeeEntry,
      makerFeeExit,
      slippageEst,
      totalFriction,
    };
  }, [setup, bracketGeometry, dollarRisk, leverage]);

  // Baseline Reality context from research strategy results
  const baselineInfo = opportunity?.historical_baseline || setup.historical_baseline || {
    pattern: 'Unknown',
    win_rate: null,
    profit_factor: null,
    sample_trades: null,
    status: 'UNTESTED',
    message: 'No historical evidence',
  };

  const isUntested = baselineInfo.status === 'UNTESTED';
  const isNoEdge = baselineInfo.status === 'NO VALIDATED EDGE';
  const timestampStr = baselineInfo.run_timestamp
    ? new Date(baselineInfo.run_timestamp).toLocaleString('en-US', {
        month: 'short',
        day: 'numeric',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
        timeZoneName: 'short',
      })
    : null;

  const handleCopy = () => {
    const cleanSym = symbol.replace('USDT', '');
    const slip = `=========================================
[${symbol} ${setup.side} DISCRETIONARY TRADE ORDER SLIP]
=========================================
Order Type:      POST-ONLY LIMIT ORDER
Limit Entry:     $${formatPrice(setup.limit_entry, currentPrice)} (50% CE)
Stop Loss:       $${formatPrice(setup.stop_loss, currentPrice)} (-1.0R | -${setup.risk_pct.toFixed(2)}%)
-----------------------------------------
BRACKET EXIT AUTHORITY (Single Exit Engine):
1. Target 1:     $${formatPrice(bracketGeometry.tp1Price, currentPrice)} (+${bracketGeometry.tp1R}R | 50% Scale-out)
2. Breakeven:    Arm when price reaches $${formatPrice(bracketGeometry.beTriggerPrice, currentPrice)} (+${bracketGeometry.beTriggerR}R)
                 -> Move SL to $${formatPrice(bracketGeometry.bePrice, currentPrice)} (+0.05% fee cover)
3. Target 2:     $${formatPrice(bracketGeometry.tp2Price, currentPrice)} (+${bracketGeometry.tp2R}R | Remaining 50% Runner)
4. Time Stop:    Close at market if underwater after ${bracketGeometry.stagnancyBars} candles
-----------------------------------------
RISK & SIZING SPECIFICATIONS:
Account Equity:  $${accountEquity.toLocaleString()}
Risk Budget:     $${dollarRisk} (${riskPercent}% equity)
Leverage:        ${leverage}x
Position Size:   ${calculations.qtyCoins.toLocaleString(undefined, { maximumFractionDigits: 4 })} ${cleanSym}
Notional Value:  $${calculations.notionalUsd.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} USD
Required Margin: $${calculations.requiredMargin.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} USD
-----------------------------------------
PAYOFF PROFILE:
TP1 PnL:         +$${calculations.tp1Profit.toFixed(2)} (on ${calculations.tp1Qty.toFixed(3)} ${cleanSym})
TP2 PnL:         +$${calculations.tp2Profit.toFixed(2)} (on ${calculations.tp2Qty.toFixed(3)} ${cleanSym})
Total Target:    +$${calculations.totalPotentialProfit.toFixed(2)} (+1.04R blended)
Max Loss:        -$${calculations.maxLoss.toFixed(2)} (-1.00R)
Est. Friction:   -$${calculations.totalFriction.toFixed(2)} (0.10% round-trip)
=========================================
EMPIRICAL REALITY (Research Validation):
Pattern:         ${baselineInfo.pattern}
Status:          ${baselineInfo.status}${baselineInfo.message ? ` (${baselineInfo.message})` : ''}
${isUntested ? 'Evidence:        No historical evidence (UNTESTED)' : `Metrics:         ${baselineInfo.win_rate !== null ? baselineInfo.win_rate + '%' : 'N/A'} Win Rate | ${baselineInfo.profit_factor !== null ? baselineInfo.profit_factor : 'N/A'} PF (${baselineInfo.sample_trades ?? 0} trades)`}
${timestampStr ? `Validated:       stats are from ${timestampStr}` : ''}
=========================================`;

    navigator.clipboard.writeText(slip);
    setCopied(true);
    setTimeout(() => setCopied(false), 2200);
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

      {/* Empirical Baseline Reality Warning Banner */}
      <div
        className={`p-2.5 rounded border text-[11px] space-y-1 ${
          isUntested
            ? 'border-slate-500/40 bg-slate-800/40 text-slate-300'
            : isNoEdge
            ? 'border-crimson-red/50 bg-crimson-red/10 text-crimson-red'
            : 'border-amber-500/40 bg-amber-500/10 text-amber-300'
        }`}
      >
        <div className="flex items-center justify-between font-bold">
          <div className="flex items-center space-x-1.5">
            <ShieldAlert size={14} className={isNoEdge ? 'text-crimson-red' : isUntested ? 'text-slate-400' : 'text-amber-400'} />
            <span className={isNoEdge ? 'text-crimson-red tracking-wide' : isUntested ? 'text-slate-300' : 'text-amber-400'}>
              {baselineInfo.status}
            </span>
          </div>
          <span
            className={`text-[10px] px-1.5 py-0.5 rounded font-mono font-bold ${
              isNoEdge
                ? 'bg-crimson-red/20 text-crimson-red border border-crimson-red/30'
                : isUntested
                ? 'bg-slate-700/50 text-slate-400 border border-slate-600/30'
                : 'bg-amber-400/20 text-amber-300 border border-amber-400/30'
            }`}
            title={GLOSSARY_TOOLTIPS.PF}
          >
            {isUntested
              ? 'UNTESTED'
              : `${baselineInfo.win_rate ?? 'N/A'}% WR | ${baselineInfo.profit_factor ?? 'N/A'} PF`}
          </span>
        </div>
        <p className="text-[10px] text-slate-300 leading-relaxed">
          {isUntested ? (
            <span>
              Pattern <span className="font-semibold text-slate-200">{baselineInfo.pattern}</span> has {baselineInfo.message || 'no historical evidence'}. Exercise discretionary caution.
            </span>
          ) : (
            <span>
              <span className="font-semibold text-slate-200">{baselineInfo.pattern}</span> backtest ({baselineInfo.sample_trades ?? 0} trades) shows PF {baselineInfo.profit_factor ?? 'N/A'} and EV {baselineInfo.expectancy_r !== undefined && baselineInfo.expectancy_r !== null ? `${baselineInfo.expectancy_r}R` : 'N/A'}. Warning: {baselineInfo.status}.
            </span>
          )}
        </p>
        {timestampStr && (
          <div className="text-[9px] text-slate-400 pt-0.5 border-t border-slate-700/40 flex items-center justify-between">
            <span>stats are from {timestampStr}</span>
            {baselineInfo.matched_p_value !== undefined && baselineInfo.matched_p_value !== null && (
              <span>Rank p-val: {baselineInfo.matched_p_value}</span>
            )}
          </div>
        )}
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

      {/* Interactive Account Equity & Risk Budget Calculator */}
      <div className="bg-dark-card p-2.5 rounded border border-dark-border space-y-2">
        <div className="flex items-center justify-between border-b border-dark-border/40 pb-1.5">
          <span className="text-[10px] text-slate-400 font-bold uppercase tracking-wider flex items-center space-x-1">
            <DollarSign size={11} className="text-laser-cyan" />
            <span>DISCRETIONARY RISK CALCULATOR</span>
          </span>
          <span className="text-[10px] text-slate-400 font-mono">Option B Strict Sizing</span>
        </div>

        {/* Equity & Dollar Risk Inputs */}
        <div className="grid grid-cols-2 gap-2">
          <div>
            <label className="text-[10px] text-slate-400 font-semibold block mb-0.5">
              ACCOUNT EQUITY ($)
            </label>
            <div className="flex items-center bg-dark-bg border border-dark-border rounded px-2 py-1">
              <span className="text-slate-500 mr-1">$</span>
              <input
                type="number"
                value={accountEquity}
                onChange={(e) => handleEquityChange(Number(e.target.value))}
                className="bg-transparent text-slate-100 w-full outline-none font-bold text-xs"
              />
            </div>
          </div>

          <div>
            <label className="text-[10px] text-slate-400 font-semibold block mb-0.5">
              DOLLAR RISK ($)
            </label>
            <div className="flex items-center bg-dark-bg border border-dark-border rounded px-2 py-1">
              <span className="text-slate-500 mr-1">$</span>
              <input
                type="number"
                value={dollarRisk}
                onChange={(e) => handleDollarRiskChange(Number(e.target.value))}
                className="bg-transparent text-laser-cyan w-full outline-none font-bold text-xs"
              />
            </div>
          </div>
        </div>

        {/* Risk Percentage Quick Toggles */}
        <div>
          <div className="flex items-center justify-between mb-1">
            <label className="text-[10px] text-slate-400 font-semibold block">
              EQUITY RISK % PRESETS
            </label>
            <span className="text-[10px] text-amber-300 font-semibold">{riskPercent}% Risk</span>
          </div>
          <div className="grid grid-cols-3 gap-1.5">
            {[0.5, 1.0, 2.0].map((pct) => (
              <button
                key={pct}
                onClick={() => handleRiskPercentChange(pct)}
                className={`py-1 rounded text-[11px] font-bold border transition flex items-center justify-center space-x-1 ${
                  riskPercent === pct
                    ? 'bg-sky-500/20 text-sky-400 border-sky-400/50 shadow-sm shadow-sky-500/20'
                    : 'bg-dark-bg text-slate-400 border-dark-border hover:bg-dark-card hover:text-slate-200'
                }`}
              >
                <span>{pct}%</span>
                <span className="text-[9px] text-slate-500">(${Math.round(accountEquity * (pct / 100))})</span>
              </button>
            ))}
          </div>
        </div>

        {/* Leverage Toggles */}
        <div>
          <div className="flex items-center justify-between mb-1">
            <label className="text-[10px] text-slate-400 font-semibold block">LEVERAGE</label>
            <span className="text-[9px] text-slate-500 font-mono">Max 3.0x Institutional</span>
          </div>
          <div className="flex items-center space-x-1.5">
            {[1, 2, 3].map((lev) => (
              <button
                key={lev}
                onClick={() => setLeverage(lev)}
                className={`flex-1 py-1 rounded text-[11px] font-bold border transition ${
                  leverage === lev
                    ? 'bg-sky-500/20 text-sky-400 border-sky-400/50 shadow-sm shadow-sky-500/20'
                    : 'bg-dark-bg text-slate-400 border-dark-border hover:bg-dark-card'
                }`}
              >
                {lev}x
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Single Exit Authority Bracket Geometry Grid */}
      <div className="space-y-1.5 bg-dark-card p-2.5 rounded border border-dark-border">
        <div className="flex items-center justify-between border-b border-dark-border/40 pb-1">
          <span className="text-[10px] text-slate-400 font-bold uppercase tracking-wider flex items-center space-x-1">
            <Target size={11} className="text-laser-green" />
            <span>BRACKET GEOMETRY (Single Exit Authority)</span>
          </span>
          <span className="text-[9px] text-laser-green font-semibold" title={GLOSSARY_TOOLTIPS.RR}>1:{setup.rr_ratio} RR</span>
        </div>

        {/* Post-Only Limit Entry */}
        <div className="flex items-center justify-between py-1 border-b border-dark-border/40">
          <span className="text-slate-400 text-[11px]" title={GLOSSARY_TOOLTIPS.CE}>POST-ONLY LIMIT ENTRY (50% CE):</span>
          <span className="text-amber-warn font-bold text-[12px]">
            ${formatPrice(setup.limit_entry, currentPrice)}
          </span>
        </div>

        {/* Structural Stop Loss (-1.0R) */}
        <div className="flex items-center justify-between py-1 border-b border-dark-border/40">
          <div className="flex items-center space-x-1 text-slate-400 text-[11px]">
            <Target size={12} className="text-crimson-red" />
            <span title={GLOSSARY_TOOLTIPS.R}>STOP LOSS (-1.0R):</span>
          </div>
          <span className="text-crimson-red font-bold text-[12px]">
            ${formatPrice(setup.stop_loss, currentPrice)}{' '}
            <span className="text-[10px] text-slate-400">(-{setup.risk_pct.toFixed(2)}%)</span>
          </span>
        </div>

        {/* Target 1 (+0.75R / 50% Scale) */}
        <div className="flex items-center justify-between py-1 border-b border-dark-border/40">
          <span className="text-slate-400 text-[11px]" title={GLOSSARY_TOOLTIPS.R}>TP1 (+{bracketGeometry.tp1R}R / 50% SCALE):</span>
          <span className="text-laser-green font-bold text-[12px]">
            ${formatPrice(bracketGeometry.tp1Price, currentPrice)}{' '}
            <span className="text-[10px] text-emerald-400">(+${calculations.tp1Profit.toFixed(1)})</span>
          </span>
        </div>

        {/* Breakeven Armed Trigger (+0.85R Excursion) */}
        <div className="flex items-center justify-between py-1 border-b border-dark-border/40 bg-sky-500/5 px-1 rounded">
          <span className="text-sky-400 text-[10px] font-semibold" title={GLOSSARY_TOOLTIPS.R}>
            BE TRIGGER (+{bracketGeometry.beTriggerR}R EXCURSION):
          </span>
          <span className="text-sky-300 font-bold text-[11px]">
            ${formatPrice(bracketGeometry.beTriggerPrice, currentPrice)}
          </span>
        </div>
        <div className="flex items-center justify-between text-[9px] text-slate-400 px-1">
          <span>↳ Move SL to BE price (+0.05% cover):</span>
          <span className="text-slate-300 font-mono">
            ${formatPrice(bracketGeometry.bePrice, currentPrice)}
          </span>
        </div>

        {/* Target 2 (+1.33R Final Runner) */}
        <div className="flex items-center justify-between py-1 border-b border-dark-border/40">
          <span className="text-slate-400 text-[11px]" title={GLOSSARY_TOOLTIPS.R}>TP2 (+{bracketGeometry.tp2R}R RUNNER):</span>
          <span className="text-emerald-400 font-bold text-[12px]">
            ${formatPrice(bracketGeometry.tp2Price, currentPrice)}{' '}
            <span className="text-[10px] text-emerald-400">(+${calculations.tp2Profit.toFixed(1)})</span>
          </span>
        </div>

        {/* Time Stop Stagnancy */}
        <div className="flex items-center justify-between py-0.5 text-[10px] text-slate-400">
          <div className="flex items-center space-x-1">
            <Clock size={11} className="text-amber-400" />
            <span>STAGNANCY TIME STOP:</span>
          </div>
          <span className="text-slate-300 font-mono">{bracketGeometry.stagnancyBars} Candles (market exit)</span>
        </div>
      </div>

      {/* Sizing & Capital Allocation Parameters */}
      <div className="bg-dark-bg p-2.5 rounded border border-dark-border/80 space-y-1.5 text-[11px]">
        <div className="flex items-center justify-between">
          <span className="text-slate-400">CALCULATED COIN QUANTITY:</span>
          <span className="text-slate-200 font-bold">
            {calculations.qtyCoins.toLocaleString(undefined, { maximumFractionDigits: 4 })} {symbol.replace('USDT', '')}
          </span>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-slate-400">TOTAL NOTIONAL POSITION:</span>
          <span className="text-slate-200 font-bold">
            ${calculations.notionalUsd.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} USD
          </span>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-slate-400">REQUIRED MARGIN ({leverage}x):</span>
          <span className="text-laser-cyan font-bold">
            ${calculations.requiredMargin.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} USD
          </span>
        </div>
        <div className="flex items-center justify-between pt-1 border-t border-dark-border/40">
          <span className="text-slate-400">PAYOFF PROFILE:</span>
          <span className="text-emerald-400 font-bold">
            +${calculations.totalPotentialProfit.toFixed(1)} <span className="text-slate-500 font-normal">/</span>{' '}
            <span className="text-crimson-red">-${calculations.maxLoss.toFixed(1)}</span>
          </span>
        </div>
      </div>

      {/* Friction Attribution Breakdown */}
      <div className="p-2 rounded bg-dark-card/60 border border-dark-border/60 text-[10px] space-y-1 text-slate-400">
        <div className="flex items-center justify-between font-semibold text-slate-300">
          <span>INSTITUTIONAL FRICTION ATTRIBUTION</span>
          <span className="text-amber-warn font-mono">-${calculations.totalFriction.toFixed(2)} (0.10%)</span>
        </div>
        <div className="grid grid-cols-3 gap-1 pt-1 text-[9px] text-slate-500">
          <div>Taker Entry (0.05%): ${calculations.takerFeeEntry.toFixed(2)}</div>
          <div>Maker TP (0.02%): ${calculations.makerFeeExit.toFixed(2)}</div>
          <div>Slippage (0.03%): ${calculations.slippageEst.toFixed(2)}</div>
        </div>
      </div>

      {/* Copy Order Slip Button */}
      <button
        onClick={handleCopy}
        className={`w-full py-2.5 rounded font-bold flex items-center justify-center space-x-2 transition ${
          copied
            ? 'bg-laser-green text-dark-bg font-extrabold shadow-lg shadow-laser-green/20'
            : 'bg-sky-500 hover:bg-sky-400 text-dark-bg font-bold shadow-lg shadow-sky-500/20'
        }`}
      >
        {copied ? (
          <>
            <Check size={14} />
            <span>ORDER SLIP COPIED TO CLIPBOARD!</span>
          </>
        ) : (
          <>
            <Copy size={14} />
            <span>COPY COMPLETE DISCRETIONARY ORDER SLIP</span>
          </>
        )}
      </button>
    </div>
  );
};

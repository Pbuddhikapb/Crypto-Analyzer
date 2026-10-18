import React, { useMemo } from 'react';
import { Indicators, ScannerOpportunity } from '../types/market';
import {
  CheckCircle2,
  XCircle,
  Clock,
  ShieldCheck,
  ShieldAlert,
  Flame,
  Activity,
  Layers,
  BarChart3,
  Waves,
  Zap,
} from 'lucide-react';

interface ConfirmationChecklistProps {
  symbol: string;
  side: 'LONG' | 'SHORT';
  indicators: Indicators | null;
  opportunity?: ScannerOpportunity | null;
  currentPrice?: number | null;
}

interface PillarStatus {
  id: string;
  title: string;
  desc: string;
  status: 'PASS' | 'PENDING' | 'FAIL';
  badge: string;
  icon: React.ReactNode;
}

export const ConfirmationChecklist: React.FC<ConfirmationChecklistProps> = ({
  side,
  indicators,
  opportunity,
  currentPrice,
}) => {
  const analysis = useMemo(() => {
    const isLong = side === 'LONG';
    const trend = indicators?.trend || opportunity?.trend || 'NEUTRAL';
    const htfTrend = opportunity?.htf_trend || 'NEUTRAL';
    const adx = indicators?.adx_14 ?? opportunity?.adx_14 ?? 20.0;
    const isChop = indicators?.is_chop ?? opportunity?.is_chop ?? (adx < 24.0);
    const volSurge = opportunity?.volume_surge ?? 1.0;
    const rsi = indicators?.rsi ?? null;
    const hasBullFvg = indicators?.has_bullish_fvg ?? false;
    const hasBearFvg = indicators?.has_bearish_fvg ?? false;
    const hasBullDiv = indicators?.has_bullish_divergence ?? false;
    const hasBearDiv = indicators?.has_bearish_divergence ?? false;
    const obConfluence = indicators?.order_block_confluence?.has_confluence ?? false;
    const obType = indicators?.order_block_confluence?.block_type;

    // 1. Macro Trend Filter (EMA 50/200 + HTF 4h)
    let p1Status: 'PASS' | 'PENDING' | 'FAIL' = 'PENDING';
    let p1Badge = 'NEUTRAL';
    let p1Desc = 'EMAs Neutral';
    const isMacroAligned = (isLong && trend === 'BULLISH') || (!isLong && trend === 'BEARISH');
    const isHtfOpposing = (isLong && htfTrend === 'BEARISH') || (!isLong && htfTrend === 'BULLISH');

    if (isHtfOpposing) {
      p1Status = 'FAIL';
      p1Badge = 'OPPOSING 4H';
      p1Desc = `4h HTF is ${htfTrend}`;
    } else if (isMacroAligned) {
      p1Status = 'PASS';
      p1Badge = 'ALIGNED';
      p1Desc = `EMA 50/200 ${trend} • 4h ${htfTrend}`;
    } else {
      p1Status = 'PENDING';
      p1Badge = 'NO BIAS';
      p1Desc = `Waiting for ${side} alignment`;
    }

    // 2. ADX Dynamic Momentum & Chop Schmitt Trigger
    let p2Status: 'PASS' | 'PENDING' | 'FAIL' = 'PENDING';
    let p2Badge = `${adx.toFixed(1)} ADX`;
    let p2Desc = 'Moderate Trend';

    if (isChop || adx < 24.0) {
      p2Status = 'FAIL';
      p2Badge = 'CHOP FILTER';
      p2Desc = `ADX ${adx.toFixed(1)} < 24.0 (Chop Zone)`;
    } else if (adx >= 25.0) {
      p2Status = 'PASS';
      p2Badge = `ADX ${adx.toFixed(1)}`;
      p2Desc = 'Strong Trend Expansion (Trigger Active)';
    } else {
      p2Status = 'PENDING';
      p2Badge = `ADX ${adx.toFixed(1)}`;
      p2Desc = 'Transitional (24.0 - 25.0)';
    }

    // 3. SMC Fair Value Gap (FVG Retest / Mitigation)
    let p3Status: 'PASS' | 'PENDING' | 'FAIL' = 'PENDING';
    let p3Badge = 'NO FVG';
    let p3Desc = 'No active gap in range';

    const relevantFvg = isLong ? hasBullFvg : hasBearFvg;
    const activeBoxes = indicators?.active_fvg_boxes || [];
    const targetBoxType = isLong ? 'BULLISH' : 'BEARISH';
    const matchingBoxes = activeBoxes.filter((b) => b.type === targetBoxType);

    if (relevantFvg || matchingBoxes.length > 0) {
      p3Status = 'PASS';
      p3Badge = 'GAP ACTIVE';
      const targetBox = matchingBoxes[0];
      p3Desc = targetBox?.ce
        ? `50% CE Retest @ $${targetBox.ce.toFixed(2)}`
        : `Active ${side} FVG Mitigated`;
    } else {
      p3Status = 'PENDING';
      p3Badge = 'PENDING';
      p3Desc = `Awaiting ${side} FVG formation`;
    }

    // 4. Institutional Order Block / Breaker Block
    let p4Status: 'PASS' | 'PENDING' | 'FAIL' = 'PENDING';
    let p4Badge = 'UNTESTED';
    let p4Desc = 'No block tapped';

    if (obConfluence) {
      p4Status = 'PASS';
      p4Badge = obType ? obType.replace('_', ' ') : 'OB TAPPED';
      p4Desc = 'Institutional block touched (15.0 pts)';
    } else if ((indicators?.order_blocks?.length || 0) > 0) {
      p4Status = 'PENDING';
      p4Badge = 'IN SIGHT';
      p4Desc = 'Unmitigated Order Block nearby';
    } else {
      p4Status = 'PENDING';
      p4Badge = 'NONE';
      p4Desc = 'Awaiting institutional block touch';
    }

    // 5. Sequential Volume Expansion
    let p5Status: 'PASS' | 'PENDING' | 'FAIL' = 'PENDING';
    let p5Badge = `${volSurge.toFixed(1)}x VOL`;
    let p5Desc = 'Normal Volume';

    if (volSurge >= 1.5) {
      p5Status = 'PASS';
      p5Badge = `SURGE ${volSurge.toFixed(1)}x`;
      p5Desc = `Volume >= 1.5x 20-period SMA`;
    } else if (volSurge >= 1.1) {
      p5Status = 'PENDING';
      p5Badge = `${volSurge.toFixed(1)}x VOL`;
      p5Desc = 'Moderate volume increase';
    } else {
      p5Status = 'PENDING';
      p5Badge = `${volSurge.toFixed(1)}x VOL`;
      p5Desc = 'Sub-average participation';
    }

    // 6. Momentum & Fractal RSI Divergence
    let p6Status: 'PASS' | 'PENDING' | 'FAIL' = 'PENDING';
    let p6Badge = rsi !== null ? `RSI ${rsi.toFixed(1)}` : 'RSI --';
    let p6Desc = 'Neutral Momentum';

    const relevantDiv = isLong ? hasBullDiv : hasBearDiv;
    const rsiOversold = isLong && rsi !== null && rsi <= 35;
    const rsiOverbought = !isLong && rsi !== null && rsi >= 65;

    if (relevantDiv) {
      p6Status = 'PASS';
      p6Badge = 'DIVERGENCE';
      p6Desc = `Fractal ${side} Divergence Confirmed (+15 pts)`;
    } else if (rsiOversold || rsiOverbought) {
      p6Status = 'PASS';
      p6Badge = rsiOversold ? 'OVERSOLD' : 'OVERBOUGHT';
      p6Desc = `Extreme RSI Reversal Zone (${rsi?.toFixed(1)})`;
    } else {
      p6Status = 'PENDING';
      p6Badge = rsi !== null ? `RSI ${rsi.toFixed(1)}` : 'RSI';
      p6Desc = 'Within 40-60 equilibrium band';
    }

    // Construct the 6 Pillars
    const pillars: PillarStatus[] = [
      {
        id: 'p1_trend',
        title: 'Macro & HTF Structure',
        desc: p1Desc,
        status: p1Status,
        badge: p1Badge,
        icon: <Waves size={14} className="text-laser-cyan" />,
      },
      {
        id: 'p2_adx',
        title: 'ADX Momentum / Schmitt',
        desc: p2Desc,
        status: p2Status,
        badge: p2Badge,
        icon: <Activity size={14} className="text-amber-warn" />,
      },
      {
        id: 'p3_fvg',
        title: 'SMC Fair Value Gap',
        desc: p3Desc,
        status: p3Status,
        badge: p3Badge,
        icon: <Layers size={14} className="text-purple-smc" />,
      },
      {
        id: 'p4_ob',
        title: 'Order / Breaker Block',
        desc: p4Desc,
        status: p4Status,
        badge: p4Badge,
        icon: <ShieldCheck size={14} className="text-laser-green" />,
      },
      {
        id: 'p5_vol',
        title: 'Sequential Volume Surge',
        desc: p5Desc,
        status: p5Status,
        badge: p5Badge,
        icon: <BarChart3 size={14} className="text-emerald-400" />,
      },
      {
        id: 'p6_rsi',
        title: 'Momentum / Fractal RSI',
        desc: p6Desc,
        status: p6Status,
        badge: p6Badge,
        icon: <Zap size={14} className="text-sky-400" />,
      },
    ];

    const passCount = pillars.filter((p) => p.status === 'PASS').length;
    const failCount = pillars.filter((p) => p.status === 'FAIL').length;
    const pendingCount = pillars.filter((p) => p.status === 'PENDING').length;

    // Determine Hard Execution Block Reason
    let blockReason: string | null = null;
    if (isHtfOpposing) {
      blockReason = `Macro Invalidation: 4h HTF is ${htfTrend}`;
    } else if (isChop || adx < 24.0) {
      blockReason = `Chop Invalidation: ADX ${adx.toFixed(1)} < 24.0`;
    }

    const isReadyToFire = !blockReason && passCount >= 4;

    return {
      pillars,
      passCount,
      failCount,
      pendingCount,
      blockReason,
      isReadyToFire,
      confluenceScore: opportunity?.confluence_score ?? Math.min(100, passCount * 16.6),
    };
  }, [side, indicators, opportunity, currentPrice]);

  return (
    <div className="space-y-2 select-none font-mono text-xs">
      {/* 1. Execution Readiness Action Banner */}
      {analysis.blockReason ? (
        <div className="p-2.5 rounded border bg-crimson-red/15 border-crimson-red/50 text-crimson-red flex items-start space-x-2">
          <ShieldAlert size={16} className="shrink-0 mt-0.5 animate-pulse" />
          <div className="flex-1">
            <div className="font-bold text-[12px] tracking-wide flex items-center justify-between">
              <span>⛔ EXECUTION BLOCKED</span>
              <span className="text-[10px] px-1.5 py-0.2 rounded bg-crimson-red/20 font-mono">
                {analysis.passCount}/6 PASS
              </span>
            </div>
            <div className="text-[11px] text-slate-300 mt-0.5 leading-snug">
              {analysis.blockReason}
            </div>
          </div>
        </div>
      ) : analysis.isReadyToFire ? (
        <div className="p-2.5 rounded border bg-laser-green/15 border-laser-green/60 text-laser-green shadow-[0_0_12px_rgba(0,255,163,0.2)] flex items-start space-x-2">
          <Flame size={16} className="shrink-0 mt-0.5 text-laser-green animate-bounce" />
          <div className="flex-1">
            <div className="font-bold text-[12px] tracking-wide flex items-center justify-between">
              <span>🟢 READY TO FIRE — A+ CONFLUENCE</span>
              <span className="text-[10px] px-1.5 py-0.2 rounded bg-laser-green/20 font-bold">
                {analysis.passCount}/6 PASS
              </span>
            </div>
            <div className="text-[11px] text-slate-200 mt-0.5 leading-snug">
              All core criteria aligned. Limit entry recommended at 50% CE.
            </div>
          </div>
        </div>
      ) : (
        <div className="p-2.5 rounded border bg-sky-500/10 border-sky-400/40 text-sky-400 flex items-start space-x-2">
          <Clock size={16} className="shrink-0 mt-0.5 text-sky-400" />
          <div className="flex-1">
            <div className="font-bold text-[12px] tracking-wide flex items-center justify-between">
              <span>🟡 STAGING IN PROGRESS</span>
              <span className="text-[10px] px-1.5 py-0.2 rounded bg-sky-400/20 font-bold">
                {analysis.passCount}/6 MET
              </span>
            </div>
            <div className="text-[11px] text-slate-300 mt-0.5 leading-snug">
              Waiting for {analysis.pendingCount} confirmation{analysis.pendingCount > 1 ? 's' : ''} to reach institutional threshold.
            </div>
          </div>
        </div>
      )}

      {/* 2. 6-Pillar Checklist Grid */}
      <div className="bg-dark-card border border-dark-border rounded p-2 space-y-1.5">
        <div className="flex items-center justify-between pb-1 border-b border-dark-border/50 text-[10px] text-slate-400 font-semibold uppercase tracking-wider">
          <span>6-Pillar Confirmation Matrix</span>
          <span className="text-slate-300 font-bold">
            Score: {analysis.confluenceScore.toFixed(0)}/100
          </span>
        </div>

        <div className="space-y-1 pt-0.5">
          {analysis.pillars.map((p) => {
            let statusIcon = <Clock size={13} className="text-slate-500 shrink-0" />;
            let badgeCls = 'bg-slate-800 text-slate-400 border-slate-700';

            if (p.status === 'PASS') {
              statusIcon = <CheckCircle2 size={13} className="text-laser-green shrink-0" />;
              badgeCls = 'bg-laser-green/15 text-laser-green border-laser-green/40 font-bold';
            } else if (p.status === 'FAIL') {
              statusIcon = <XCircle size={13} className="text-crimson-red shrink-0" />;
              badgeCls = 'bg-crimson-red/15 text-crimson-red border-crimson-red/40 font-bold';
            }

            return (
              <div
                key={p.id}
                className="flex items-center justify-between py-1 px-1.5 rounded hover:bg-dark-panel/50 transition border border-transparent hover:border-dark-border"
              >
                <div className="flex items-center space-x-2 min-w-0 pr-2">
                  {statusIcon}
                  <div className="min-w-0">
                    <div className="text-[11px] font-bold text-slate-200 truncate flex items-center space-x-1">
                      <span>{p.title}</span>
                    </div>
                    <div className="text-[10px] text-slate-400 truncate">{p.desc}</div>
                  </div>
                </div>

                <span
                  className={`text-[9px] px-1.5 py-0.5 rounded border whitespace-nowrap shrink-0 ${badgeCls}`}
                >
                  {p.badge}
                </span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
};

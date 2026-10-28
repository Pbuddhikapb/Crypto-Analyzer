import React, { useEffect, useRef, useState, useMemo } from 'react';
import {
  createChart,
  ColorType,
  CrosshairMode,
  IChartApi,
  ISeriesApi,
  UTCTimestamp,
  LineStyle,
  IPriceLine,
} from 'lightweight-charts';
import { Candle, Indicators } from '../types/market';
import { formatPrice, getMinMove } from '../utils/formatters';

interface TradingChartProps {
  candles: Candle[];
  indicators: Indicators | null;
  showEMAs: boolean;
  showFVGs: boolean;
  showRSI: boolean;
  latestPrice?: number | null;
}

export const TradingChart: React.FC<TradingChartProps> = ({
  candles,
  indicators,
  showEMAs,
  showFVGs,
  showRSI,
  latestPrice,
}) => {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const rsiContainerRef = useRef<HTMLDivElement>(null);

  const chartRef = useRef<IChartApi | null>(null);
  const rsiChartRef = useRef<IChartApi | null>(null);

  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const ema50SeriesRef = useRef<ISeriesApi<'Line'> | null>(null);
  const ema200SeriesRef = useRef<ISeriesApi<'Line'> | null>(null);
  const rsiSeriesRef = useRef<ISeriesApi<'Line'> | null>(null);
  const fvgPriceLinesRef = useRef<IPriceLine[]>([]);

  // Hovered or latest candle for chart tooltip
  const [hoveredCandle, setHoveredCandle] = useState<Candle | null>(null);
  const [hoveredEma50, setHoveredEma50] = useState<number | null>(null);
  const [hoveredEma200, setHoveredEma200] = useState<number | null>(null);

  // Reference price derived from latest price or most recent candle
  const refPrice = useMemo(() => {
    if (latestPrice && latestPrice > 0) return latestPrice;
    if (candles.length > 0) return candles[candles.length - 1].close;
    return 1000.0;
  }, [latestPrice, candles]);

  // 1. Initialize Main Candlestick Chart
  useEffect(() => {
    if (!chartContainerRef.current) return;

    const formatter = (p: number) => formatPrice(p, refPrice);

    const chart = createChart(chartContainerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: '#080b11' },
        textColor: '#94a3b8',
        fontFamily: 'ui-monospace, monospace',
        fontSize: 11,
      },
      localization: {
        priceFormatter: formatter,
      },
      grid: {
        vertLines: { color: '#131b2c', style: LineStyle.Dotted },
        horzLines: { color: '#131b2c', style: LineStyle.Dotted },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: '#38bdf8', width: 1, style: LineStyle.Dashed },
        horzLine: { color: '#38bdf8', width: 1, style: LineStyle.Dashed },
      },
      timeScale: {
        borderColor: '#1e293b',
        timeVisible: true,
        secondsVisible: false,
      },
      rightPriceScale: {
        borderColor: '#1e293b',
        scaleMargins: { top: 0.1, bottom: 0.15 },
      },
    });

    const minMove = getMinMove(refPrice);

    const candleSeries = chart.addCandlestickSeries({
      upColor: '#00e676',
      downColor: '#ff1744',
      borderUpColor: '#00e676',
      borderDownColor: '#ff1744',
      wickUpColor: '#00e676',
      wickDownColor: '#ff1744',
      priceFormat: {
        type: 'custom',
        formatter,
        minMove,
      },
    });

    const ema50Series = chart.addLineSeries({
      color: '#00f2fe',
      lineWidth: 1,
      title: 'EMA 50',
      priceFormat: {
        type: 'custom',
        formatter,
        minMove,
      },
    });

    const ema200Series = chart.addLineSeries({
      color: '#a855f7',
      lineWidth: 2,
      title: 'EMA 200',
      priceFormat: {
        type: 'custom',
        formatter,
        minMove,
      },
    });

    chartRef.current = chart;
    candleSeriesRef.current = candleSeries;
    ema50SeriesRef.current = ema50Series;
    ema200SeriesRef.current = ema200Series;

    // Crosshair move listener for interactive tooltip
    chart.subscribeCrosshairMove((param) => {
      if (!param || !param.time || !param.seriesData) {
        setHoveredCandle(null);
        setHoveredEma50(null);
        setHoveredEma200(null);
        return;
      }

      const cData = param.seriesData.get(candleSeries) as { open: number; high: number; low: number; close: number } | undefined;
      if (cData) {
        setHoveredCandle({
          time: Number(param.time),
          open: cData.open,
          high: cData.high,
          low: cData.low,
          close: cData.close,
          volume: 0,
        });
      }

      const e50 = param.seriesData.get(ema50Series) as { value: number } | undefined;
      setHoveredEma50(e50?.value ?? null);

      const e200 = param.seriesData.get(ema200Series) as { value: number } | undefined;
      setHoveredEma200(e200?.value ?? null);
    });

    // ResizeObserver for main chart
    const resizeObserver = new ResizeObserver((entries) => {
      if (!entries || entries.length === 0) return;
      const { width, height } = entries[0].contentRect;
      chart.applyOptions({ width, height });
    });
    resizeObserver.observe(chartContainerRef.current);

    return () => {
      resizeObserver.disconnect();
      chart.remove();
      chartRef.current = null;
      candleSeriesRef.current = null;
      ema50SeriesRef.current = null;
      ema200SeriesRef.current = null;
    };
  }, []);

  // Update chart price formatting whenever the symbol reference price changes tier
  useEffect(() => {
    if (!chartRef.current || !candleSeriesRef.current) return;
    const formatter = (p: number) => formatPrice(p, refPrice);
    const minMove = getMinMove(refPrice);

    chartRef.current.applyOptions({
      localization: { priceFormatter: formatter },
    });
    candleSeriesRef.current.applyOptions({
      priceFormat: { type: 'custom', formatter, minMove },
    });
    ema50SeriesRef.current?.applyOptions({
      priceFormat: { type: 'custom', formatter, minMove },
    });
    ema200SeriesRef.current?.applyOptions({
      priceFormat: { type: 'custom', formatter, minMove },
    });
  }, [refPrice]);

  // 2. Initialize RSI Sub-Pane Chart (when showRSI is true)
  useEffect(() => {
    if (!showRSI || !rsiContainerRef.current) return;

    const rsiChart = createChart(rsiContainerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: '#06090e' },
        textColor: '#64748b',
        fontFamily: 'ui-monospace, monospace',
        fontSize: 10,
      },
      grid: {
        vertLines: { color: '#0d1320', style: LineStyle.Dotted },
        horzLines: { color: '#0d1320', style: LineStyle.Dotted },
      },
      timeScale: {
        visible: true,
        borderColor: '#1e293b',
      },
      rightPriceScale: {
        borderColor: '#1e293b',
        scaleMargins: { top: 0.1, bottom: 0.1 },
      },
    });

    const rsiSeries = rsiChart.addLineSeries({
      color: '#38bdf8',
      lineWidth: 1,
      title: 'RSI (14)',
    });

    // Overbought 70 and Oversold 30 reference levels
    rsiSeries.createPriceLine({
      price: 70,
      color: '#ff1744',
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      title: 'OB 70',
      axisLabelVisible: true,
    });
    rsiSeries.createPriceLine({
      price: 30,
      color: '#00e676',
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      title: 'OS 30',
      axisLabelVisible: true,
    });

    rsiChartRef.current = rsiChart;
    rsiSeriesRef.current = rsiSeries;

    // Synchronize time scales
    if (chartRef.current) {
      chartRef.current.timeScale().subscribeVisibleLogicalRangeChange((range) => {
        if (range && rsiChartRef.current) {
          rsiChartRef.current.timeScale().setVisibleLogicalRange(range);
        }
      });
    }

    const rsiResizeObserver = new ResizeObserver((entries) => {
      if (!entries || entries.length === 0) return;
      const { width, height } = entries[0].contentRect;
      rsiChart.applyOptions({ width, height });
    });
    rsiResizeObserver.observe(rsiContainerRef.current);

    return () => {
      rsiResizeObserver.disconnect();
      rsiChart.remove();
      rsiChartRef.current = null;
      rsiSeriesRef.current = null;
    };
  }, [showRSI]);

  // 3. Populate Candlesticks and Calculate Dynamic EMAs
  useEffect(() => {
    if (!candleSeriesRef.current || candles.length === 0) return;

    // Sort and deduplicate candles to guarantee strictly ascending timestamps for lightweight-charts
    const sorted = [...candles].sort((a, b) => Number(a.time) - Number(b.time));
    const cleanCandles: typeof candles = [];
    for (const c of sorted) {
      if (cleanCandles.length === 0 || Number(c.time) > Number(cleanCandles[cleanCandles.length - 1].time)) {
        cleanCandles.push(c);
      }
    }

    if (cleanCandles.length === 0) return;

    const chartCandles = cleanCandles.map((c) => ({
      time: c.time as UTCTimestamp,
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
    }));

    candleSeriesRef.current.setData(chartCandles);

    // Compute rolling EMA series
    if (showEMAs && ema50SeriesRef.current && ema200SeriesRef.current) {
      const closes = cleanCandles.map((c) => c.close);
      const ema50Data: { time: UTCTimestamp; value: number }[] = [];
      const ema200Data: { time: UTCTimestamp; value: number }[] = [];

      // Calculate rolling EMA 50
      if (closes.length >= 50) {
        let ema = closes.slice(0, 50).reduce((a, b) => a + b, 0) / 50;
        const k = 2 / (50 + 1);
        ema50Data.push({ time: cleanCandles[49].time as UTCTimestamp, value: ema });
        for (let i = 50; i < closes.length; i++) {
          ema = closes[i] * k + ema * (1 - k);
          ema50Data.push({ time: cleanCandles[i].time as UTCTimestamp, value: ema });
        }
      }

      // Calculate rolling EMA 200
      if (closes.length >= 200) {
        let ema = closes.slice(0, 200).reduce((a, b) => a + b, 0) / 200;
        const k = 2 / (200 + 1);
        ema200Data.push({ time: cleanCandles[199].time as UTCTimestamp, value: ema });
        for (let i = 200; i < closes.length; i++) {
          ema = closes[i] * k + ema * (1 - k);
          ema200Data.push({ time: cleanCandles[i].time as UTCTimestamp, value: ema });
        }
      }

      ema50SeriesRef.current.setData(ema50Data);
      ema200SeriesRef.current.setData(ema200Data);
    } else if (ema50SeriesRef.current && ema200SeriesRef.current) {
      ema50SeriesRef.current.setData([]);
      ema200SeriesRef.current.setData([]);
    }

    // Populate RSI Sub-Pane
    if (showRSI && rsiSeriesRef.current) {
      if (cleanCandles.length >= 15) {
        const closes = cleanCandles.map((c) => c.close);
        const rsiData: { time: UTCTimestamp; value: number }[] = [];

        // Calculate Wilder's RSI
        let gains = 0;
        let losses = 0;
        for (let i = 1; i <= 14; i++) {
          const diff = closes[i] - closes[i - 1];
          if (diff >= 0) gains += diff;
          else losses -= diff;
        }
        let avgGain = gains / 14;
        let avgLoss = losses / 14;
        let rs = avgLoss === 0 ? 100 : avgGain / avgLoss;
        let rsi = 100 - 100 / (1 + rs);
        rsiData.push({ time: cleanCandles[14].time as UTCTimestamp, value: rsi });

        for (let i = 15; i < closes.length; i++) {
          const diff = closes[i] - closes[i - 1];
          const gain = diff > 0 ? diff : 0;
          const loss = diff < 0 ? -diff : 0;
          avgGain = (avgGain * 13 + gain) / 14;
          avgLoss = (avgLoss * 13 + loss) / 14;
          rs = avgLoss === 0 ? 100 : avgGain / avgLoss;
          rsi = 100 - 100 / (1 + rs);
          rsiData.push({ time: cleanCandles[i].time as UTCTimestamp, value: rsi });
        }
        rsiSeriesRef.current.setData(rsiData);
      } else {
        rsiSeriesRef.current.setData([]);
      }
    }
  }, [candles, showEMAs, showRSI]);

  // 4. Update FVG and Bracket Price Lines
  useEffect(() => {
    if (!candleSeriesRef.current) return;

    // Clean old price lines
    fvgPriceLinesRef.current.forEach((pl) => candleSeriesRef.current?.removePriceLine(pl));
    fvgPriceLinesRef.current = [];

    if (showFVGs && indicators && indicators.active_fvg_boxes) {
      indicators.active_fvg_boxes.slice(-4).forEach((box) => {
        if (!candleSeriesRef.current) return;
        const isBull = box.type === 'BULLISH';
        const color = isBull ? '#00e676' : '#ff1744';

        const topPl = candleSeriesRef.current.createPriceLine({
          price: box.top,
          color,
          lineWidth: 1,
          lineStyle: LineStyle.Dotted,
          title: `${box.type} FVG TOP: ${formatPrice(box.top, refPrice)}`,
          axisLabelVisible: true,
        });

        const botPl = candleSeriesRef.current.createPriceLine({
          price: box.bottom,
          color,
          lineWidth: 1,
          lineStyle: LineStyle.Dotted,
          title: `${box.type} FVG BOT: ${formatPrice(box.bottom, refPrice)}`,
          axisLabelVisible: true,
        });

        fvgPriceLinesRef.current.push(topPl, botPl);

        if (box.ce && (!indicators.trade_setup || Math.abs(indicators.trade_setup.limit_entry - box.ce) > 0.0001)) {
          const cePl = candleSeriesRef.current.createPriceLine({
            price: box.ce,
            color: '#f59e0b',
            lineWidth: 1,
            lineStyle: LineStyle.Dashed,
            title: `FVG 50% CE: ${formatPrice(box.ce, refPrice)}`,
            axisLabelVisible: true,
          });
          fvgPriceLinesRef.current.push(cePl);
        }
      });

      // Render Single Exit Authority Synthetic OCO Bracket Targets
      const setup = indicators.trade_setup;
      if (setup && candleSeriesRef.current) {
        // 1. Limit Entry
        const entryPl = candleSeriesRef.current.createPriceLine({
          price: setup.limit_entry,
          color: setup.side === 'LONG' ? '#38bdf8' : '#fb923c',
          lineWidth: 2,
          lineStyle: LineStyle.Solid,
          title: `LIMIT ENTRY (${setup.side} @ ${formatPrice(setup.limit_entry, refPrice)})`,
          axisLabelVisible: true,
        });

        // 2. Structural Stop Loss (-1.0R)
        const slPl = candleSeriesRef.current.createPriceLine({
          price: setup.stop_loss,
          color: '#ef4444',
          lineWidth: 2,
          lineStyle: LineStyle.Solid,
          title: `STOP LOSS (-1.0R @ ${formatPrice(setup.stop_loss, refPrice)} | -${setup.risk_pct.toFixed(2)}%)`,
          axisLabelVisible: true,
        });

        // 3. TP1 (+0.75R / 50% Partial Scale-out)
        const tp1Pl = candleSeriesRef.current.createPriceLine({
          price: setup.tp1,
          color: '#10b981',
          lineWidth: 2,
          lineStyle: LineStyle.Dashed,
          title: `TP1 (+${setup.tp1_r ?? 0.75}R @ ${formatPrice(setup.tp1, refPrice)} / 50% SCALE)`,
          axisLabelVisible: true,
        });

        // 4. Breakeven Armed Trigger (+0.85R Excursion)
        const beTriggerPrice = setup.be_trigger ?? (setup.side === 'LONG' ? setup.limit_entry + (0.85 * setup.risk_dist) : setup.limit_entry - (0.85 * setup.risk_dist));
        const bePl = candleSeriesRef.current.createPriceLine({
          price: beTriggerPrice,
          color: '#06b6d4',
          lineWidth: 1,
          lineStyle: LineStyle.Dotted,
          title: `BE TRIGGER (+${setup.be_trigger_r ?? 0.85}R @ ${formatPrice(beTriggerPrice, refPrice)} ARMED)`,
          axisLabelVisible: true,
        });

        // 5. TP2 (+1.33R Final Runner)
        const tp2Pl = candleSeriesRef.current.createPriceLine({
          price: setup.tp2,
          color: '#00e676',
          lineWidth: 2,
          lineStyle: LineStyle.Solid,
          title: `TP2 RUNNER (+${setup.tp2_r ?? setup.rr_ratio}R @ ${formatPrice(setup.tp2, refPrice)} FINAL)`,
          axisLabelVisible: true,
        });

        fvgPriceLinesRef.current.push(entryPl, slPl, tp1Pl, bePl, tp2Pl);
      }

      // Render Fibonacci OTE and Equilibrium Levels if available
      const fibs = indicators.fibonacci_levels;
      if (fibs && candleSeriesRef.current) {
        const eqPl = candleSeriesRef.current.createPriceLine({
          price: fibs['0.5'],
          color: '#38bdf8',
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          title: `50% EQUILIBRIUM: ${formatPrice(fibs['0.5'], refPrice)}`,
          axisLabelVisible: true,
        });

        const ote618Pl = candleSeriesRef.current.createPriceLine({
          price: fibs['0.618'],
          color: '#f59e0b',
          lineWidth: 1,
          lineStyle: LineStyle.Solid,
          title: `0.618 OTE BOUND: ${formatPrice(fibs['0.618'], refPrice)}`,
          axisLabelVisible: true,
        });

        const ote705Pl = candleSeriesRef.current.createPriceLine({
          price: fibs['0.705'],
          color: '#fbbf24',
          lineWidth: 2,
          lineStyle: LineStyle.Solid,
          title: `0.705 SWEET SPOT: ${formatPrice(fibs['0.705'], refPrice)}`,
          axisLabelVisible: true,
        });

        const ote786Pl = candleSeriesRef.current.createPriceLine({
          price: fibs['0.786'],
          color: '#f59e0b',
          lineWidth: 1,
          lineStyle: LineStyle.Solid,
          title: `0.786 OTE BOUND: ${formatPrice(fibs['0.786'], refPrice)}`,
          axisLabelVisible: true,
        });

        fvgPriceLinesRef.current.push(eqPl, ote618Pl, ote705Pl, ote786Pl);
      }

      // Render Active Order Block / Breaker Block Mean Threshold Lines
      if (indicators.order_blocks && candleSeriesRef.current) {
        indicators.order_blocks.slice(-2).forEach((ob) => {
          const isBull = ob.type.includes('BULLISH');
          const obMtPl = candleSeriesRef.current!.createPriceLine({
            price: ob.mean_threshold,
            color: isBull ? '#10b981' : '#f43f5e',
            lineWidth: 1,
            lineStyle: LineStyle.Dashed,
            title: `${isBull ? 'DEMAND' : 'SUPPLY'} OB MT: ${formatPrice(ob.mean_threshold, refPrice)}`,
            axisLabelVisible: true,
          });
          fvgPriceLinesRef.current.push(obMtPl);
        });
      }
    }
  }, [indicators, showFVGs, refPrice]);

  // 5. Real-time Tick update on last candle
  useEffect(() => {
    if (!candleSeriesRef.current || !latestPrice || candles.length === 0) return;
    const last = candles[candles.length - 1];
    candleSeriesRef.current.update({
      time: last.time as UTCTimestamp,
      open: last.open,
      high: Math.max(last.high, latestPrice),
      low: Math.min(last.low, latestPrice),
      close: latestPrice,
    });
  }, [latestPrice, candles]);

  const activeCandle = hoveredCandle || (candles.length > 0 ? candles[candles.length - 1] : null);

  return (
    <div className="w-full h-full flex flex-col bg-dark-bg select-none relative">
      {/* Chart Top-Left Tooltip & OHLC Legend */}
      {activeCandle && (
        <div className="absolute top-2 left-3 z-10 pointer-events-none flex flex-wrap items-center gap-x-3 gap-y-1 bg-dark-bg/90 backdrop-blur-sm border border-dark-border/60 px-2.5 py-1 rounded text-[11px] font-mono shadow-sm">
          <div className="flex items-center space-x-1.5 text-slate-300">
            <span className="text-slate-500 font-semibold">O:</span>
            <span className="font-bold">{formatPrice(activeCandle.open, refPrice)}</span>
            <span className="text-slate-500 font-semibold">H:</span>
            <span className="font-bold text-laser-green">{formatPrice(activeCandle.high, refPrice)}</span>
            <span className="text-slate-500 font-semibold">L:</span>
            <span className="font-bold text-crimson-red">{formatPrice(activeCandle.low, refPrice)}</span>
            <span className="text-slate-500 font-semibold">C:</span>
            <span className="font-bold">{formatPrice(activeCandle.close, refPrice)}</span>
          </div>
          {showEMAs && (candles.length >= 50 || candles.length >= 200) && (
            <div className="flex items-center space-x-2 text-[10px]">
              {candles.length >= 50 && (
                <span className="text-laser-cyan font-semibold">
                  EMA 50: {formatPrice(hoveredEma50 ?? indicators?.ema_50 ?? null, refPrice)}
                </span>
              )}
              {candles.length >= 200 && (
                <span className="text-purple-400 font-semibold">
                  EMA 200: {formatPrice(hoveredEma200 ?? indicators?.ema_200 ?? null, refPrice)}
                </span>
              )}
            </div>
          )}
        </div>
      )}

      <div ref={chartContainerRef} className="flex-1 w-full min-h-0" />
      {showRSI && (
        <div className="h-28 w-full border-t border-dark-border flex flex-col">
          <div className="px-3 py-0.5 bg-dark-panel/90 border-b border-dark-border/40 flex items-center justify-between text-[10px] text-slate-400">
            <span className="font-semibold text-sky-400">
              RSI (14) MOMENTUM OSCILLATOR
              {candles.length < 15 && (
                <span className="ml-1.5 text-amber-400/80 font-normal">(Needs &ge; 15 bars)</span>
              )}
            </span>
            {candles.length >= 15 && indicators?.has_bullish_divergence && (
              <span className="text-laser-green font-bold bg-laser-green/10 px-1 rounded">
                BULLISH DIVERGENCE DETECTED
              </span>
            )}
            {candles.length >= 15 && indicators?.has_bearish_divergence && (
              <span className="text-crimson-red font-bold bg-crimson-red/10 px-1 rounded">
                BEARISH DIVERGENCE DETECTED
              </span>
            )}
          </div>
          <div ref={rsiContainerRef} className="flex-1 w-full min-h-0" />
        </div>
      )}
    </div>
  );
};

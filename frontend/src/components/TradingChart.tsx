import React, { useEffect, useRef } from 'react';
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

  // 1. Initialize Main Candlestick Chart
  useEffect(() => {
    if (!chartContainerRef.current) return;

    const chart = createChart(chartContainerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: '#080b11' },
        textColor: '#94a3b8',
        fontFamily: 'ui-monospace, monospace',
        fontSize: 11,
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

    const candleSeries = chart.addCandlestickSeries({
      upColor: '#00e676',
      downColor: '#ff1744',
      borderUpColor: '#00e676',
      borderDownColor: '#ff1744',
      wickUpColor: '#00e676',
      wickDownColor: '#ff1744',
    });

    const ema50Series = chart.addLineSeries({
      color: '#00f2fe',
      lineWidth: 1,
      title: 'EMA 50',
    });

    const ema200Series = chart.addLineSeries({
      color: '#a855f7',
      lineWidth: 2,
      title: 'EMA 200',
    });

    chartRef.current = chart;
    candleSeriesRef.current = candleSeries;
    ema50SeriesRef.current = ema50Series;
    ema200SeriesRef.current = ema200Series;

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
    if (showRSI && rsiSeriesRef.current && cleanCandles.length >= 15) {
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
    }
  }, [candles, showEMAs, showRSI]);

  // 4. Update FVG Price Lines
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
          title: `${box.type} FVG TOP`,
          axisLabelVisible: true,
        });

        const botPl = candleSeriesRef.current.createPriceLine({
          price: box.bottom,
          color,
          lineWidth: 1,
          lineStyle: LineStyle.Dotted,
          title: `${box.type} FVG BOT`,
          axisLabelVisible: true,
        });

        fvgPriceLinesRef.current.push(topPl, botPl);

        if (box.ce && (!indicators.trade_setup || Math.abs(indicators.trade_setup.limit_entry - box.ce) > 0.0001)) {
          const cePl = candleSeriesRef.current.createPriceLine({
            price: box.ce,
            color: '#f59e0b',
            lineWidth: 1,
            lineStyle: LineStyle.Dashed,
            title: `FVG 50% CE`,
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
          title: `LIMIT ENTRY (${setup.side})`,
          axisLabelVisible: true,
        });

        // 2. Structural Stop Loss (-1.0R)
        const slPl = candleSeriesRef.current.createPriceLine({
          price: setup.stop_loss,
          color: '#ef4444',
          lineWidth: 2,
          lineStyle: LineStyle.Solid,
          title: `STOP LOSS (-1.0R | -${setup.risk_pct.toFixed(2)}%)`,
          axisLabelVisible: true,
        });

        // 3. TP1 (+0.75R / 50% Partial Scale-out)
        const tp1Pl = candleSeriesRef.current.createPriceLine({
          price: setup.tp1,
          color: '#10b981',
          lineWidth: 2,
          lineStyle: LineStyle.Dashed,
          title: `TP1 (+${setup.tp1_r ?? 0.75}R / 50% SCALE)`,
          axisLabelVisible: true,
        });

        // 4. Breakeven Armed Trigger (+0.85R Excursion)
        const beTriggerPrice = setup.be_trigger ?? (setup.side === 'LONG' ? setup.limit_entry + (0.85 * setup.risk_dist) : setup.limit_entry - (0.85 * setup.risk_dist));
        const bePl = candleSeriesRef.current.createPriceLine({
          price: beTriggerPrice,
          color: '#06b6d4',
          lineWidth: 1,
          lineStyle: LineStyle.Dotted,
          title: `BE TRIGGER (+${setup.be_trigger_r ?? 0.85}R ARMED)`,
          axisLabelVisible: true,
        });

        // 5. TP2 (+1.33R Final Runner)
        const tp2Pl = candleSeriesRef.current.createPriceLine({
          price: setup.tp2,
          color: '#00e676',
          lineWidth: 2,
          lineStyle: LineStyle.Solid,
          title: `TP2 RUNNER (+${setup.tp2_r ?? setup.rr_ratio}R FINAL)`,
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
          title: '50% EQUILIBRIUM',
          axisLabelVisible: true,
        });

        const ote618Pl = candleSeriesRef.current.createPriceLine({
          price: fibs['0.618'],
          color: '#f59e0b',
          lineWidth: 1,
          lineStyle: LineStyle.Solid,
          title: '0.618 OTE BOUND',
          axisLabelVisible: true,
        });

        const ote705Pl = candleSeriesRef.current.createPriceLine({
          price: fibs['0.705'],
          color: '#fbbf24',
          lineWidth: 2,
          lineStyle: LineStyle.Solid,
          title: '0.705 SWEET SPOT',
          axisLabelVisible: true,
        });

        const ote786Pl = candleSeriesRef.current.createPriceLine({
          price: fibs['0.786'],
          color: '#f59e0b',
          lineWidth: 1,
          lineStyle: LineStyle.Solid,
          title: '0.786 OTE BOUND',
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
            title: `${isBull ? 'DEMAND' : 'SUPPLY'} OB MT`,
            axisLabelVisible: true,
          });
          fvgPriceLinesRef.current.push(obMtPl);
        });
      }
    }
  }, [indicators, showFVGs]);

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

  return (
    <div className="w-full h-full flex flex-col bg-dark-bg select-none">
      <div ref={chartContainerRef} className="flex-1 w-full min-h-0" />
      {showRSI && (
        <div className="h-28 w-full border-t border-dark-border flex flex-col">
          <div className="px-3 py-0.5 bg-dark-panel/90 border-b border-dark-border/40 flex items-center justify-between text-[10px] text-slate-400">
            <span className="font-semibold text-sky-400">RSI (14) MOMENTUM OSCILLATOR</span>
            {indicators?.has_bullish_divergence && (
              <span className="text-laser-green font-bold bg-laser-green/10 px-1 rounded">
                BULLISH DIVERGENCE DETECTED
              </span>
            )}
            {indicators?.has_bearish_divergence && (
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

import { useEffect, useState, useCallback } from 'react';
import { AlertTriangle } from 'lucide-react';
import { Navbar } from './components/Navbar';
import { TopCoinsBar } from './components/TopCoinsBar';
import { ChartToolbar } from './components/ChartToolbar';
import { TradingChart } from './components/TradingChart';
import { WhaleTape } from './components/WhaleTape';
import { LiquidationCard } from './components/LiquidationCard';
import { OrderBookDepth } from './components/OrderBookDepth';
import { ConfluencePanel } from './components/ConfluencePanel';
import { TradeStagingCard } from './components/TradeStagingCard';
import { OpportunityRadar } from './components/OpportunityRadar';
import { ActiveTradeCockpit } from './components/ActiveTradeCockpit';
import { useWebSocket } from './state/useWebSocket';
import { soundEngine } from './state/audio';
import { formatUTCHM } from './utils/formatters';
import {
  fetchCandles,
  fetchHealth,
  fetchMarketSymbols,
  fetchMicrostructureSummary,
  fetchPortfolioSummary,
  fetchScannerOpportunities,
} from './state/api';
import {
  Candle,
  Indicators,
  LiquidationEvent,
  MarketSymbol,
  MicrostructureSummary,
  PortfolioSummary,
  ScannerOpportunity,
  SystemHealth,
  WhaleTrade,
  WSMessage,
} from './types/market';

export function App() {
  // Navigation & Market Universe State
  const [activeSymbol, setActiveSymbol] = useState<string>('BTCUSDT');
  const [activeTimeframe, setActiveTimeframe] = useState<string>('15m');
  const [symbols, setSymbols] = useState<MarketSymbol[]>([]);
  const [latestPrices, setLatestPrices] = useState<Record<string, number>>({});
  const [opportunities, setOpportunities] = useState<ScannerOpportunity[]>([]);

  // Market & Technical State
  const [candles, setCandles] = useState<Candle[]>([]);
  const [indicators, setIndicators] = useState<Indicators | null>(null);
  const [candleHistorySource, setCandleHistorySource] = useState<'rest_futures' | 'parquet' | 'none' | null>(null);
  const [candleLiveStartMs, setCandleLiveStartMs] = useState<number | null>(null);

  // Microstructure & Order Flow State
  const [microstructure, setMicrostructure] = useState<MicrostructureSummary | null>(null);
  const [whaleTrades, setWhaleTrades] = useState<WhaleTrade[]>([]);
  const [liquidations, setLiquidations] = useState<LiquidationEvent[]>([]);
  const [rollingLiqVol, setRollingLiqVol] = useState<number>(0);
  const [isCascadeAlert, setIsCascadeAlert] = useState<boolean>(false);

  // Portfolio State
  const [portfolio, setPortfolio] = useState<PortfolioSummary | null>(null);
  const [health, setHealth] = useState<SystemHealth | null>(null);
  const [localUpdates, setLocalUpdates] = useState<Record<string, number>>({});

  // UI Toggles
  const [showEMAs, setShowEMAs] = useState<boolean>(true);
  const [showFVGs, setShowFVGs] = useState<boolean>(true);
  const [showRSI, setShowRSI] = useState<boolean>(true);
  const [isRightPanelCollapsed, setIsRightPanelCollapsed] = useState<boolean>(false);
  const [rightTab, setRightTab] = useState<'COCKPIT' | 'STAGING' | 'ORDER_FLOW' | 'RADAR'>('RADAR');

  // WebSocket Message Dispatcher
  const handleWebSocketMessage = useCallback(
    (msg: WSMessage) => {
      switch (msg.type) {
        case 'tick':
          setLatestPrices((prev) => ({ ...prev, [msg.symbol]: msg.price }));
          setLocalUpdates((prev) => ({ ...prev, [msg.symbol]: Math.floor(Date.now() / 1000) }));
          break;

        case 'candle':
          setLocalUpdates((prev) => ({ ...prev, [msg.symbol]: Math.floor(Date.now() / 1000) }));
          if (msg.symbol === activeSymbol) {
            setCandles((prev) => {
              const updated = [...prev];
              const lastIdx = updated.length - 1;
              if (lastIdx >= 0 && Number(msg.candle.time) <= Number(updated[lastIdx].time)) {
                updated[lastIdx] = msg.candle;
              } else {
                updated.push(msg.candle);
                if (updated.length > 250) updated.shift();
              }
              return updated;
            });
            setIndicators(msg.indicators);
          }
          break;

        case 'whale_trade':
          setWhaleTrades((prev) => [msg.trade, ...prev.slice(0, 49)]);
          soundEngine.playWhaleChime(msg.trade.side);
          break;

        case 'liquidation':
          setLiquidations((prev) => [msg.event, ...prev.slice(0, 49)]);
          setRollingLiqVol(msg.event.rolling_60s_volume);
          setIsCascadeAlert(msg.event.is_cascade);
          if (msg.event.is_cascade) {
            soundEngine.playCascadeAlert();
          }
          break;

        case 'order_book_depth':
          window.dispatchEvent(new CustomEvent('crypto-order-book-depth', { detail: msg.depth }));
          break;

        case 'trade_opened':
        case 'trade_closed':
          fetchPortfolioSummary().then(setPortfolio).catch(() => {});
          break;
      }
    },
    [activeSymbol]
  );

  const { isConnected, latencyMs, sendMessage } = useWebSocket({
    onMessage: handleWebSocketMessage,
  });

  // Focus active symbol on backend for targeted low-bandwidth L2 depth streaming
  useEffect(() => {
    if (sendMessage) {
      sendMessage({ action: 'focus_symbol', symbol: activeSymbol });
    }
  }, [activeSymbol, sendMessage]);

  // Initial Load & Symbol/Timeframe Changes
  useEffect(() => {
    fetchMarketSymbols()
      .then((data) => {
        setSymbols(data);
        const prices: Record<string, number> = {};
        data.forEach((s) => {
          if (s.latest_price) prices[s.symbol] = s.latest_price;
        });
        setLatestPrices((prev) => ({ ...prices, ...prev }));
      })
      .catch(() => {});

    fetchPortfolioSummary().then(setPortfolio).catch(() => {});
    fetchScannerOpportunities().then(setOpportunities).catch(() => {});
    fetchHealth().then(setHealth).catch(() => {});

    const oppInterval = setInterval(() => {
      fetchScannerOpportunities().then(setOpportunities).catch(() => {});
      fetchHealth().then(setHealth).catch(() => {});
    }, 5000);
    return () => clearInterval(oppInterval);
  }, []);

  useEffect(() => {
    fetchCandles(activeSymbol, activeTimeframe, 250)
      .then((res) => {
        setCandles(res.candles);
        setIndicators(res.indicators);
        if (res.history_source) setCandleHistorySource(res.history_source);
        if (res.live_start_ms !== undefined) setCandleLiveStartMs(res.live_start_ms);
      })
      .catch(() => {});

    fetchMicrostructureSummary(activeSymbol)
      .then((res) => {
        setMicrostructure(res);
        setWhaleTrades(res.recent_whale_trades || []);
        setRollingLiqVol(res.rolling_60s_liquidation_volume || 0);
        setIsCascadeAlert(res.is_cascade_alert || false);
      })
      .catch(() => {});
  }, [activeSymbol, activeTimeframe]);

  const activeFuturesSymbol =
    symbols.find((s) => s.symbol === activeSymbol)?.futures_symbol || activeSymbol;

  const activeOpportunity = opportunities.find((o) => o.symbol === activeSymbol);

  const timeframeSecondsMap: Record<string, number> = {
    '1m': 60,
    '3m': 180,
    '5m': 300,
    '15m': 900,
    '30m': 1800,
    '1h': 3600,
    '2h': 7200,
    '4h': 14400,
    '1d': 86400,
  };
  const candleIntervalSec = timeframeSecondsMap[activeTimeframe] || health?.candle_interval_sec || 900;
  const currentLastUpdate =
    localUpdates[activeSymbol] ||
    health?.last_update_per_symbol?.[activeSymbol] ||
    health?.last_update_times?.[activeSymbol] ||
    null;

  const activeHistorySource =
    candleHistorySource ||
    health?.history_source?.[activeSymbol] ||
    'none';

  const liveStartMs =
    candleLiveStartMs ||
    health?.live_start_ms?.[activeSymbol] ||
    (candles.length > 0 ? (candles[0].time > 10_000_000_000 ? candles[0].time : candles[0].time * 1000) : null);
  const historyUnavailableLiveSince = liveStartMs ? formatUTCHM(liveStartMs) : formatUTCHM(Date.now());

  return (
    <div className="flex flex-col h-screen w-screen overflow-hidden bg-dark-bg text-slate-100">
      {/* 1. Header Navigation */}
      <Navbar
        isConnected={isConnected}
        latencyMs={latencyMs}
        isFuturesOnline={microstructure?.is_futures_online ?? true}
        macroBias={indicators?.trend || 'NEUTRAL'}
        dataSource={health?.data_source || 'binance_futures_um'}
        lastUpdateSec={currentLastUpdate}
        candleIntervalSec={candleIntervalSec}
        activeSymbol={activeSymbol}
      />

      {/* 2. Top Coins Horizontal Bar */}
      <TopCoinsBar
        symbols={symbols}
        activeSymbol={activeSymbol}
        onSelectSymbol={setActiveSymbol}
        latestPrices={latestPrices}
        opportunities={opportunities}
      />

      {/* 3. Main Terminal Workspace (3-Column Layout) */}
      <div className="flex-1 flex flex-col md:flex-row min-h-0 overflow-hidden">
        {/* Left/Center: Chart + Indicator Sub-Pane */}
        <div className="flex-1 flex flex-col min-w-0 border-r border-dark-border">
          <ChartToolbar
            symbol={activeSymbol}
            futuresSymbol={activeFuturesSymbol}
            activeTimeframe={activeTimeframe}
            onTimeframeChange={setActiveTimeframe}
            showEMAs={showEMAs}
            onToggleEMAs={() => setShowEMAs((v) => !v)}
            showFVGs={showFVGs}
            onToggleFVGs={() => setShowFVGs((v) => !v)}
            showRSI={showRSI}
            onToggleRSI={() => setShowRSI((v) => !v)}
            isRightPanelCollapsed={isRightPanelCollapsed}
            onToggleRightPanel={() => setIsRightPanelCollapsed((v) => !v)}
          />

          {activeHistorySource !== 'rest_futures' && (
            <div className="bg-amber-950/80 border-b border-amber-500/40 px-3 py-1.5 flex items-center gap-2 text-amber-300 text-xs font-mono">
              <AlertTriangle className="w-3.5 h-3.5 text-amber-400 shrink-0" />
              <span>
                History unavailable (REST blocked): charts show live data only since {historyUnavailableLiveSince}
              </span>
            </div>
          )}

          <div className="flex-1 min-h-0 relative">
            <TradingChart
              candles={candles}
              indicators={indicators}
              showEMAs={showEMAs}
              showFVGs={showFVGs}
              showRSI={showRSI}
              latestPrice={latestPrices[activeSymbol]}
            />
          </div>
        </div>

        {/* Right Panel: Trade Staging, Order Flow, Alpha Radar (Collapsible) */}
        {!isRightPanelCollapsed && (
          <div className="hidden md:block w-80 lg:w-96 flex flex-col min-h-0 bg-dark-panel border-l border-dark-border">
            {/* Panel Tab Switcher */}
            <div className="flex items-center border-b border-dark-border bg-dark-bg/60 p-1 text-[11px] font-mono">
              <button
                onClick={() => setRightTab('COCKPIT')}
                className={`flex-1 py-1.5 rounded font-bold transition text-center relative ${
                  rightTab === 'COCKPIT'
                    ? 'bg-amber-500/20 text-amber-400 border border-amber-400/40 shadow-sm'
                    : 'text-slate-400 hover:text-slate-200'
                }`}
              >
                <span>RISK</span>
              </button>
              <button
                onClick={() => setRightTab('STAGING')}
                className={`flex-1 py-1.5 rounded font-bold transition text-center ${
                  rightTab === 'STAGING'
                    ? 'bg-sky-500/20 text-sky-400 border border-sky-400/40 shadow-sm'
                    : 'text-slate-400 hover:text-slate-200'
                }`}
              >
                TRADE SLIP
              </button>
              <button
                onClick={() => setRightTab('ORDER_FLOW')}
                className={`flex-1 py-1.5 rounded font-bold transition text-center ${
                  rightTab === 'ORDER_FLOW'
                    ? 'bg-purple-500/20 text-purple-400 border border-purple-400/40 shadow-sm'
                    : 'text-slate-400 hover:text-slate-200'
                }`}
              >
                FLOW
              </button>
              <button
                onClick={() => setRightTab('RADAR')}
                className={`flex-1 py-1.5 rounded font-bold transition text-center ${
                  rightTab === 'RADAR'
                    ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-400/40 shadow-sm'
                    : 'text-slate-400 hover:text-slate-200'
                }`}
              >
                RADAR
              </button>
            </div>

            {/* Tab Views */}
            <div className="flex-1 min-h-0 overflow-hidden flex flex-col">
              {rightTab === 'COCKPIT' && (
                <ActiveTradeCockpit
                  portfolio={portfolio}
                  latestPrices={latestPrices}
                  onSelectSymbol={setActiveSymbol}
                  onSwitchToStaging={() => setRightTab('STAGING')}
                />
              )}

              {rightTab === 'STAGING' && (
                <TradeStagingCard
                  symbol={activeSymbol}
                  currentPrice={latestPrices[activeSymbol]}
                  tradeSetup={indicators?.trade_setup}
                  macroBias={indicators?.trend}
                  indicators={indicators}
                  opportunity={activeOpportunity}
                />
              )}

              {rightTab === 'ORDER_FLOW' && (
                <div className="flex-1 flex flex-col min-h-0 divide-y divide-dark-border overflow-y-auto">
                  <OrderBookDepth
                    symbol={activeSymbol}
                    latestPrice={latestPrices[activeSymbol]}
                  />
                  <WhaleTape trades={whaleTrades} />
                  <LiquidationCard
                    liquidations={liquidations}
                    rollingVolume={rollingLiqVol}
                    isCascadeAlert={isCascadeAlert}
                  />
                </div>
              )}

              {rightTab === 'RADAR' && (
                <OpportunityRadar
                  activeSymbol={activeSymbol}
                  onSelectSymbol={setActiveSymbol}
                />
              )}
            </div>
          </div>
        )}
      </div>

      {/* 4. Bottom Confluence & Status Bar */}
      <ConfluencePanel
        indicators={indicators}
        microstructure={microstructure}
        portfolio={portfolio}
        opportunity={activeOpportunity}
        apiGitCommit={health?.git_commit}
        onOpenCockpit={() => {
          setRightTab('COCKPIT');
          setIsRightPanelCollapsed(false);
        }}
      />
    </div>
  );
}

export default App;

import argparse
import asyncio
import json
import os
import sys
import time
from typing import Dict, List, Optional, Sequence

import aiohttp
import numpy as np
import websockets

from indicators import (
    calculate_ema,
    calculate_rsi,
    check_recent_bullish_fvg,
    check_recent_bearish_fvg,
    format_price,
)
from microstructure import (
    FundingRateTracker,
    FuturesHealthProber,
    LiquidationTracker,
    OpenInterestEngine,
    SymbolNormalizer,
    WhaleTradeFilter,
)
from risk import PortfolioRiskManager
from trader import PaperTrader

DEFAULT_MONITORED_SYMBOLS = [
    "BTCUSDT",
    "SOLUSDT",
    "XRPUSDT",
    "BNBUSDT",
    "SUIUSDT",
]
TOP_10_SYMBOLS = DEFAULT_MONITORED_SYMBOLS


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Institutional Binance Multi-Coin Scanner & Paper Trader")
    parser.add_argument(
        "--symbols",
        type=str,
        default=",".join(DEFAULT_MONITORED_SYMBOLS),
        help="Comma-separated symbols to monitor (e.g. BTCUSDT,SOLUSDT,XRPUSDT)",
    )
    parser.add_argument(
        "--timeframe",
        type=str,
        default="15m",
        choices=["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "1d"],
        help="Candle timeframe interval (default: 15m)",
    )
    parser.add_argument(
        "--no-market-structure",
        action="store_true",
        help="Disable Pillar 1 Market Structure BOS/CHoCH alignment requirement",
    )
    parser.add_argument(
        "--max-positions",
        type=int,
        default=3,
        help="Maximum concurrent active portfolio positions",
    )
    parser.add_argument(
        "--cooldown",
        type=int,
        default=3,
        help="Symbol cooldown bars after a trade closes",
    )
    return parser.parse_args()


def get_combined_stream_url(symbols: Sequence[str], timeframe: str) -> str:
    """Build Binance multiplexed WebSocket stream URL."""
    stream_params = "/".join(f"{s.strip().lower()}@kline_{timeframe}" for s in symbols)
    return f"wss://stream.binance.com:9443/stream?streams={stream_params}"


async def warmup_symbol(
    session: aiohttp.ClientSession,
    symbol: str,
    timeframe: str,
    trader: PaperTrader,
) -> None:
    """Fetch initial 250 klines for a single symbol and pre-seed trader buffers."""
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol.upper()}&interval={timeframe}&limit=250"
    for attempt in range(1, 4):
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                if response.status == 200:
                    data = await response.json()
                    # Load first 249 closed candles
                    for k in data[:249]:
                        candle_tuple = (
                            float(k[1]),
                            float(k[2]),
                            float(k[3]),
                            float(k[4]),
                            float(k[5]),
                        )
                        trader.load_historical_candle(candle_tuple)
                    break
                elif response.status == 429:
                    print(f"[WARMUP RATE LIMIT] 429 for {symbol}. Waiting 2s before retry...")
                    await asyncio.sleep(2.0)
                else:
                    print(f"[WARMUP WARNING] HTTP {response.status} for {symbol} (attempt {attempt}/3)")
                    await asyncio.sleep(1.0)
        except Exception as e:
            if attempt == 3:
                raise RuntimeError(f"Failed to warm up {symbol} after 3 attempts: {e}")
            await asyncio.sleep(1.0)

    # If trader has HTF trend filter enabled, also prime 4h macro candles
    if getattr(trader, "htf_trend_filter", False):
        htf_url = f"https://api.binance.com/api/v3/klines?symbol={symbol.upper()}&interval=4h&limit=250"
        for attempt in range(1, 4):
            try:
                async with session.get(htf_url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    if response.status == 200:
                        htf_data = await response.json()
                        for k in htf_data[:249]:
                            candle_tuple = (
                                float(k[1]),
                                float(k[2]),
                                float(k[3]),
                                float(k[4]),
                                float(k[5]),
                            )
                            trader.load_historical_htf_candle(candle_tuple)
                        break
                    elif response.status == 429:
                        await asyncio.sleep(2.0)
                    else:
                        await asyncio.sleep(1.0)
            except Exception:
                await asyncio.sleep(1.0)


async def warmup_all_traders(
    traders: Dict[str, PaperTrader],
    symbols: List[str],
    timeframe: str,
) -> None:
    """Perform parallel REST warmup across all symbols using connection pooling."""
    print(f"\n[WARMUP] Priming 250 {timeframe} candles across {len(symbols)} coins in parallel...")
    start_t = time.perf_counter()

    connector = aiohttp.TCPConnector(limit=10)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [
            warmup_symbol(session, sym, timeframe, traders[sym])
            for sym in symbols
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    failed_symbols = []
    for sym, res in zip(list(symbols), results):
        if isinstance(res, Exception):
            print(f"[WARMUP WARNING] Failed to prime {sym}: {res}. Dropping from active universe.")
            failed_symbols.append(sym)
            traders.pop(sym, None)

    for sym in failed_symbols:
        symbols.remove(sym)

    if not symbols:
        print("[WARMUP FATAL] All requested symbols failed warmup. Exiting.")
        sys.exit(1)

    elapsed = time.perf_counter() - start_t
    print(f"[WARMUP COMPLETE] {len(symbols)} coins primed in {elapsed:.2f}s (<2% Binance rate limit budget).\n")


class FuturesMicrostructureManager:
    """Manages real-time Binance Futures WebSocket streams and REST metrics."""

    def __init__(self, symbols: List[str]):
        self.symbols = [s.upper() for s in symbols]
        self.futures_symbols = [SymbolNormalizer.to_futures_symbol(s) for s in self.symbols]
        self.futures_to_spot = {SymbolNormalizer.to_futures_symbol(s): s for s in self.symbols}
        self.whale_filter = WhaleTradeFilter()
        self.liq_tracker = LiquidationTracker()
        self.oi_engine = OpenInterestEngine()
        self.funding_tracker = FundingRateTracker()
        self.is_online = False

    async def check_health(self) -> Tuple[bool, str]:
        loop = asyncio.get_running_loop()
        is_ok, msg = await loop.run_in_executor(None, FuturesHealthProber.probe_connectivity)
        self.is_online = is_ok
        return is_ok, msg

    async def run_ws_stream(self) -> None:
        """Stream real-time aggTrade and forceOrder events from Binance Futures routed WS."""
        if not self.is_online:
            return

        stream_names = [f"{sym.lower()}@aggTrade" for sym in self.futures_symbols]
        stream_names.append("!forceOrder@arr")
        ws_url = f"wss://fstream.binance.com/market/stream?streams={'/'.join(stream_names)}"
        backoff = 3

        while True:
            try:
                print(f"[Futures WS] Connecting to microstructure stream across {len(self.futures_symbols)} coins...")
                async with websockets.connect(ws_url, ping_interval=20, ping_timeout=20) as ws:
                    print(f"[Futures WS] Connected to Futures stream! Monitoring whale trades & liquidations...\n")
                    backoff = 3

                    async for message in ws:
                        payload = json.loads(message)
                        data = payload.get("data", payload)
                        event_type = data.get("e")

                        if event_type == "aggTrade":
                            f_sym = str(data.get("s", "")).upper()
                            if f_sym in self.futures_to_spot:
                                trade = self.whale_filter.process_trade(data)
                                if trade and trade.badge == "WHALE":
                                    spot_sym = self.futures_to_spot[f_sym]
                                    print(f"\n>>> [WHALE TRADE] [{trade.badge}] {spot_sym} {trade.side}: {trade.quantity:,.4f} @ {format_price(trade.price)} (${trade.notional:,.2f}) <<<\n")

                        elif event_type == "forceOrder":
                            o = data.get("o", {})
                            f_sym = str(o.get("s", "")).upper()
                            if f_sym in self.futures_to_spot:
                                event = self.liq_tracker.process_order(o)
                                if event:
                                    spot_sym = self.futures_to_spot[f_sym]
                                    rolling_vol, is_cascade = self.liq_tracker.get_rolling_summary(f_sym)
                                    if is_cascade:
                                        print(f"\n>>> [CASCADE ALERT] {spot_sym} {event.liquidated_position} FLUSH: 60s Liquidation Volume ${rolling_vol:,.2f} Exceeded $1M! <<<\n")

            except asyncio.CancelledError:
                print("[Futures WS] Stream cancelled. Shutting down cleanly.")
                break
            except Exception as e:
                print(f"[Futures WS] Error: {e}. Reconnecting in {backoff} seconds...")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    async def poll_rest_metrics(self) -> None:
        """Periodically query instantaneous Open Interest and Funding Rate every 30 seconds."""
        if not self.is_online:
            return

        connector = aiohttp.TCPConnector(limit=5)
        timeout = aiohttp.ClientTimeout(total=5)

        while True:
            try:
                async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
                    for f_sym in self.futures_symbols:
                        spot_sym = self.futures_to_spot[f_sym]
                        try:
                            # 1. Open Interest
                            oi_url = f"https://fapi.binance.com/fapi/v1/openInterest?symbol={f_sym}"
                            async with session.get(oi_url) as resp:
                                if resp.status == 200:
                                    oi_data = await resp.json()
                                    oi_val = float(oi_data.get("openInterest", 0.0))
                                    # 2. Premium Index & Funding
                                    p_url = f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={f_sym}"
                                    async with session.get(p_url) as p_resp:
                                        if p_resp.status == 200:
                                            p_data = await p_resp.json()
                                            mark_p = float(p_data.get("markPrice", 0.0))
                                            funding_r = float(p_data.get("lastFundingRate", 0.0))
                                            next_t = int(p_data.get("nextFundingTime", 0))

                                            self.oi_engine.update(spot_sym, mark_p, oi_val)
                                            self.funding_tracker.update_funding(spot_sym, funding_r, next_t)
                        except Exception:
                            pass
            except asyncio.CancelledError:
                break
            except Exception:
                pass

            await asyncio.sleep(30)


async def poll_htf_candles(
    traders: Dict[str, PaperTrader],
    symbols: List[str],
) -> None:
    """Periodically fetch latest closed 4h candle to keep HTF macro trend state fresh."""
    connector = aiohttp.TCPConnector(limit=5)
    timeout = aiohttp.ClientTimeout(total=10)
    while True:
        try:
            await asyncio.sleep(900)  # Check every 15 minutes
            async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
                for sym in symbols:
                    trader = traders.get(sym)
                    if not trader or not getattr(trader, "htf_trend_filter", False):
                        continue
                    url = f"https://api.binance.com/api/v3/klines?symbol={sym.upper()}&interval=4h&limit=2"
                    try:
                        async with session.get(url) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                if data:
                                    k = data[0]
                                    candle_tuple = (
                                        float(k[1]),
                                        float(k[2]),
                                        float(k[3]),
                                        float(k[4]),
                                        float(k[5]),
                                    )
                                    trader.on_htf_candle(candle_tuple)
                    except Exception:
                        pass
        except asyncio.CancelledError:
            break
        except Exception:
            await asyncio.sleep(60)


def print_multi_coin_radar(
    traders: Dict[str, PaperTrader],
    risk_manager: PortfolioRiskManager,
    timeframe: str,
    latest_prices: Optional[Dict[str, float]] = None,
    micro_mgr: Optional[FuturesMicrostructureManager] = None,
) -> None:
    """Print a high-density, synchronized terminal radar matrix."""
    summary = risk_manager.get_portfolio_summary()
    open_count = risk_manager.get_open_positions_count()
    max_positions = risk_manager.max_open_positions

    print("=" * 142)
    print(
        f" [RADAR] BINANCE MULTI-COIN SCANNER ({timeframe}) [STRATEGY V2.0] | "
        f"ACTIVE TRADES: {open_count}/{max_positions} | "
        f"CLOSED TRADES: {summary['total_trades']} | "
        f"WIN RATE: {summary['win_rate_pct']:.1f}% | "
        f"NET PNL: {summary['total_net_return_pct']:+.2f}%"
    )
    print("=" * 142)
    print(
        f"{'SYMBOL':<10} | {'PRICE ($)':<12} | {'RSI':<6} | {'TREND':<9} | "
        f"{'SMC FVG':<14} | {'POSITION STATUS':<38} | {'OI REGIME':<15} | {'FUNDING APR':<18}"
    )
    print("-" * 142)

    for sym, trader in traders.items():
        if not trader.candles:
            continue

        latest_candle = trader.candles[-1]
        price = (
            latest_prices.get(sym, latest_candle[3])
            if latest_prices and sym in latest_prices
            else latest_candle[3]
        )
        closes = [c[3] for c in trader.candles]

        # Calculate live RSI
        rsi_val = (
            trader.rsi_history[-1]
            if trader.rsi_history
            else calculate_rsi(closes, period=14)
        )

        # Calculate live Trend
        trend = "NEUTRAL"
        if len(closes) >= 200:
            ema_50 = calculate_ema(closes, 50)
            ema_200 = calculate_ema(closes, 200)
            if price > ema_50 and ema_50 > ema_200:
                trend = "BULLISH"
            elif price < ema_50 and ema_50 < ema_200:
                trend = "BEARISH"

        htf_trend = trader.get_htf_trend() if getattr(trader, "htf_trend_filter", False) else None
        trend_disp = f"{trend[:4]}/{htf_trend[:4]}" if htf_trend else trend

        # FVG Status
        has_bull = check_recent_bullish_fvg(list(trader.candles), lookback=5)
        has_bear = check_recent_bearish_fvg(list(trader.candles), lookback=5)
        if has_bull and has_bear:
            fvg_str = "BULL_&_BEAR"
        elif has_bull:
            fvg_str = "BULLISH_FVG"
        elif has_bear:
            fvg_str = "BEARISH_FVG"
        else:
            fvg_str = "NONE"

        # Position status
        active = trader.get_active_trade()
        if active:
            side = active["side"]
            entry = active["entry_price"]
            sl = active["stop_loss"]
            tp = active["take_profit"]
            unrealized_pct = ((price - entry) / entry * 100.0) if side == "LONG" else ((entry - price) / entry * 100.0)
            pos_str = f"{side} @ {format_price(entry)} ({unrealized_pct:+.2f}%) [SL: {format_price(sl)}]"
        else:
            # Check cooldown
            if risk_manager.is_symbol_in_cooldown(sym, trader.bar_count):
                pos_str = "FLAT (Cooldown Active)"
            else:
                pos_str = "FLAT (Scanning)"

        # Microstructure metrics
        if micro_mgr and micro_mgr.is_online:
            oi_regime = micro_mgr.oi_engine.get_regime(sym)
            apr_val, cd_val = micro_mgr.funding_tracker.get_summary(sym)
            funding_str = f"{apr_val:+.2f}% [{cd_val}]" if cd_val != "--:--:--" else "N/A"
        else:
            oi_regime = "N/A"
            funding_str = "N/A"

        formatted_p = format_price(price)
        print(
            f"{sym:<10} | {formatted_p:>12} | {rsi_val:>5.1f}  | {trend_disp:<9} | "
            f"{fvg_str:<14} | {pos_str:<38} | {oi_regime:<15} | {funding_str:<18}"
        )

    print("=" * 142 + "\n")


async def run_multiplexed_stream(
    traders: Dict[str, PaperTrader],
    risk_manager: PortfolioRiskManager,
    symbols: List[str],
    timeframe: str,
    micro_mgr: Optional[FuturesMicrostructureManager] = None,
) -> None:
    """Connect to the multiplexed Binance WebSocket stream with auto-reconnection."""
    ws_url = get_combined_stream_url(symbols, timeframe)
    backoff = 3
    latest_prices: Dict[str, float] = {}
    radar_task: Optional[asyncio.Task] = None

    async def debounced_radar_print(delay: float = 0.25):
        try:
            await asyncio.sleep(delay)
            print_multi_coin_radar(
                traders,
                risk_manager,
                timeframe,
                latest_prices=latest_prices,
                micro_mgr=micro_mgr,
            )
        except asyncio.CancelledError:
            pass

    while True:
        try:
            print(f"[WebSocket] Connecting to multiplexed stream across {len(symbols)} coins...")
            async with websockets.connect(
                ws_url,
                ping_interval=20,
                ping_timeout=20,
            ) as ws:
                print(f"[WebSocket] Connected! Streaming {len(symbols)} coins. Waiting for candle close events...\n")
                backoff = 3

                # Render initial baseline matrix
                print_multi_coin_radar(
                    traders,
                    risk_manager,
                    timeframe,
                    latest_prices=latest_prices,
                    micro_mgr=micro_mgr,
                )

                async for message in ws:
                    payload = json.loads(message)

                    # Extract data from combined stream wrapper
                    data = payload.get("data", payload)
                    kline = data.get("k", {})
                    symbol = (data.get("s") or kline.get("s") or "").upper()

                    if not symbol or symbol not in traders:
                        continue

                    # Update real-time live tick price cache
                    curr_c = float(kline.get("c") or 0.0)
                    if curr_c > 0.0:
                        latest_prices[symbol] = curr_c

                    # Only evaluate full strategy when the candle closes
                    if kline.get("x") is True:
                        candle_tuple = (
                            float(kline["o"]),
                            float(kline["h"]),
                            float(kline["l"]),
                            float(kline["c"]),
                            float(kline["v"]),
                        )
                        timestamp = str(kline.get("t") or time.time())

                        # Route to symbol's dedicated PaperTrader instance
                        traders[symbol].on_candle(candle_tuple, timestamp=timestamp)

                        # Debounce multi-coin radar matrix update (250ms cancellation window)
                        if radar_task and not radar_task.done():
                            radar_task.cancel()
                        radar_task = asyncio.create_task(debounced_radar_print(0.25))

        except asyncio.CancelledError:
            if radar_task and not radar_task.done():
                radar_task.cancel()
            print("[WebSocket] Stream cancelled. Shutting down cleanly.")
            break
        except Exception as e:
            if radar_task and not radar_task.done():
                radar_task.cancel()
            print(f"[WebSocket] Error: {e}. Reconnecting in {backoff} seconds...")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


async def main() -> None:
    args = parse_arguments()
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    timeframe = args.timeframe
    db_path = "data/signals_audit.db"

    # Initialize centralized Portfolio Risk Manager
    risk_manager = PortfolioRiskManager(
        max_open_positions=args.max_positions,
        symbol_cooldown_bars=args.cooldown,
        db_path=db_path,
    )

    # Initialize Futures Microstructure Manager
    micro_mgr = FuturesMicrostructureManager(symbols=symbols)

    # Initialize dedicated PaperTrader state machines for each coin with Strategy v2.0
    traders: Dict[str, PaperTrader] = {
        sym: PaperTrader(
            symbol=sym,
            timeframe=timeframe,
            db_path=db_path,
            risk_manager=risk_manager,
            micro_mgr=micro_mgr,
            adaptive_sl_floor=True,
            htf_trend_filter=True,
            require_market_structure=not args.no_market_structure,
        )
        for sym in symbols
    }

    # Parallel REST Warmup for Spot candles and 4h macro candles
    await warmup_all_traders(traders, symbols, timeframe)

    is_futures_ok, probe_msg = await micro_mgr.check_health()
    if is_futures_ok:
        print(f"[Futures Telemetry] {probe_msg}")
    else:
        print(f"[Futures Telemetry] {probe_msg} -> Running in Spot-only mode.")

    # Concurrently stream Spot + Futures + poll REST metrics + poll 4h HTF candles
    tasks = [
        run_multiplexed_stream(traders, risk_manager, symbols, timeframe, micro_mgr=micro_mgr),
        poll_htf_candles(traders, symbols),
    ]
    if micro_mgr.is_online:
        tasks.append(micro_mgr.run_ws_stream())
        tasks.append(micro_mgr.poll_rest_metrics())

    await asyncio.gather(*tasks)


if __name__ == "__main__":
    args = parse_arguments()
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    db_path = "data/signals_audit.db"

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[Application] Shutdown requested by user.")

        # Instantiate risk manager to extract portfolio summary
        risk_manager = PortfolioRiskManager(
            max_open_positions=args.max_positions,
            symbol_cooldown_bars=args.cooldown,
            db_path=db_path,
        )
        summary = risk_manager.get_portfolio_summary()

        # Checkpoint WAL
        risk_manager.checkpoint()

        print("\n" + "=" * 65)
        print("[AUDIT] PORTFOLIO MULTI-COIN AUDIT SUMMARY")
        print("=" * 65)
        print(f"Monitored Universe:  {len(symbols)} coins ({', '.join(symbols[:5])}...)")
        print(f"Total Trades:        {summary['total_trades']}")
        print(f"Win Rate:            {summary['win_rate_pct']:.1f}% ({summary['winning_trades']}W / {summary['losing_trades']}L)")
        print(f"Net Portfolio PnL:   {summary['total_net_return_pct']:+.2f}%")
        print(f"Avg R-Multiple:      {summary['avg_r_multiple']:.2f}R")
        print(f"Symbols Traded:      {', '.join(summary['symbols_traded']) if summary['symbols_traded'] else 'None'}")
        print("=" * 65 + "\n")

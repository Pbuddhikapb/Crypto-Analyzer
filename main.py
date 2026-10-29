import argparse
import asyncio
import json
import time
from typing import Any, Dict, List, Optional

import websockets

from indicators import (
    calculate_ema,
    calculate_rsi,
    check_recent_bullish_fvg,
    check_recent_bearish_fvg,
    format_price,
)
import market_feed
from market_feed import (
    DEFAULT_MONITORED_SYMBOLS,
    TOP_10_SYMBOLS,
    CandleFeedBuffer,
    get_combined_stream_url,
    poll_htf_candles,
)
from microstructure import FuturesMicrostructureManager

warmup_symbol = market_feed.warmup_symbol


async def warmup_all_traders(
    traders: Dict[str, Any],
    symbols: List[str],
    timeframe: str,
    timestamps_dict: Optional[Dict[str, List[int]]] = None,
) -> None:
    """Delegate parallel warmup to market_feed while honoring monkeypatches on main.warmup_symbol."""
    return await market_feed.warmup_all_traders(
        traders=traders,
        symbols=symbols,
        timeframe=timeframe,
        timestamps_dict=timestamps_dict,
        warmup_fn=warmup_symbol,
    )


__all__ = [
    "CandleFeedBuffer",
    "DEFAULT_MONITORED_SYMBOLS",
    "FuturesMicrostructureManager",
    "TOP_10_SYMBOLS",
    "get_combined_stream_url",
    "poll_htf_candles",
    "print_multi_coin_radar",
    "warmup_all_traders",
    "warmup_symbol",
]


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
    return parser.parse_args()


def print_multi_coin_radar(
    traders: Dict[str, Any],
    timeframe: str = "15m",
    latest_prices: Optional[Dict[str, float]] = None,
    micro_mgr: Optional[FuturesMicrostructureManager] = None,
) -> None:
    """Print a high-density, synchronized terminal radar matrix."""
    header_stats = "ACTIVE TRADES: 0/3 (EVIDENCE_ONLY) | PAPER TRADING: DISABLED"

    print("=" * 142)
    print(
        f" [RADAR] BINANCE MULTI-COIN SCANNER ({timeframe}) [STRATEGY V2.0] | {header_stats}"
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
            if getattr(trader, "rsi_history", None)
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

        # Position status (terminal operates in evidence-only mode)
        pos_str = "FLAT (Evidence Only)"

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
    traders: Dict[str, Any],
    symbols: Optional[List[str]] = None,
    timeframe: str = "15m",
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
                timeframe=timeframe,
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
                    timeframe=timeframe,
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

    # Initialize Futures Microstructure Manager
    micro_mgr = FuturesMicrostructureManager(symbols=symbols)

    # Initialize dedicated CandleFeedBuffer instances for each coin (evidence-status mode)
    traders: Dict[str, CandleFeedBuffer] = {
        sym: CandleFeedBuffer(
            symbol=sym,
            timeframe=timeframe,
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
        run_multiplexed_stream(traders, symbols, timeframe, micro_mgr=micro_mgr),
        poll_htf_candles(traders, symbols),
    ]
    if micro_mgr.is_online:
        tasks.append(micro_mgr.run_ws_stream())
        tasks.append(micro_mgr.poll_rest_metrics())

    await asyncio.gather(*tasks)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[Application] Shutdown requested by user.")
        print("Radar scanner cleanly stopped.")

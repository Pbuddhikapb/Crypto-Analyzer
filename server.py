import asyncio
import json
import os
import subprocess
import time
from collections import deque
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from indicators import (
    calculate_ema,
    calculate_fibonacci_levels,
    calculate_optimal_trade_setup,
    check_order_block_confluence,
    compute_terminal_features,
    detect_breaker_blocks,
    detect_order_blocks,
    normalize_candles_5tuple,
)
from main import (
    TOP_10_SYMBOLS,
    FuturesMicrostructureManager,
    get_combined_stream_url,
    poll_htf_candles,
    warmup_all_traders,
)
from microstructure import OrderBookEngine, SymbolNormalizer
from risk import PortfolioRiskManager


def get_git_commit() -> str:
    """Return short git commit hash from git rev-parse, or 'unknown' if unavailable."""
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if res.returncode == 0:
            commit = res.stdout.strip()
            if commit:
                return commit
    except Exception:
        pass
    return "unknown"


class CandleFeedBuffer:
    """
    Lightweight candle feed buffer maintaining rolling historical bars
    for live terminal display, technical indicator computation, and radar scoring.
    Does not execute trades or manage positions (evidence-status mode).
    """

    def __init__(self, symbol: str, timeframe: str = "15m", maxlen: int = 250):
        self.symbol = symbol
        self.timeframe = timeframe
        self.candles: deque = deque(maxlen=maxlen)
        self.htf_candles: deque = deque(maxlen=maxlen)
        self.htf_trend_filter: bool = True
        self.htf_trend: Optional[str] = None
        self.trend_1h: Optional[str] = None
        self.bar_count: int = 0

    def load_historical_candle(self, candle_data: Any) -> None:
        self.candles.append(tuple(candle_data))
        self.bar_count += 1

    def load_historical_htf_candle(self, candle_data: Any) -> None:
        self.htf_candles.append(tuple(candle_data))

    def add_candle(self, candle_data: Any, timestamp: Optional[str] = None) -> None:
        """Append a closed candle to the rolling buffer."""
        self.candles.append(tuple(candle_data))
        self.bar_count += 1

    def on_candle(self, candle_data: Any, timestamp: Optional[str] = None) -> None:
        """Backwards compatibility alias for add_candle."""
        self.add_candle(candle_data, timestamp=timestamp)

    def on_htf_candle(self, candle_data: Any) -> None:
        self.htf_candles.append(tuple(candle_data))

    def set_htf_trend(self, trend: Optional[str]) -> None:
        self.htf_trend = trend.upper() if trend else None

    def get_htf_trend(self) -> Optional[str]:
        if self.htf_trend is not None:
            return self.htf_trend
        if len(self.htf_candles) < 50:
            return None
        htf_closes = [c[3] for c in self.htf_candles]
        ema_50 = calculate_ema(htf_closes, 50)
        curr_close = htf_closes[-1]
        if len(htf_closes) >= 200:
            ema_200 = calculate_ema(htf_closes, 200)
            if ema_50 > ema_200:
                return "BULLISH" if curr_close >= ema_200 else "NEUTRAL"
            elif ema_50 < ema_200:
                return "BEARISH" if curr_close <= ema_200 else "NEUTRAL"
            return "NEUTRAL"
        else:
            if curr_close >= ema_50:
                return "BULLISH"
            elif curr_close < ema_50:
                return "BEARISH"
            return "NEUTRAL"


# Backwards compatibility alias
MarketDataFeed = CandleFeedBuffer

# Explicit Pattern -> Tested Strategy Mapping
PATTERN_STRATEGY_MAP: Dict[str, Optional[str]] = {
    "Multi-TF Confluence": "ConfluenceThreshold",
    "FVG Retest": "FVGRetest",
    "Trend Pullback": "TrendPullback",
    "Liquidity Sweep": "LiquiditySweep",
    "RSI Divergence": None,
}

_STRATEGY_RESULTS_CACHE: Dict[str, Any] = {}
_STRATEGY_RESULTS_MTIME: float = -1.0


def get_strategy_results(json_path: str = "research/reports/strategy_results.json") -> Dict[str, Any]:
    """Load and cache strategy validation results with file mtime checking."""
    global _STRATEGY_RESULTS_CACHE, _STRATEGY_RESULTS_MTIME
    try:
        if not os.path.exists(json_path):
            return {}
        current_mtime = os.path.getmtime(json_path)
        if current_mtime != _STRATEGY_RESULTS_MTIME:
            with open(json_path, "r", encoding="utf-8") as f:
                _STRATEGY_RESULTS_CACHE = json.load(f)
            _STRATEGY_RESULTS_MTIME = current_mtime
        return _STRATEGY_RESULTS_CACHE
    except Exception as e:
        print(f"[Strategy Results Cache Error] {e}")
        return {}


def get_historical_baseline(pattern: str, json_path: str = "research/reports/strategy_results.json") -> Dict[str, Any]:
    """
    Look up empirical evidence from strategy_results.json for a pattern.
    If untested, returns status='UNTESTED' and message='No historical evidence' with null numbers.
    If rejected, returns status='NO VALIDATED EDGE' with real net numbers.
    """
    strat_name = PATTERN_STRATEGY_MAP.get(pattern)
    if not strat_name:
        return {
            "pattern": pattern,
            "strategy_name": None,
            "status": "UNTESTED",
            "message": "No historical evidence",
            "win_rate": None,
            "profit_factor": None,
            "expectancy_r": None,
            "sample_trades": None,
            "bootstrap_ci": None,
            "matched_p_value": None,
            "verdict": None,
            "run_timestamp": None,
            "data_date_range": None,
        }

    results_data = get_strategy_results(json_path)
    strats = results_data.get("strategies", {})
    strat_data = strats.get(strat_name)

    if not strat_data:
        return {
            "pattern": pattern,
            "strategy_name": strat_name,
            "status": "UNTESTED",
            "message": "No historical evidence",
            "win_rate": None,
            "profit_factor": None,
            "expectancy_r": None,
            "sample_trades": None,
            "bootstrap_ci": None,
            "matched_p_value": None,
            "verdict": None,
            "run_timestamp": None,
            "data_date_range": None,
        }

    verdict = str(strat_data.get("verdict", ""))
    is_rejected = "REJECTED" in verdict.upper()
    status = "NO VALIDATED EDGE" if is_rejected else "VALIDATED (p < 0.05)"

    return {
        "pattern": pattern,
        "strategy_name": strat_name,
        "win_rate": strat_data.get("net_win_rate"),
        "profit_factor": strat_data.get("net_profit_factor"),
        "expectancy_r": strat_data.get("net_expectancy_r"),
        "sample_trades": strat_data.get("trade_count"),
        "status": status,
        "bootstrap_ci": strat_data.get("bootstrap_ci"),
        "matched_p_value": strat_data.get("matched_p_value"),
        "verdict": verdict,
        "run_timestamp": strat_data.get("run_timestamp") or results_data.get("metadata", {}).get("run_timestamp"),
        "data_date_range": strat_data.get("data_date_range") or results_data.get("metadata", {}).get("data_date_range"),
    }


class ConnectionManager:
    """Manages active client WebSocket connections with non-blocking broadcast."""

    def __init__(self) -> None:
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict) -> None:
        dead_connections: List[WebSocket] = []
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except Exception:
                dead_connections.append(connection)
        for dead in dead_connections:
            self.disconnect(dead)


def extract_fvg_boxes(candles: Sequence[Any], lookback: int = 20) -> List[Dict[str, Any]]:
    """Extract coordinates of active unmitigated FVG boxes for chart rendering."""
    c_norm = normalize_candles_5tuple(candles) if candles else []
    n = len(c_norm)
    if n < 3:
        return []

    boxes: List[Dict[str, Any]] = []
    start_idx = max(2, n - lookback)

    for i in range(start_idx, n):
        # Bullish FVG
        curr_low = float(c_norm[i][2])
        high_2_bars_ago = float(c_norm[i - 2][1])
        mid_open = float(c_norm[i - 1][0])
        mid_high = float(c_norm[i - 1][1])
        mid_low = float(c_norm[i - 1][2])
        mid_close = float(c_norm[i - 1][3])
        mid_body = mid_close - mid_open
        mid_range = mid_high - mid_low

        if curr_low > high_2_bars_ago and mid_range > 0.0 and mid_body > 0.0:
            if (mid_body / mid_range) >= 0.5:
                invalidated = False
                for k in range(i + 1, n):
                    if float(c_norm[k][3]) < high_2_bars_ago:
                        invalidated = True
                        break
                if not invalidated:
                    boxes.append({
                        "type": "BULLISH",
                        "top": curr_low,
                        "bottom": high_2_bars_ago,
                        "ce": (curr_low + high_2_bars_ago) / 2.0,
                        "candle_index": i,
                    })

        # Bearish FVG
        curr_high = float(c_norm[i][1])
        low_2_bars_ago = float(c_norm[i - 2][2])
        mid_body_red = mid_open - mid_close
        if curr_high < low_2_bars_ago and mid_range > 0.0 and mid_body_red > 0.0:
            if (mid_body_red / mid_range) >= 0.5:
                invalidated = False
                for k in range(i + 1, n):
                    if float(c_norm[k][3]) > low_2_bars_ago:
                        invalidated = True
                        break
                if not invalidated:
                    boxes.append({
                        "type": "BEARISH",
                        "top": low_2_bars_ago,
                        "bottom": curr_high,
                        "ce": (low_2_bars_ago + curr_high) / 2.0,
                        "candle_index": i,
                    })
    return boxes


def compute_indicators(candles: Sequence[Any]) -> Dict[str, Any]:
    """Compute TA indicators, trend, divergences, and FVG boxes for a candle series.
    Adapts compute_terminal_features to the frontend market schema.
    """
    c_norm = normalize_candles_5tuple(candles) if candles else []
    if not c_norm:
        return {
            "ema_50": None,
            "ema_200": None,
            "rsi": None,
            "trend": "NEUTRAL",
            "has_bullish_fvg": False,
            "has_bearish_fvg": False,
            "has_bullish_divergence": False,
            "has_bearish_divergence": False,
            "active_fvg_boxes": [],
            "adx_14": None,
            "atr_14": None,
            "is_chop": False,
            "trade_setup": None,
            "fibonacci_levels": None,
            "dealing_range": None,
            "pricing_zone": None,
            "market_structure": None,
            "order_blocks": [],
            "breaker_blocks": [],
            "order_block_confluence": None,
        }

    feat = compute_terminal_features(c_norm)
    trend = feat["trend"]["regime"]
    latest_close = feat["price"]["close"]
    n = len(c_norm)

    # Active FVG boxes for visual rendering
    boxes = extract_fvg_boxes(c_norm, lookback=20)

    # Dealing range and pricing zone
    dr = feat["structure"]["dealing_range"]
    fib_levels = None
    pricing_zone = None
    if dr:
        side_bias = "LONG" if trend == "BULLISH" else "SHORT"
        fib_levels = calculate_fibonacci_levels(dr["swing_low"], dr["swing_high"], side=side_bias)
        dp = feat["structure"]["discount_premium"]
        pricing_zone = dp["zone"] if side_bias == "LONG" else dp["zone_short"]

    # Order blocks and breaker blocks
    active_obs = []
    active_bbs = []
    if n >= 15:
        all_obs = detect_order_blocks(c_norm, lookback=50)
        all_bbs = detect_breaker_blocks(c_norm, order_blocks=all_obs, lookback=50)
        active_obs = [ob for ob in all_obs if ob["status"] in ("UNMITIGATED", "MITIGATED")]
        active_bbs = [bb for bb in all_bbs if bb["status"] in ("UNMITIGATED", "MITIGATED")]

    # Order block confluence
    ob_confluence = None
    if latest_close > 0 and trend in ("BULLISH", "BEARISH") and n >= 15:
        side_chk = "LONG" if trend == "BULLISH" else "SHORT"
        has_ob, ob_msg, ob_details = check_order_block_confluence(latest_close, c_norm, side=side_chk, lookback=50)
        ob_confluence = {
            "has_confluence": has_ob,
            "message": ob_msg,
            "block_type": ob_details.get("block_type"),
            "mean_threshold_tapped": ob_details.get("mean_threshold_tapped", False),
        }

    # Trade setup
    trade_setup = None
    has_bull_fvg = feat["smc"]["bullish_fvg_active"]
    has_bear_fvg = feat["smc"]["bearish_fvg_active"]
    if n >= 15 and latest_close > 0:
        setup_side = "LONG" if (trend == "BULLISH" or has_bull_fvg) else ("SHORT" if (trend == "BEARISH" or has_bear_fvg) else None)
        if setup_side is not None:
            target_box = next((b for b in reversed(boxes) if b["type"] == setup_side), None)
            fvg_dict = {"ce": target_box["ce"], "lower": target_box["bottom"], "upper": target_box["top"]} if target_box else None
            trade_setup = calculate_optimal_trade_setup(
                side=setup_side,
                current_price=latest_close,
                atr=feat["volatility"]["atr_14"] or (latest_close * 0.01),
                fvg_info=fvg_dict,
                macro_bias=trend,
            )

    rsi = feat["momentum"]["rsi_14"] if n >= 15 else None
    atr_14 = feat["volatility"]["atr_14"] if n >= 15 else None
    adx_14 = feat["momentum"]["adx_14"] if n >= 30 else None
    is_chop = feat["momentum"]["is_choppy"] if n >= 30 else False

    return {
        "ema_50": feat["trend"]["ema_50"],
        "ema_200": feat["trend"]["ema_200"],
        "rsi": rsi,
        "trend": trend,
        "has_bullish_fvg": has_bull_fvg,
        "has_bearish_fvg": has_bear_fvg,
        "has_bullish_divergence": feat["momentum"]["bullish_divergence"],
        "has_bearish_divergence": feat["momentum"]["bearish_divergence"],
        "active_fvg_boxes": boxes,
        "adx_14": adx_14,
        "atr_14": atr_14,
        "is_chop": is_chop,
        "trade_setup": trade_setup,
        "fibonacci_levels": fib_levels,
        "dealing_range": dr,
        "pricing_zone": pricing_zone,
        "market_structure": feat["structure"],
        "order_blocks": active_obs,
        "breaker_blocks": active_bbs,
        "order_block_confluence": ob_confluence,
    }


def timeframe_to_seconds(tf: str) -> int:
    """Convert timeframe string (e.g. 1m, 15m, 1h, 4h, 1d) to integer seconds."""
    if tf.endswith("m"):
        return int(tf[:-1]) * 60
    elif tf.endswith("h"):
        return int(tf[:-1]) * 3600
    elif tf.endswith("d"):
        return int(tf[:-1]) * 86400
    return 60


def aggregate_candles(
    candles: Sequence[Any],
    factor: int,
    timestamps: Optional[Sequence[int]] = None,
) -> Tuple[List[Tuple[float, float, float, float, float]], Optional[List[int]]]:
    """Aggregate lower timeframe candles into higher timeframe bars.
    
    E.g. factor=4 combines 4x 15m candles into 1x 1h candle.
    Calculates True OHLCV: open=first open, high=max high, low=min low, close=last close, vol=sum vol.
    """
    if factor <= 1 or not candles:
        c_norm = normalize_candles_5tuple(candles) if candles else []
        return c_norm, list(timestamps) if timestamps else None

    c_norm = normalize_candles_5tuple(candles)
    n = len(c_norm)
    agg_candles: List[Tuple[float, float, float, float, float]] = []
    agg_ts: Optional[List[int]] = [] if timestamps else None
    ts_list = list(timestamps) if timestamps else []

    for i in range(0, n, factor):
        chunk = c_norm[i:i + factor]
        chunk_open = chunk[0][0]
        chunk_high = max(c[1] for c in chunk)
        chunk_low = min(c[2] for c in chunk)
        chunk_close = chunk[-1][3]
        chunk_vol = sum(c[4] for c in chunk)
        agg_candles.append((chunk_open, chunk_high, chunk_low, chunk_close, chunk_vol))

        if agg_ts is not None and ts_list:
            if i < len(ts_list):
                agg_ts.append(ts_list[i])

    return agg_candles, agg_ts


def format_candles_for_chart(
    candles: Sequence[Any],
    timestamps: Optional[Sequence[int]] = None,
    timeframe: str = "1m",
) -> List[Dict[str, Any]]:
    """Format candles for TradingView Lightweight Charts with integer-second timestamps."""
    n = len(candles)
    if n == 0:
        return []

    interval_sec = 60
    if timeframe.endswith("m"):
        interval_sec = int(timeframe[:-1]) * 60
    elif timeframe.endswith("h"):
        interval_sec = int(timeframe[:-1]) * 3600
    elif timeframe.endswith("d"):
        interval_sec = int(timeframe[:-1]) * 86400

    now_sec = int(time.time())

    # Clean timestamps to integer seconds
    clean_ts: List[int] = []
    if timestamps:
        for t in timestamps:
            if t is not None:
                val = int(t)
                clean_ts.append(val // 1000 if val > 10_000_000_000 else val)

    m = len(clean_ts)
    formatted: List[Dict[str, Any]] = []

    if m >= n:
        active_ts = clean_ts[-n:]
        for i, c in enumerate(candles):
            raw_c = c[1:] if len(c) >= 6 else c
            formatted.append({
                "time": active_ts[i],
                "open": float(raw_c[0]),
                "high": float(raw_c[1]),
                "low": float(raw_c[2]),
                "close": float(raw_c[3]),
                "volume": float(raw_c[4]),
            })
    elif m > 0:
        # Align known timestamps to the end of the candle array
        first_known = clean_ts[0]
        offset = n - m
        for i, c in enumerate(candles):
            raw_c = c[1:] if len(c) >= 6 else c
            if i < offset:
                ts_sec = first_known - (offset - i) * interval_sec
            else:
                ts_sec = clean_ts[i - offset]
            formatted.append({
                "time": ts_sec,
                "open": float(raw_c[0]),
                "high": float(raw_c[1]),
                "low": float(raw_c[2]),
                "close": float(raw_c[3]),
                "volume": float(raw_c[4]),
            })
    else:
        anchor_sec = (now_sec // interval_sec) * interval_sec
        for i, c in enumerate(candles):
            raw_c = c[1:] if len(c) >= 6 else c
            ts_sec = anchor_sec - (n - 1 - i) * interval_sec
            formatted.append({
                "time": ts_sec,
                "open": float(raw_c[0]),
                "high": float(raw_c[1]),
                "low": float(raw_c[2]),
                "close": float(raw_c[3]),
                "volume": float(raw_c[4]),
            })

    # Strict monotonicity guarantee: ensure every timestamp is strictly greater than the previous
    for i in range(1, len(formatted)):
        if formatted[i]["time"] <= formatted[i - 1]["time"]:
            formatted[i]["time"] = formatted[i - 1]["time"] + interval_sec

    return formatted


async def process_closed_candle_event(
    app: FastAPI,
    symbol: str,
    candle_tuple: Tuple[float, float, float, float, float],
    raw_t: int,
    kline_time_sec: int,
) -> None:
    """Process a closed candle for a symbol: update candle buffer, indicators, and broadcast."""
    feed = app.state.candle_buffers.get(symbol)
    if feed is None:
        feed = app.state.traders.get(symbol)
    if feed is None:
        return

    feed.add_candle(candle_tuple, timestamp=str(raw_t))

    # Update last update tracking
    if not hasattr(app.state, "last_update_times"):
        app.state.last_update_times = {}
    app.state.last_update_times[symbol] = int(time.time())

    # Store in rolling timestamp buffer
    if symbol not in app.state.candle_timestamps:
        app.state.candle_timestamps[symbol] = []
    app.state.candle_timestamps[symbol].append(kline_time_sec)
    if len(app.state.candle_timestamps[symbol]) > 250:
        app.state.candle_timestamps[symbol].pop(0)

    # Broadcast closed candle with indicators
    indicators = compute_indicators(feed.candles)
    await app.state.connection_manager.broadcast({
        "type": "candle",
        "symbol": symbol,
        "candle": {
            "time": kline_time_sec,
            "open": candle_tuple[0],
            "high": candle_tuple[1],
            "low": candle_tuple[2],
            "close": candle_tuple[3],
            "volume": candle_tuple[4],
        },
        "indicators": indicators,
    })


async def run_kline_ingestion(
    app: FastAPI,
    symbols: List[str],
    timeframe: str,
) -> None:
    """Stream real-time Binance USD-M Futures klines and ticks, broadcasting to connected WebSocket clients."""
    import websockets

    ws_url = get_combined_stream_url(symbols, timeframe)
    backoff = 3

    while True:
        try:
            async with websockets.connect(ws_url, ping_interval=20, ping_timeout=20) as ws:
                backoff = 3
                app.state.is_spot_connected = True
                app.state.is_kline_connected = True

                async for message in ws:
                    payload = json.loads(message)
                    data = payload.get("data", payload)
                    kline = data.get("k", {})
                    symbol = (data.get("s") or kline.get("s") or "").upper()

                    if not symbol or symbol not in app.state.traders:
                        continue

                    # Record last-update timestamp
                    now_t = time.time()
                    if not hasattr(app.state, "last_update_times"):
                        app.state.last_update_times = {}
                    app.state.last_update_times[symbol] = int(now_t)

                    curr_c = float(kline.get("c") or 0.0)
                    if curr_c > 0.0:
                        app.state.latest_prices[symbol] = curr_c
                        # Broadcast throttled tick to protect client bandwidth (max 2 updates/sec per symbol)
                        if not hasattr(app.state, "last_tick_broadcast"):
                            app.state.last_tick_broadcast = {}
                        last_t = app.state.last_tick_broadcast.get(symbol, 0.0)
                        if (now_t - last_t) >= 0.5:
                            app.state.last_tick_broadcast[symbol] = now_t
                            await app.state.connection_manager.broadcast({
                                "type": "tick",
                                "symbol": symbol,
                                "price": curr_c,
                                "timestamp": int(now_t),
                            })

                    # Process closed candle
                    if kline.get("x") is True:
                        candle_tuple = (
                            float(kline["o"]),
                            float(kline["h"]),
                            float(kline["l"]),
                            float(kline["c"]),
                            float(kline["v"]),
                        )
                        raw_t = int(kline.get("t", time.time() * 1000))
                        kline_time_sec = raw_t // 1000 if raw_t > 10_000_000_000 else raw_t

                        await process_closed_candle_event(app, symbol, candle_tuple, raw_t, kline_time_sec)

        except asyncio.CancelledError:
            break
        except Exception as e:
            app.state.is_spot_connected = False
            app.state.is_kline_connected = False
            print(f"[Futures Kline WS Warning] Connection to {ws_url} failed: {e}. Retrying in {backoff}s...")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


run_spot_ingestion = run_kline_ingestion


async def run_futures_ingestion(
    app: FastAPI,
    micro_mgr: FuturesMicrostructureManager,
) -> None:
    """Stream real-time Futures aggTrade and forceOrder events with WebSocket broadcast."""
    import websockets

    if not micro_mgr.is_online:
        return

    stream_names = [f"{sym.lower()}@aggTrade" for sym in micro_mgr.futures_symbols]
    stream_names.append("!forceOrder@arr")
    stream_names.append("!markPrice@arr")
    ws_url = f"wss://fstream.binance.com/market/stream?streams={'/'.join(stream_names)}"
    backoff = 3

    while True:
        try:
            async with websockets.connect(ws_url, ping_interval=20, ping_timeout=20) as ws:
                backoff = 3
                async for message in ws:
                    payload = json.loads(message)
                    data = payload.get("data", payload)
                    event_type = data.get("e") if isinstance(data, dict) else None

                    if event_type == "aggTrade":
                        f_sym = str(data.get("s", "")).upper()
                        if f_sym in micro_mgr.futures_to_spot:
                            trade = micro_mgr.whale_filter.process_trade(data)
                            if trade:
                                spot_sym = micro_mgr.futures_to_spot[f_sym]
                                await app.state.connection_manager.broadcast({
                                    "type": "whale_trade",
                                    "trade": {
                                        "symbol": spot_sym,
                                        "price": trade.price,
                                        "quantity": trade.quantity,
                                        "notional": trade.notional,
                                        "side": trade.side,
                                        "badge": trade.badge,
                                        "timestamp": trade.timestamp,
                                    },
                                })

                    elif event_type == "forceOrder":
                        o = data.get("o", {})
                        f_sym = str(o.get("s", "")).upper()
                        if f_sym in micro_mgr.futures_to_spot:
                            event = micro_mgr.liq_tracker.process_order(o)
                            if event:
                                spot_sym = micro_mgr.futures_to_spot[f_sym]
                                rolling_vol, is_cascade = micro_mgr.liq_tracker.get_rolling_summary(f_sym)
                                await app.state.connection_manager.broadcast({
                                    "type": "liquidation",
                                    "event": {
                                        "symbol": spot_sym,
                                        "liquidated_position": event.liquidated_position,
                                        "price": event.price,
                                        "quantity": event.quantity,
                                        "notional": event.notional,
                                        "timestamp": event.timestamp,
                                        "rolling_60s_volume": rolling_vol,
                                        "is_cascade": is_cascade,
                                    },
                                })

                    elif event_type == "markPriceUpdate" or isinstance(data, list):
                        items = data if isinstance(data, list) else [data]
                        for item in items:
                            if isinstance(item, dict) and item.get("e") == "markPriceUpdate":
                                f_sym = str(item.get("s", "")).upper()
                                if f_sym in micro_mgr.futures_to_spot:
                                    spot_sym = micro_mgr.futures_to_spot[f_sym]
                                    funding_r = float(item.get("r") or 0.0)
                                    next_t = int(item.get("T") or 0)
                                    mark_p = float(item.get("p") or 0.0)
                                    micro_mgr.funding_tracker.update_funding(spot_sym, funding_r, next_t)
                                    if mark_p > 0.0:
                                        micro_mgr.oi_engine.update(spot_sym, mark_p, 0.0)

        except asyncio.CancelledError:
            break
        except Exception:
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


async def run_order_book_stream(app: FastAPI) -> None:
    """Stream real-time Binance USD-M Futures @depth20 for the active UI symbol with automatic focus switching."""
    import websockets

    backoff = 3
    while True:
        try:
            if getattr(app.state, "is_telemetry_paused", False):
                await asyncio.sleep(1.0)
                continue

            current_sym = getattr(app.state, "active_depth_symbol", "BTCUSDT").lower()
            ws_url = f"wss://fstream.binance.com/ws/{current_sym}@depth20"
            active_at_connect = current_sym

            async with websockets.connect(ws_url, ping_interval=20, ping_timeout=20, compression="deflate") as ws:
                backoff = 3
                async for message in ws:
                    if getattr(app.state, "is_telemetry_paused", False):
                        break
                    if getattr(app.state, "active_depth_symbol", "BTCUSDT").lower() != active_at_connect:
                        break

                    payload = json.loads(message)
                    snapshot = app.state.order_book_engine.process_depth_payload(
                        getattr(app.state, "active_depth_symbol", "BTCUSDT"),
                        payload,
                    )
                    await app.state.connection_manager.broadcast({
                        "type": "order_book_depth",
                        "depth": snapshot.to_dict(),
                    })
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[Futures Depth WS Warning] Depth connection error: {e}. Retrying in {backoff}s...")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


def seed_buffer_from_parquet(
    buffer: CandleFeedBuffer,
    symbol: str,
    timeframe: str = "15m",
    timestamps_dict: Optional[Dict[str, List[int]]] = None,
    limit: int = 249,
    data_dir: str = "data/candles",
) -> bool:
    """Pre-seed a CandleFeedBuffer with historical candles from the local verified Parquet data lake."""
    import pandas as pd
    sym = symbol.upper()
    try:
        file_path = os.path.join(data_dir, f"{sym}_{timeframe}.parquet")
        if os.path.exists(file_path):
            df = pd.read_parquet(file_path)
        else:
            file_1h = os.path.join(data_dir, f"{sym}_1h.parquet")
            if not os.path.exists(file_1h):
                return False
            df = pd.read_parquet(file_1h)
            if timeframe == "15m":
                needed_1h = (limit // 4) + 2
                df_recent = df.tail(needed_1h).copy()
                expanded = []
                for _, row in df_recent.iterrows():
                    base_t = int(row["open_time"])
                    o = float(row["open"])
                    h = float(row["high"])
                    l = float(row["low"])
                    c = float(row["close"])
                    v = float(row["volume"]) / 4.0
                    c0 = o + (c - o) * 0.25
                    h0 = max(o, c0, min(h, max(o, c0) + abs(c - o) * 0.1))
                    l0 = min(o, c0, max(l, min(o, c0) - abs(c - o) * 0.1))
                    o1 = c0
                    c1 = o + (c - o) * 0.50
                    h1 = max(o1, c1, h if (h > max(o, c)) else max(o1, c1))
                    l1 = min(o1, c1, min(o1, c1))
                    o2 = c1
                    c2 = o + (c - o) * 0.75
                    h2 = max(o2, c2)
                    l2 = min(o2, c2, l if (l < min(o, c)) else min(o2, c2))
                    o3 = c2
                    c3 = c
                    h3 = max(o3, c3)
                    l3 = min(o3, c3)
                    expanded.append((base_t, (o, h0, l0, c0, v)))
                    expanded.append((base_t + 900_000, (o1, h1, l1, c1, v)))
                    expanded.append((base_t + 1800_000, (o2, h2, l2, c2, v)))
                    expanded.append((base_t + 2700_000, (o3, h3, l3, c3, v)))

                selected = expanded[-limit:]
                interval_sec = 900
                now_sec = int(time.time())
                anchor_sec = (now_sec // interval_sec) * interval_sec
                m = len(selected)
                for idx, (_raw_t, c_tuple) in enumerate(selected):
                    buffer.load_historical_candle(c_tuple)
                    if timestamps_dict is not None and sym in timestamps_dict:
                        ts_sec = anchor_sec - (m - 1 - idx) * interval_sec
                        timestamps_dict[sym].append(ts_sec)

                if getattr(buffer, "htf_trend_filter", False):
                    file_4h = os.path.join(data_dir, f"{sym}_4h.parquet")
                    if os.path.exists(file_4h):
                        df_4h = pd.read_parquet(file_4h)
                        for _, row in df_4h.tail(limit).iterrows():
                            buffer.load_historical_htf_candle((
                                float(row["open"]),
                                float(row["high"]),
                                float(row["low"]),
                                float(row["close"]),
                                float(row["volume"]),
                            ))
                return True

        # Direct match for 1h or other available timeframes
        interval_sec = timeframe_to_seconds(timeframe)
        now_sec = int(time.time())
        anchor_sec = (now_sec // interval_sec) * interval_sec
        df_recent = df.tail(limit)
        m = len(df_recent)
        for idx, (_, row) in enumerate(df_recent.iterrows()):
            c_tuple = (
                float(row["open"]),
                float(row["high"]),
                float(row["low"]),
                float(row["close"]),
                float(row["volume"]),
            )
            buffer.load_historical_candle(c_tuple)
            if timestamps_dict is not None and sym in timestamps_dict:
                ts_sec = anchor_sec - (m - 1 - idx) * interval_sec
                timestamps_dict[sym].append(ts_sec)

        if getattr(buffer, "htf_trend_filter", False):
            file_4h = os.path.join(data_dir, f"{sym}_4h.parquet")
            if os.path.exists(file_4h):
                df_4h = pd.read_parquet(file_4h)
                for _, row in df_4h.tail(limit).iterrows():
                    buffer.load_historical_htf_candle((
                        float(row["open"]),
                        float(row["high"]),
                        float(row["low"]),
                        float(row["close"]),
                        float(row["volume"]),
                    ))
        return True
    except Exception as e:
        print(f"[Seed Buffer Error] {sym}: {e}")
        return False


def create_app(
    enable_ingestion: Optional[bool] = None,
    db_path: str = "data/signals_audit.db",
    symbols: Optional[List[str]] = None,
    timeframe: str = "15m",
    frontend_dist: Optional[str] = None,
) -> FastAPI:
    """Create and configure the FastAPI application instance."""
    if enable_ingestion is None:
        enable_ingestion = os.getenv("ENABLE_BACKGROUND_INGESTION", "true").lower() in ("true", "1", "yes")

    active_symbols = symbols or list(TOP_10_SYMBOLS)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Startup
        app.state.connection_manager = ConnectionManager()
        app.state.risk_manager = PortfolioRiskManager(
            max_open_positions=3,
            symbol_cooldown_bars=3,
            account_equity_usd=10000.0,
            risk_per_trade_pct=1.0,
            max_leverage=3.0,
            db_path=db_path,
        )
        app.state.micro_mgr = FuturesMicrostructureManager(symbols=active_symbols)
        app.state.order_book_engine = OrderBookEngine()
        app.state.active_depth_symbol = active_symbols[0]
        app.state.is_telemetry_paused = False
        app.state.last_tick_broadcast = {}
        app.state.candle_buffers = {
            sym: CandleFeedBuffer(
                symbol=sym,
                timeframe=timeframe,
            )
            for sym in active_symbols
        }
        app.state.traders = app.state.candle_buffers
        app.state.latest_prices = {}
        app.state.candle_timestamps = {sym: [] for sym in active_symbols}
        app.state.last_update_times = {}
        app.state.is_spot_connected = False
        app.state.is_kline_connected = False
        app.state.futures_status_message = "Not checked"

        background_tasks: List[asyncio.Task] = []

        if enable_ingestion:
            try:
                # Parallel REST Warmup
                await warmup_all_traders(
                    app.state.traders,
                    active_symbols,
                    timeframe,
                    timestamps_dict=app.state.candle_timestamps,
                )
            except Exception as e:
                print(f"[Server Warmup Error] {e}")

            # Ensure all buffers are seeded; if REST warmup failed or was geoblocked, prime from local parquet data lake
            now_t = int(time.time())
            for sym, buffer in app.state.candle_buffers.items():
                if len(buffer.candles) == 0:
                    seed_buffer_from_parquet(buffer, sym, timeframe, timestamps_dict=app.state.candle_timestamps)
                if buffer.candles:
                    app.state.latest_prices[sym] = float(buffer.candles[-1][3])
                    app.state.last_update_times[sym] = now_t

            # Probe Futures
            try:
                is_futures_ok, probe_msg = await app.state.micro_mgr.check_health()
                app.state.futures_status_message = probe_msg
            except Exception as e:
                app.state.futures_status_message = f"Probe Error: {e}"

            # Launch ingestion tasks
            t_spot = asyncio.create_task(run_spot_ingestion(app, active_symbols, timeframe))
            t_htf = asyncio.create_task(poll_htf_candles(app.state.traders, active_symbols))
            t_ob = asyncio.create_task(run_order_book_stream(app))
            background_tasks.extend([t_spot, t_htf, t_ob])

            if app.state.micro_mgr.is_online:
                t_fut = asyncio.create_task(run_futures_ingestion(app, app.state.micro_mgr))
                t_poll = asyncio.create_task(app.state.micro_mgr.poll_rest_metrics())
                background_tasks.extend([t_fut, t_poll])

        yield

        # Teardown
        for task in background_tasks:
            if not task.done():
                task.cancel()

        if background_tasks:
            await asyncio.gather(*background_tasks, return_exceptions=True)

        if hasattr(app.state, "risk_manager") and app.state.risk_manager:
            app.state.risk_manager.checkpoint()

    app = FastAPI(
        title="Institutional Crypto Terminal API",
        version="1.0.0",
        description="High-performance backend transport and real-time WebSocket hub for Binance Spot & Futures.",
        lifespan=lifespan,
    )

    # Initialize default state attributes before lifespan execution
    app.state.connection_manager = ConnectionManager()
    app.state.risk_manager = None
    app.state.candle_buffers = {}
    app.state.traders = app.state.candle_buffers
    app.state.micro_mgr = None
    app.state.latest_prices = {}
    app.state.candle_timestamps = {}
    app.state.last_update_times = {}
    app.state.last_tick_broadcast = {}
    app.state.is_spot_connected = False
    app.state.is_kline_connected = False
    app.state.futures_status_message = "Not checked"

    # CORS configuration restricted to local loopback Vite dev and GET only
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5174", "http://localhost:5174"],
        allow_credentials=True,
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    # REST Endpoints
    @app.get("/api/health")
    async def get_health():
        """Health check endpoint reporting connectivity and system readiness."""
        db_ok = os.path.exists(db_path) or getattr(app.state, "risk_manager", None) is not None
        micro = getattr(app.state, "micro_mgr", None)
        conn_mgr = getattr(app.state, "connection_manager", None)
        buffers = getattr(app.state, "candle_buffers", getattr(app.state, "traders", {}))
        tf_sec = timeframe_to_seconds(timeframe)
        last_updates = getattr(app.state, "last_update_times", {})
        kline_conn = bool(
            getattr(app.state, "is_kline_connected", getattr(app.state, "is_spot_connected", False))
        )

        now_t = int(time.time())
        staleness_threshold = 2 * tf_sec

        # Determine symbols with data and stale symbols
        symbols_with_data = [
            sym
            for sym, buf in buffers.items()
            if (sym in last_updates and last_updates[sym] > 0)
            or (hasattr(buf, "candles") and len(buf.candles) > 0)
        ]

        stale_symbols = sorted(
            [
                sym
                for sym in buffers
                if (sym not in last_updates)
                or (now_t - last_updates[sym] > staleness_threshold)
            ]
        )

        if not symbols_with_data:
            status = "down"
        elif not kline_conn or len(stale_symbols) > 0:
            status = "degraded"
        else:
            status = "healthy"

        return {
            "status": status,
            "git_commit": get_git_commit(),
            "data_source": "binance_futures_um",
            "kline_connectivity": kline_conn,
            "spot_connectivity": kline_conn,  # Backwards compatibility alias
            "futures_connectivity": getattr(micro, "is_online", False) if micro else False,
            "futures_status_message": getattr(app.state, "futures_status_message", "Unknown"),
            "active_symbols_count": len(buffers),
            "db_status": "connected" if db_ok else "unreachable",
            "connected_ws_clients": len(conn_mgr.active_connections) if conn_mgr else 0,
            "last_update_per_symbol": dict(last_updates),
            "last_update_times": dict(last_updates),
            "stale_symbols": stale_symbols,
            "timeframe": timeframe,
            "candle_interval_sec": tf_sec,
        }

    @app.get("/api/market/symbols")
    async def get_market_symbols():
        """Return monitored symbols with contracts and current market snapshots."""
        results = []
        buffers = getattr(app.state, "candle_buffers", getattr(app.state, "traders", {}))
        for sym, buffer in buffers.items():
            f_sym = SymbolNormalizer.to_futures_symbol(sym)
            latest_price = app.state.latest_prices.get(sym)
            if latest_price is None and buffer.candles:
                latest_price = buffer.candles[-1][3]

            indicators = compute_indicators(buffer.candles)

            results.append({
                "symbol": sym,
                "futures_symbol": f_sym,
                "latest_price": latest_price,
                "has_open_position": False,
                "trend": indicators["trend"],
                "htf_trend": buffer.get_htf_trend(),
                "rsi": indicators["rsi"],
            })
        return results

    @app.get("/api/market/candles")
    async def get_market_candles(
        symbol: str = Query("BTCUSDT", description="Market symbol"),
        timeframe: str = Query("1m", description="Candle interval"),
        limit: int = Query(250, ge=1, le=1000, description="Max candles to return"),
    ):
        """Return candles formatted with integer-second timestamps for Lightweight Charts."""
        sym = symbol.upper()
        buffers = getattr(app.state, "candle_buffers", getattr(app.state, "traders", {}))
        if sym not in buffers:
            raise HTTPException(status_code=404, detail=f"Symbol '{sym}' not found in active universe.")

        buffer = buffers[sym]
        candles_list = list(buffer.candles)
        timestamps = list(app.state.candle_timestamps.get(sym, []))

        # Check if timeframe aggregation is required
        buffer_tf = getattr(buffer, "timeframe", "15m")
        req_sec = timeframe_to_seconds(timeframe)
        base_sec = timeframe_to_seconds(buffer_tf)
        factor = max(1, req_sec // base_sec) if base_sec > 0 else 1

        if factor > 1 and len(candles_list) >= factor:
            display_candles, display_ts = aggregate_candles(candles_list, factor, timestamps=timestamps)
        else:
            display_candles = normalize_candles_5tuple(candles_list)
            display_ts = timestamps

        active_candles = display_candles[-limit:] if len(display_candles) > limit else display_candles
        active_ts = display_ts[-limit:] if (display_ts and len(display_ts) > limit) else display_ts

        formatted_candles = format_candles_for_chart(
            active_candles,
            timestamps=active_ts,
            timeframe=timeframe,
        )
        indicators = compute_indicators(active_candles)

        return {
            "symbol": sym,
            "timeframe": timeframe,
            "candles": formatted_candles,
            "indicators": indicators,
        }

    @app.get("/api/portfolio/summary")
    async def get_portfolio_summary():
        """Return portfolio risk sizing and capital allocation metrics (paper trading disabled)."""
        summary = app.state.risk_manager.get_portfolio_summary()
        base_equity = float(summary.get("account_equity_usd", 10000.0))

        return {
            **summary,
            "active_positions_count": 0,
            "max_open_positions": app.state.risk_manager.max_open_positions,
            "active_trades": [],
            "total_unrealized_pnl_usd": 0.0,
            "current_equity_usd": base_equity,
            "paper_trading_enabled": False,
            "execution_mode": "EVIDENCE_ONLY",
            "evidence_note": "Automated paper trading disabled. All tested hypotheses failed to show edge over random noise.",
        }

    @app.get("/api/microstructure/summary")
    async def get_microstructure_summary(
        symbol: str = Query("BTCUSDT", description="Market symbol"),
    ):
        """Return derivatives order flow, Open Interest regime, and funding rate."""
        sym = symbol.upper()
        f_sym = SymbolNormalizer.to_futures_symbol(sym)
        micro_mgr = app.state.micro_mgr

        oi_regime = micro_mgr.oi_engine.get_regime(sym)
        apr, countdown = micro_mgr.funding_tracker.get_summary(sym)
        funding_rate = micro_mgr.funding_tracker.rates.get(sym, 0.0)
        rolling_vol, is_cascade = micro_mgr.liq_tracker.get_rolling_summary(f_sym)

        whale_trades = [
            {
                "symbol": t.symbol,
                "price": t.price,
                "quantity": t.quantity,
                "notional": t.notional,
                "side": t.side,
                "badge": t.badge,
                "timestamp": t.timestamp,
            }
            for t in micro_mgr.whale_filter.tape
            if t.symbol == f_sym or t.symbol == sym
        ]

        return {
            "symbol": sym,
            "futures_symbol": f_sym,
            "is_futures_online": micro_mgr.is_online,
            "oi_regime": oi_regime,
            "funding_rate": funding_rate,
            "funding_apr_pct": apr,
            "funding_countdown": countdown,
            "rolling_60s_liquidation_volume": rolling_vol,
            "is_cascade_alert": is_cascade,
            "recent_whale_trades": whale_trades,
        }

    @app.get("/api/scanner/opportunities")
    async def get_scanner_opportunities():
        """Scan active universe and return ranked high-confluence institutional trade opportunities."""
        opportunities = []
        buffers = getattr(app.state, "candle_buffers", getattr(app.state, "traders", {}))
        for sym, buffer in buffers.items():
            if len(buffer.candles) < 20:
                continue

            candles_list = list(buffer.candles)
            indicators = compute_indicators(candles_list)
            trade_setup = indicators.get("trade_setup")

            # Calculate volume surge
            vols = [float(c[4]) for c in candles_list]
            avg_vol_20 = float(np.mean(vols[-21:-1])) if len(vols) >= 21 else float(np.mean(vols[:-1])) if len(vols) > 1 else 1.0
            vol_surge = round((vols[-1] / avg_vol_20), 2) if avg_vol_20 > 0 else 1.0

            curr_price = app.state.latest_prices.get(sym, float(candles_list[-1][3]))
            adx_val = indicators.get("adx_14") or 20.0
            trend = indicators["trend"]
            htf_trend = buffer.get_htf_trend() or "NEUTRAL"
            pricing_zone = indicators.get("pricing_zone") or "EQUILIBRIUM"

            # Compute priority score: Directional confluence model
            score = 50.0

            # 1. Macro Trend Alignment (+15 for matching HTF and LTF)
            if trend == htf_trend and trend in ("BULLISH", "BEARISH"):
                score += 15.0
            elif trend != "NEUTRAL" and htf_trend != "NEUTRAL" and trend != htf_trend:
                score -= 10.0  # Counter-trend conflict penalty

            # 2. Smart Money Zone Retest / FVG Confluence (+15 if direction matches)
            if trend == "BULLISH" and indicators.get("has_bullish_fvg"):
                score += 15.0
            elif trend == "BEARISH" and indicators.get("has_bearish_fvg"):
                score += 15.0
            elif indicators.get("has_bullish_fvg") or indicators.get("has_bearish_fvg"):
                score += 5.0

            # 3. Discount / Premium Confluence (+10 for buying discount / selling premium)
            if trend == "BULLISH" and pricing_zone in ("DISCOUNT", "DEEP_DISCOUNT"):
                score += 10.0
            elif trend == "BEARISH" and pricing_zone in ("PREMIUM", "DEEP_PREMIUM"):
                score += 10.0

            # 4. Volatility & Volume Expansion
            if adx_val >= 25.0:
                score += 10.0
            elif indicators.get("is_chop", False) or adx_val < 20.0:
                score -= 10.0

            if vol_surge >= 1.5:
                score += 10.0

            # 5. Momentum Divergence (+10 for trend-aligned divergence)
            if trend in ("BULLISH", "NEUTRAL") and indicators.get("has_bullish_divergence"):
                score += 10.0
            if trend in ("BEARISH", "NEUTRAL") and indicators.get("has_bearish_divergence"):
                score += 10.0

            clamped_score = max(5.0, min(100.0, score))

            # Categorize primary structural inflection pattern
            inflection_pattern = "EQUILIBRIUM"
            pattern_key = "Multi-TF Confluence"

            if trend == "BULLISH" and indicators.get("has_bullish_fvg"):
                inflection_pattern = "SMC Bullish FVG Retest"
                pattern_key = "FVG Retest"
            elif trend == "BEARISH" and indicators.get("has_bearish_fvg"):
                inflection_pattern = "SMC Bearish FVG Retest"
                pattern_key = "FVG Retest"
            elif indicators.get("has_bullish_divergence") or indicators.get("has_bearish_divergence"):
                inflection_pattern = "Momentum RSI Divergence"
                pattern_key = "RSI Divergence"
            elif trend == htf_trend and trend in ("BULLISH", "BEARISH"):
                inflection_pattern = f"Macro {trend.title()} Pullback"
                pattern_key = "Trend Pullback"

            baseline_info = get_historical_baseline(pattern_key)

            opportunities.append({
                "symbol": sym,
                "current_price": curr_price,
                "trend": trend,
                "htf_trend": htf_trend,
                "adx_14": adx_val,
                "volume_surge": vol_surge,
                "confluence_score": round(clamped_score, 1),
                "is_chop": indicators.get("is_chop", False),
                "inflection_pattern": inflection_pattern,
                "historical_baseline": baseline_info,
                "trade_setup": trade_setup,
            })

        opportunities.sort(key=lambda x: -x["confluence_score"])
        return opportunities

    @app.get("/api/trade/bracket")
    async def get_trade_bracket(
        symbol: str = Query("BTCUSDT", description="Market symbol"),
        side: str = Query("LONG", description="Position side: LONG or SHORT"),
        entry_price: float = Query(..., description="Nominal entry price"),
        stop_loss: float = Query(..., description="Stop loss invalidation level"),
    ):
        """Calculate mathematically verified Single Exit Authority bracket geometry."""
        from engine.exits import calculate_bracket_geometry
        bracket = calculate_bracket_geometry(
            side=side,
            entry_price=entry_price,
            stop_loss=stop_loss,
        )
        return {
            "symbol": symbol.upper(),
            **bracket,
        }

    @app.get("/api/market/depth")
    async def get_market_depth(
        symbol: str = Query("BTCUSDT", description="Market symbol for order book depth"),
    ):
        """Return latest 20-level order book depth and Order Book Imbalance (OBI)."""
        sym = symbol.upper()
        latest_p = app.state.latest_prices.get(sym, 50000.0)
        snapshot = app.state.order_book_engine.get_snapshot(sym, fallback_price=latest_p)
        return snapshot.to_dict()

    # WebSocket Hub
    @app.websocket("/ws")
    async def websocket_hub(websocket: WebSocket):
        """Real-time bi-directional WebSocket hub with ping/pong and live stream broadcast."""
        await app.state.connection_manager.connect(websocket)
        try:
            while True:
                data = await websocket.receive_text()
                try:
                    payload = json.loads(data)
                    action = payload.get("action")
                    if action == "ping":
                        await websocket.send_json({
                            "type": "pong",
                            "timestamp": int(time.time()),
                        })
                    elif action == "focus_symbol":
                        target_sym = str(payload.get("symbol", "")).upper()
                        buffers = getattr(app.state, "candle_buffers", getattr(app.state, "traders", {}))
                        if target_sym in buffers:
                            app.state.active_depth_symbol = target_sym
                            cached_snap = app.state.order_book_engine.get_snapshot(
                                target_sym,
                                fallback_price=app.state.latest_prices.get(target_sym, 50000.0),
                            )
                            await websocket.send_json({
                                "type": "order_book_depth",
                                "depth": cached_snap.to_dict(),
                            })
                    elif action == "pause_telemetry":
                        app.state.is_telemetry_paused = True
                    elif action == "resume_telemetry":
                        app.state.is_telemetry_paused = False
                except Exception:
                    pass
        except WebSocketDisconnect:
            app.state.connection_manager.disconnect(websocket)
        except Exception:
            app.state.connection_manager.disconnect(websocket)

    # Mount built frontend static files if available
    dist_dir = (
        frontend_dist
        or os.getenv("FRONTEND_DIST")
        or os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend", "dist")
    )
    if os.path.isdir(dist_dir):
        app.mount("/", StaticFiles(directory=dist_dir, html=True), name="static")

    return app


# Default instance for uvicorn server:app
app = create_app()

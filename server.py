import asyncio
import json
import os
import sys
import time
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from indicators import (
    calculate_adx,
    calculate_atr,
    calculate_ema,
    calculate_fibonacci_levels,
    calculate_optimal_trade_setup,
    calculate_rsi,
    check_order_block_confluence,
    check_recent_bearish_fvg,
    check_recent_bullish_fvg,
    classify_discount_premium,
    compute_terminal_features,
    detect_breaker_blocks,
    detect_market_structure,
    detect_order_blocks,
    detect_rsi_divergences,
    format_price,
    get_active_dealing_range,
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
from trader import PaperTrader


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
    """Process a closed candle for a symbol: on_candle lifecycle, indicators, and broadcasts."""
    trader = app.state.traders.get(symbol)
    if trader is None:
        return

    prev_trade = trader.get_active_trade()
    trader.on_candle(candle_tuple, timestamp=str(raw_t))
    new_trade = trader.get_active_trade()

    # Store in rolling timestamp buffer
    if symbol not in app.state.candle_timestamps:
        app.state.candle_timestamps[symbol] = []
    app.state.candle_timestamps[symbol].append(kline_time_sec)
    if len(app.state.candle_timestamps[symbol]) > 250:
        app.state.candle_timestamps[symbol].pop(0)

    # Broadcast trade events
    if prev_trade is None and new_trade is not None:
        await app.state.connection_manager.broadcast({
            "type": "trade_opened",
            "trade": new_trade,
        })
    elif prev_trade is not None and new_trade is None:
        await app.state.connection_manager.broadcast({
            "type": "trade_closed",
            "symbol": symbol,
        })

    # Broadcast closed candle with indicators
    indicators = compute_indicators(trader.candles)
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


async def queue_candidate_candle(
    app: FastAPI,
    symbol: str,
    candle_tuple: Tuple[float, float, float, float, float],
    raw_t: int,
    kline_time_sec: int,
) -> None:
    """Queue idle symbol closed candle in 1.0s batching buffer to rank by conviction."""
    if not hasattr(app.state, "candle_batch_queue"):
        app.state.candle_batch_queue = {}
        app.state.candle_batch_task = None

    app.state.candle_batch_queue[symbol] = {
        "symbol": symbol,
        "candle_tuple": candle_tuple,
        "raw_t": raw_t,
        "kline_time_sec": kline_time_sec,
    }

    if app.state.candle_batch_task is None or app.state.candle_batch_task.done():
        async def flush_batch():
            await asyncio.sleep(1.0)
            batch = getattr(app.state, "candle_batch_queue", {})
            app.state.candle_batch_queue = {}
            if not batch:
                return

            # Sort candidate idle symbols descending by estimated confluence score
            scored_candidates = []
            for sym, item in batch.items():
                t = app.state.traders.get(sym)
                score = t.estimate_confluence_priority(item["candle_tuple"]) if t and hasattr(t, "estimate_confluence_priority") else 0.0
                scored_candidates.append((score, sym, item))

            scored_candidates.sort(key=lambda x: x[0], reverse=True)

            # Process candidates in priority order
            for score, sym, item in scored_candidates:
                # Check priority preemption if portfolio is at capacity and score is elite (>= 85.0)
                rm = getattr(app.state, "risk_manager", None)
                if rm is not None and getattr(rm, "allow_preemption", False) and score >= getattr(rm, "preemption_min_candidate_score", 85.0):
                    preempt_sym, pre_reason = rm.find_preemption_candidate(app.state.traders, score)
                    if preempt_sym:
                        stagnant_t = app.state.traders.get(preempt_sym)
                        if stagnant_t and stagnant_t.active_trade is not None:
                            curr_p = app.state.latest_prices.get(preempt_sym, stagnant_t.active_trade["entry_price"])
                            preempt_snap = stagnant_t.preempt_trade(curr_p, exit_timestamp=str(item["raw_t"]))
                            if preempt_snap:
                                await app.state.connection_manager.broadcast({
                                    "type": "trade_preempted",
                                    "symbol": preempt_sym,
                                    "replaced_by": sym,
                                    "trade": preempt_snap,
                                })

                await process_closed_candle_event(
                    app,
                    sym,
                    item["candle_tuple"],
                    item["raw_t"],
                    item["kline_time_sec"],
                )

        app.state.candle_batch_task = asyncio.create_task(flush_batch())


async def run_spot_ingestion(
    app: FastAPI,
    symbols: List[str],
    timeframe: str,
) -> None:
    """Stream real-time Spot klines and ticks, broadcasting to connected WebSocket clients."""
    import websockets

    stream_urls = [
        get_combined_stream_url(symbols, timeframe),
        f"wss://stream.binance.us:9443/stream?streams={'/'.join(f'{s.strip().lower()}@kline_{timeframe}' for s in symbols)}",
    ]
    url_idx = 0
    backoff = 3

    while True:
        try:
            ws_url = stream_urls[url_idx % len(stream_urls)]
            async with websockets.connect(ws_url, ping_interval=20, ping_timeout=20) as ws:
                backoff = 3
                app.state.is_spot_connected = True

                async for message in ws:
                    payload = json.loads(message)
                    data = payload.get("data", payload)
                    kline = data.get("k", {})
                    symbol = (data.get("s") or kline.get("s") or "").upper()

                    if not symbol or symbol not in app.state.traders:
                        continue

                    curr_c = float(kline.get("c") or 0.0)
                    if curr_c > 0.0:
                        app.state.latest_prices[symbol] = curr_c
                        # Broadcast throttled tick to protect client bandwidth (max 2 updates/sec per symbol)
                        now_t = time.time()
                        last_t = app.state.last_tick_broadcast.get(symbol, 0.0)
                        if (now_t - last_t) >= 0.5:
                            app.state.last_tick_broadcast[symbol] = now_t
                            await app.state.connection_manager.broadcast({
                                "type": "tick",
                                "symbol": symbol,
                                "price": curr_c,
                                "timestamp": int(now_t),
                            })

                        # Sub-second intra-candle exit monitoring (TP1, TP2, BE, SL)
                        trader = app.state.traders.get(symbol)
                        if trader is not None and trader.active_trade is not None:
                            closed_snapshot = trader.on_tick(curr_c, timestamp=str(kline.get("t", int(time.time() * 1000))))
                            if closed_snapshot is not None:
                                await app.state.connection_manager.broadcast({
                                    "type": "trade_closed",
                                    "symbol": symbol,
                                    "trade": closed_snapshot,
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

                        trader = app.state.traders.get(symbol)
                        if trader is not None and trader.active_trade is not None:
                            # Pass 1: Active trade exit evaluations process immediately
                            await process_closed_candle_event(app, symbol, candle_tuple, raw_t, kline_time_sec)
                        else:
                            # Pass 2: Idle candidate setup - buffer for 1.0s to rank by confluence score
                            await queue_candidate_candle(app, symbol, candle_tuple, raw_t, kline_time_sec)

        except asyncio.CancelledError:
            break
        except Exception as e:
            app.state.is_spot_connected = False
            if "451" in str(e) or (hasattr(e, "status_code") and getattr(e, "status_code", 0) == 451):
                url_idx += 1
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


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
    ws_url = f"wss://fstream.binance.com/market/stream?streams={'/'.join(stream_names)}"
    backoff = 3

    while True:
        try:
            async with websockets.connect(ws_url, ping_interval=20, ping_timeout=20) as ws:
                backoff = 3
                async for message in ws:
                    payload = json.loads(message)
                    data = payload.get("data", payload)
                    event_type = data.get("e")

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

        except asyncio.CancelledError:
            break
        except Exception:
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


async def run_order_book_stream(app: FastAPI) -> None:
    """Stream real-time @depth20 (1s update rate) for the active UI symbol with automatic focus switching."""
    import websockets

    ws_hosts = ["stream.binance.com:9443", "stream.binance.us:9443"]
    host_idx = 0
    backoff = 3
    while True:
        try:
            if getattr(app.state, "is_telemetry_paused", False):
                await asyncio.sleep(1.0)
                continue

            current_sym = getattr(app.state, "active_depth_symbol", "BTCUSDT").lower()
            current_host = ws_hosts[host_idx % len(ws_hosts)]
            ws_url = f"wss://{current_host}/ws/{current_sym}@depth20"
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
            if "451" in str(e) or (hasattr(e, "status_code") and getattr(e, "status_code", 0) == 451):
                host_idx += 1
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


def create_app(
    enable_ingestion: Optional[bool] = None,
    db_path: str = "data/signals_audit.db",
    symbols: Optional[List[str]] = None,
    timeframe: str = "15m",
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
        app.state.traders = {
            sym: PaperTrader(
                symbol=sym,
                timeframe=timeframe,
                db_path=db_path,
                risk_manager=app.state.risk_manager,
                micro_mgr=app.state.micro_mgr,
                adaptive_sl_floor=True,
                htf_trend_filter=True,
                require_market_structure=True,
                require_fibonacci_ote=True,
                enable_order_blocks=True,
                enable_collinearity_cap=True,
                order_book_engine=app.state.order_book_engine,
            )
            for sym in active_symbols
        }
        app.state.latest_prices = {}
        app.state.candle_timestamps = {sym: [] for sym in active_symbols}
        app.state.is_spot_connected = False
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
                for sym, trader in app.state.traders.items():
                    if trader.candles:
                        app.state.latest_prices[sym] = float(trader.candles[-1][3])
            except Exception as e:
                print(f"[Server Warmup Error] {e}")

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
    app.state.traders = {}
    app.state.micro_mgr = None
    app.state.latest_prices = {}
    app.state.candle_timestamps = {}
    app.state.is_spot_connected = False
    app.state.futures_status_message = "Not checked"

    # CORS configuration for Vite and local dev
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # REST Endpoints
    @app.get("/api/health")
    async def get_health():
        """Health check endpoint reporting connectivity and system readiness."""
        db_ok = os.path.exists(db_path) or getattr(app.state, "risk_manager", None) is not None
        micro = getattr(app.state, "micro_mgr", None)
        conn_mgr = getattr(app.state, "connection_manager", None)
        traders = getattr(app.state, "traders", {})
        return {
            "status": "healthy",
            "spot_connectivity": getattr(app.state, "is_spot_connected", False),
            "futures_connectivity": getattr(micro, "is_online", False) if micro else False,
            "futures_status_message": getattr(app.state, "futures_status_message", "Unknown"),
            "active_symbols_count": len(traders),
            "db_status": "connected" if db_ok else "unreachable",
            "connected_ws_clients": len(conn_mgr.active_connections) if conn_mgr else 0,
        }

    @app.get("/api/market/symbols")
    async def get_market_symbols():
        """Return monitored symbols with contracts and current market snapshots."""
        results = []
        for sym, trader in app.state.traders.items():
            f_sym = SymbolNormalizer.to_futures_symbol(sym)
            latest_price = app.state.latest_prices.get(sym)
            if latest_price is None and trader.candles:
                latest_price = trader.candles[-1][3]

            indicators = compute_indicators(trader.candles)
            has_open = trader.get_active_trade() is not None

            results.append({
                "symbol": sym,
                "futures_symbol": f_sym,
                "latest_price": latest_price,
                "has_open_position": has_open,
                "trend": indicators["trend"],
                "htf_trend": trader.get_htf_trend(),
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
        if sym not in app.state.traders:
            raise HTTPException(status_code=404, detail=f"Symbol '{sym}' not found in active universe.")

        trader = app.state.traders[sym]
        candles_list = list(trader.candles)
        timestamps = list(app.state.candle_timestamps.get(sym, []))

        # Check if timeframe aggregation is required
        trader_tf = getattr(trader, "timeframe", "15m")
        req_sec = timeframe_to_seconds(timeframe)
        base_sec = timeframe_to_seconds(trader_tf)
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
        """Return comprehensive portfolio performance summary, capital allocation, and active trades."""
        summary = app.state.risk_manager.get_portfolio_summary()
        active_trades = []
        total_unrealized_usd = 0.0

        for sym, trader in app.state.traders.items():
            trade = trader.get_active_trade()
            if trade:
                curr_p = app.state.latest_prices.get(sym, trade["entry_price"])
                side = trade.get("side", "LONG")
                entry = trade["entry_price"]
                sl = trade.get("initial_stop_loss", trade.get("stop_loss", entry))
                risk_dist = abs(entry - sl)
                qty = float(trade.get("quantity") or 0.0)

                unrealized_pct = ((curr_p - entry) / entry * 100.0) if side == "LONG" else ((entry - curr_p) / entry * 100.0)
                unrealized_usd = (qty * (curr_p - entry)) if side == "LONG" else (qty * (entry - curr_p))
                curr_r = ((curr_p - entry) / risk_dist) if side == "LONG" else ((entry - curr_p) / risk_dist) if risk_dist > 0 else 0.0

                total_unrealized_usd += unrealized_usd

                trade_copy = dict(trade)
                trade_copy["unrealized_pnl_pct"] = round(unrealized_pct, 2)
                trade_copy["unrealized_pnl_usd"] = round(unrealized_usd, 2)
                trade_copy["current_r_multiple"] = round(curr_r, 2)
                trade_copy["current_price"] = curr_p
                entry_b = int(trade.get("entry_bar") or trader.bar_count)
                trade_copy["bars_held"] = max(0, trader.bar_count - entry_b)
                trade_copy["max_stagnancy_bars"] = getattr(trader, "max_stagnancy_bars", 12)
                active_trades.append(trade_copy)

        base_equity = float(summary.get("account_equity_usd", 10000.0))
        current_equity = round(base_equity + total_unrealized_usd, 2)

        return {
            **summary,
            "active_positions_count": len(active_trades),
            "max_open_positions": app.state.risk_manager.max_open_positions,
            "active_trades": active_trades,
            "total_unrealized_pnl_usd": round(total_unrealized_usd, 2),
            "current_equity_usd": current_equity,
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
        for sym, trader in app.state.traders.items():
            if len(trader.candles) < 20:
                continue

            candles_list = list(trader.candles)
            indicators = compute_indicators(candles_list)
            trade_setup = indicators.get("trade_setup")

            # Calculate volume surge
            vols = [float(c[4]) for c in candles_list]
            avg_vol_20 = float(np.mean(vols[-21:-1])) if len(vols) >= 21 else float(np.mean(vols[:-1])) if len(vols) > 1 else 1.0
            vol_surge = round((vols[-1] / avg_vol_20), 2) if avg_vol_20 > 0 else 1.0

            curr_price = app.state.latest_prices.get(sym, float(candles_list[-1][3]))
            adx_val = indicators.get("adx_14") or 20.0
            trend = indicators["trend"]
            htf_trend = trader.get_htf_trend() or "NEUTRAL"
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
            baseline_info = {
                "pattern": "Multi-TF Confluence",
                "win_rate": 45.0,
                "profit_factor": 0.90,
                "sample_trades": 2500,
                "status": "SELECTIVE (Requires discretionary discretion)",
            }

            if trend == "BULLISH" and indicators.get("has_bullish_fvg"):
                inflection_pattern = "SMC Bullish FVG Retest"
                baseline_info = {
                    "pattern": "FVG Retest",
                    "win_rate": 45.9,
                    "profit_factor": 0.93,
                    "sample_trades": 3805,
                    "status": "NEUTRAL BASELINE (Order flow confirmation required)",
                }
            elif trend == "BEARISH" and indicators.get("has_bearish_fvg"):
                inflection_pattern = "SMC Bearish FVG Retest"
                baseline_info = {
                    "pattern": "FVG Retest",
                    "win_rate": 45.9,
                    "profit_factor": 0.93,
                    "sample_trades": 3805,
                    "status": "NEUTRAL BASELINE (Order flow confirmation required)",
                }
            elif indicators.get("has_bullish_divergence") or indicators.get("has_bearish_divergence"):
                inflection_pattern = "Momentum RSI Divergence"
                baseline_info = {
                    "pattern": "RSI Divergence",
                    "win_rate": 48.0,
                    "profit_factor": 0.95,
                    "sample_trades": 1800,
                    "status": "REVERSAL SETUP (Check volume absorption)",
                }
            elif trend == htf_trend and trend in ("BULLISH", "BEARISH"):
                inflection_pattern = f"Macro {trend.title()} Pullback"
                baseline_info = {
                    "pattern": "Trend Pullback",
                    "win_rate": 48.7,
                    "profit_factor": 0.85,
                    "sample_trades": 3544,
                    "status": "NEUTRAL BASELINE (Wait for confirmed bounce)",
                }

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
                        if target_sym in app.state.traders:
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

    return app


# Default instance for uvicorn server:app
app = create_app()

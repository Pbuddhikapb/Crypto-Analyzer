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
    detect_breaker_blocks,
    detect_market_structure,
    detect_order_blocks,
    detect_rsi_divergences,
    format_price,
    get_active_dealing_range,
)
from main import (
    TOP_10_SYMBOLS,
    FuturesMicrostructureManager,
    get_combined_stream_url,
    poll_htf_candles,
    warmup_all_traders,
)
from microstructure import SymbolNormalizer
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
    n = len(candles)
    if n < 3:
        return []

    boxes: List[Dict[str, Any]] = []
    start_idx = max(2, n - lookback)

    for i in range(start_idx, n):
        # Bullish FVG
        curr_low = float(candles[i][2])
        high_2_bars_ago = float(candles[i - 2][1])
        mid_open = float(candles[i - 1][0])
        mid_high = float(candles[i - 1][1])
        mid_low = float(candles[i - 1][2])
        mid_close = float(candles[i - 1][3])
        mid_body = mid_close - mid_open
        mid_range = mid_high - mid_low

        if curr_low > high_2_bars_ago and mid_range > 0.0 and mid_body > 0.0:
            if (mid_body / mid_range) >= 0.5:
                invalidated = False
                for k in range(i + 1, n):
                    if float(candles[k][3]) < high_2_bars_ago:
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
        curr_high = float(candles[i][1])
        low_2_bars_ago = float(candles[i - 2][2])
        mid_body_red = mid_open - mid_close
        if curr_high < low_2_bars_ago and mid_range > 0.0 and mid_body_red > 0.0:
            if (mid_body_red / mid_range) >= 0.5:
                invalidated = False
                for k in range(i + 1, n):
                    if float(candles[k][3]) > low_2_bars_ago:
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
    """Compute TA indicators, trend, divergences, and FVG boxes for a candle series."""
    if not candles:
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

    highs = [float(c[1]) for c in candles]
    lows = [float(c[2]) for c in candles]
    closes = [float(c[3]) for c in candles]
    latest_close = closes[-1]
    ema_50 = calculate_ema(closes, 50) if len(closes) >= 50 else None
    ema_200 = calculate_ema(closes, 200) if len(closes) >= 200 else None

    trend = "NEUTRAL"
    if ema_50 is not None and ema_200 is not None:
        if latest_close > ema_50 and ema_50 > ema_200:
            trend = "BULLISH"
        elif latest_close < ema_50 and ema_50 < ema_200:
            trend = "BEARISH"

    rsi = calculate_rsi(closes, period=14) if len(closes) >= 15 else None
    atr_14 = calculate_atr(highs, lows, closes, period=14) if len(closes) >= 15 else None
    adx_14 = calculate_adx(highs, lows, closes, period=14) if len(closes) >= 30 else None
    is_chop = (adx_14 is not None and adx_14 < 25.0)

    has_bull_fvg = check_recent_bullish_fvg(candles, lookback=5)
    has_bear_fvg = check_recent_bearish_fvg(candles, lookback=5)
    bull_div, bear_div = detect_rsi_divergences(candles, period=14, lookback=30)
    boxes = extract_fvg_boxes(candles, lookback=20)

    dr = get_active_dealing_range(candles)
    fib_levels = None
    pricing_zone = None
    if dr:
        side_bias = "LONG" if trend == "BULLISH" else "SHORT"
        fib_levels = calculate_fibonacci_levels(dr["swing_low"], dr["swing_high"], side=side_bias)
        pricing_zone, _ = classify_discount_premium(latest_close, dr["swing_low"], dr["swing_high"], side=side_bias)

    ms = detect_market_structure(candles)

    order_blocks = detect_order_blocks(candles, lookback=50)
    breaker_blocks = detect_breaker_blocks(candles, order_blocks=order_blocks, lookback=50)
    active_obs = [ob for ob in order_blocks if ob["status"] in ("UNMITIGATED", "MITIGATED")]
    active_bbs = [bb for bb in breaker_blocks if bb["status"] in ("UNMITIGATED", "MITIGATED")]

    ob_confluence = None
    if latest_close > 0 and trend in ("BULLISH", "BEARISH"):
        side_chk = "LONG" if trend == "BULLISH" else "SHORT"
        has_ob, ob_msg, ob_details = check_order_block_confluence(latest_close, candles, side=side_chk, lookback=50)
        ob_confluence = {
            "has_confluence": has_ob,
            "message": ob_msg,
            "block_type": ob_details.get("block_type"),
            "mean_threshold_tapped": ob_details.get("mean_threshold_tapped", False),
        }

    trade_setup = None
    if len(closes) >= 15 and latest_close > 0:
        setup_side = "LONG" if (trend == "BULLISH" or has_bull_fvg) else ("SHORT" if (trend == "BEARISH" or has_bear_fvg) else None)
        if setup_side is not None:
            target_box = next((b for b in reversed(boxes) if b["type"] == setup_side), None)
            fvg_dict = {"ce": target_box["ce"], "lower": target_box["bottom"], "upper": target_box["top"]} if target_box else None
            trade_setup = calculate_optimal_trade_setup(
                side=setup_side,
                current_price=latest_close,
                atr=atr_14 or (latest_close * 0.01),
                fvg_info=fvg_dict,
                macro_bias=trend,
            )

    return {
        "ema_50": ema_50,
        "ema_200": ema_200,
        "rsi": rsi,
        "trend": trend,
        "has_bullish_fvg": has_bull_fvg,
        "has_bearish_fvg": has_bear_fvg,
        "has_bullish_divergence": bull_div,
        "has_bearish_divergence": bear_div,
        "active_fvg_boxes": boxes,
        "adx_14": adx_14,
        "atr_14": atr_14,
        "is_chop": is_chop,
        "trade_setup": trade_setup,
        "fibonacci_levels": fib_levels,
        "dealing_range": dr,
        "pricing_zone": pricing_zone,
        "market_structure": ms,
        "order_blocks": active_obs,
        "breaker_blocks": active_bbs,
        "order_block_confluence": ob_confluence,
    }


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

    ws_url = get_combined_stream_url(symbols, timeframe)
    backoff = 3

    while True:
        try:
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
                        # Broadcast tick
                        await app.state.connection_manager.broadcast({
                            "type": "tick",
                            "symbol": symbol,
                            "price": curr_c,
                            "timestamp": int(time.time()),
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
                await warmup_all_traders(app.state.traders, active_symbols, timeframe)
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
            background_tasks.extend([t_spot, t_htf])

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
        candles_list = list(trader.candles)[-limit:]
        timestamps = app.state.candle_timestamps.get(sym, [])[-limit:]

        formatted_candles = format_candles_for_chart(
            candles_list,
            timestamps=timestamps,
            timeframe=timeframe,
        )
        indicators = compute_indicators(candles_list)

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

            # Compute priority score
            score = 50.0
            if indicators.get("has_bullish_fvg") or indicators.get("has_bearish_fvg"):
                score += 20.0
            if adx_val >= 25.0:
                score += 15.0
            if vol_surge >= 1.5:
                score += 10.0
            if indicators.get("has_bullish_divergence") or indicators.get("has_bearish_divergence"):
                score += 15.0

            opportunities.append({
                "symbol": sym,
                "current_price": curr_price,
                "trend": indicators["trend"],
                "htf_trend": trader.get_htf_trend() or "NEUTRAL",
                "adx_14": adx_val,
                "volume_surge": vol_surge,
                "confluence_score": round(score, 1),
                "is_chop": indicators.get("is_chop", False),
                "trade_setup": trade_setup,
            })

        opportunities.sort(key=lambda x: -x["confluence_score"])
        return opportunities

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
                except Exception:
                    pass
        except WebSocketDisconnect:
            app.state.connection_manager.disconnect(websocket)
        except Exception:
            app.state.connection_manager.disconnect(websocket)

    return app


# Default instance for uvicorn server:app
app = create_app()

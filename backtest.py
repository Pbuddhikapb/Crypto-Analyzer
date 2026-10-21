import argparse
import json
import os
import sqlite3
import time
import urllib.request
from collections import deque
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from indicators import (
    calculate_adx,
    calculate_ema,
    calculate_rsi,
    check_bearish_fvg_retest,
    check_bullish_fvg_retest,
    check_recent_bearish_fvg,
    check_recent_bullish_fvg,
    detect_rsi_divergences,
    format_price,
)
from risk import PortfolioRiskManager
from trader import PaperTrader
from microstructure import (
    HistoricalMicrostructureReplayer,
    fetch_historical_funding_rates,
    fetch_historical_open_interest,
)

DEFAULT_PORTFOLIO_SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "BNBUSDT",
    "XRPUSDT",
    "DOGEUSDT",
    "ADAUSDT",
    "AVAXUSDT",
    "LINKUSDT",
    "SUIUSDT",
]

TIER1_ALPHA_SYMBOLS = [
    "BTCUSDT",
    "SOLUSDT",
    "AVAXUSDT",
    "ADAUSDT",
    "XRPUSDT",
]




def timeframe_to_ms(timeframe: str) -> int:
    """Convert Binance timeframe interval string to milliseconds."""
    multipliers = {
        "m": 60 * 1000,
        "h": 3600 * 1000,
        "d": 86400 * 1000,
        "w": 7 * 86400 * 1000,
    }
    unit = timeframe[-1].lower()
    val = int(timeframe[:-1])
    return val * multipliers.get(unit, 60 * 1000)


def fetch_historical_candles(
    symbol: str = "XRPUSDT",
    timeframe: str = "15m",
    total_candles: int = 2500,
    cache_db: str = "data/klines_cache.db",
    force_fresh: bool = False,
) -> List[Tuple[int, float, float, float, float, float]]:
    """Fetch paginated historical klines with persistent local SQLite caching and delta synchronization."""
    os.makedirs(os.path.dirname(cache_db), exist_ok=True)
    sym = symbol.upper()
    interval_ms = timeframe_to_ms(timeframe)
    now_ms = int(time.time() * 1000)

    with sqlite3.connect(cache_db) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS klines_multi_tf (
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                open_time INTEGER NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL NOT NULL,
                PRIMARY KEY (symbol, timeframe, open_time)
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_klines_multi_tf_lookup
            ON klines_multi_tf (symbol, timeframe, open_time ASC)
            """
        )
        conn.commit()

        cursor = conn.cursor()

        if force_fresh:
            cursor.execute(
                "DELETE FROM klines_multi_tf WHERE symbol = ? AND timeframe = ?",
                (sym, timeframe),
            )
            conn.commit()

        # Check existing cached range
        cursor.execute(
            "SELECT MIN(open_time), MAX(open_time), COUNT(*) FROM klines_multi_tf WHERE symbol = ? AND timeframe = ?",
            (sym, timeframe),
        )
        row = cursor.fetchone()
        min_time, max_time, cached_count = row if row else (None, None, 0)
        cached_count = cached_count or 0

        # Step 1: Forward Top-Up (fetch recent closed bars if cached max_time is behind real-time)
        if not force_fresh and cached_count > 0 and max_time is not None:
            if (now_ms - max_time) > (interval_ms * 2):
                curr_start = max_time + 1
                forward_batches = 0
                while curr_start < now_ms:
                    url = f"https://api.binance.com/api/v3/klines?symbol={sym}&interval={timeframe}&startTime={curr_start}&limit=1000"
                    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
                    try:
                        with urllib.request.urlopen(req, timeout=10) as resp:
                            if resp.status == 200:
                                data = json.loads(resp.read().decode())
                                if not data:
                                    break
                                batch = [
                                    (sym, timeframe, int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5]))
                                    for k in data
                                    if int(k[0]) + interval_ms <= now_ms  # only closed bars
                                ]
                                if not batch:
                                    break
                                cursor.executemany(
                                    "INSERT OR IGNORE INTO klines_multi_tf (symbol, timeframe, open_time, open, high, low, close, volume) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                    batch,
                                )
                                conn.commit()
                                forward_batches += 1
                                curr_start = batch[-1][2] + interval_ms
                                if len(data) < 1000:
                                    break
                                time.sleep(0.1)
                            else:
                                break
                    except Exception as e:
                        print(f"  -> Forward sync network notice: {e}")
                        break

                # Re-query count and extents after forward top-up
                cursor.execute(
                    "SELECT MIN(open_time), MAX(open_time), COUNT(*) FROM klines_multi_tf WHERE symbol = ? AND timeframe = ?",
                    (sym, timeframe),
                )
                min_time, max_time, cached_count = cursor.fetchone()
                cached_count = cached_count or 0

        # Step 2: Backward Backfill if cached count is less than requested total_candles
        if cached_count < total_candles:
            needed = total_candles - cached_count
            print(f"\n[DATA INGESTION] SQLite has {cached_count} {timeframe} bars. Backfilling {needed} earlier bars for {sym} from Binance...")
            end_time = (min_time - 1) if (min_time is not None and cached_count > 0) else None

            while needed > 0:
                limit = min(1000, needed)
                url = f"https://api.binance.com/api/v3/klines?symbol={sym}&interval={timeframe}&limit={limit}"
                if end_time:
                    url += f"&endTime={end_time}"

                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
                try:
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        if resp.status == 200:
                            data = json.loads(resp.read().decode())
                            if not data:
                                break
                            batch = [
                                (sym, timeframe, int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5]))
                                for k in data
                            ]
                            cursor.executemany(
                                "INSERT OR IGNORE INTO klines_multi_tf (symbol, timeframe, open_time, open, high, low, close, volume) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                batch,
                            )
                            conn.commit()
                            earliest_ms = batch[0][2]
                            end_time = earliest_ms - 1
                            needed -= len(batch)
                            earliest_str = datetime.fromtimestamp(earliest_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
                            print(f"  -> Ingested {len(batch)} bars (Earliest: {earliest_str})")
                            if len(data) < limit:
                                break
                            time.sleep(0.15)
                        else:
                            print(f"  -> Binance API Error: HTTP {resp.status}")
                            break
                except Exception as e:
                    print(f"  -> Network error during historical fetch: {e}")
                    break
        else:
            print(f"\n[CACHE HIT] Loaded {total_candles} {timeframe} bars for {sym} from local SQLite cache (<10ms).")

        # Step 3: Query and return requested total_candles sorted ascending
        cursor.execute(
            """
            SELECT open_time, open, high, low, close, volume
            FROM (
                SELECT open_time, open, high, low, close, volume
                FROM klines_multi_tf
                WHERE symbol = ? AND timeframe = ?
                ORDER BY open_time DESC
                LIMIT ?
            )
            ORDER BY open_time ASC
            """,
            (sym, timeframe, total_candles),
        )
        rows = cursor.fetchall()
        candles = [
            (int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]))
            for r in rows
        ]

    if not candles:
        print(f"[FATAL] No candles retrieved for {symbol} ({timeframe}).")
        return []

    print(f"[DATA READY] Successfully assembled {len(candles)} sequential bars.")
    start_dt = datetime.fromtimestamp(candles[0][0] / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
    end_dt = datetime.fromtimestamp(candles[-1][0] / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
    print(f"[WINDOW] From {start_dt} UTC to {end_dt} UTC\n")
    return candles


def fetch_1m_candles_for_window(
    symbol: str,
    start_ms: int,
    end_ms: int,
    cache_db: str = "data/klines_cache.db",
) -> List[Tuple[int, float, float, float, float, float]]:
    """Fetch and locally cache 1-minute historical candles from Binance for high-fidelity replay."""
    os.makedirs(os.path.dirname(cache_db), exist_ok=True)
    with sqlite3.connect(cache_db) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS klines_1m (
                symbol TEXT,
                open_time INTEGER,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                PRIMARY KEY (symbol, open_time)
            )
            """
        )
        conn.commit()

        cursor = conn.cursor()
        cursor.execute(
            "SELECT open_time, open, high, low, close, volume FROM klines_1m WHERE symbol = ? AND open_time >= ? AND open_time <= ? ORDER BY open_time ASC",
            (symbol, start_ms, end_ms),
        )
        cached = cursor.fetchall()
        expected_candles = max(1, int((end_ms - start_ms) / 60000))
        if len(cached) >= (expected_candles * 0.95):
            return cached

        curr_start = start_ms
        new_candles = []
        while curr_start < end_ms:
            url = f"https://api.binance.com/api/v3/klines?symbol={symbol.upper()}&interval=1m&startTime={curr_start}&endTime={end_ms}&limit=1000"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    if resp.status == 200:
                        data = json.loads(resp.read().decode())
                        if not data:
                            break
                        batch = [
                            (
                                int(k[0]),
                                float(k[1]),
                                float(k[2]),
                                float(k[3]),
                                float(k[4]),
                                float(k[5]),
                            )
                            for k in data
                        ]
                        new_candles.extend(batch)
                        curr_start = batch[-1][0] + 60000
                        time.sleep(0.1)
                    else:
                        break
            except Exception:
                break

        if new_candles:
            cursor.executemany(
                "INSERT OR IGNORE INTO klines_1m (symbol, open_time, open, high, low, close, volume) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(symbol, c[0], c[1], c[2], c[3], c[4], c[5]) for c in new_candles],
            )
            conn.commit()

        cursor.execute(
            "SELECT open_time, open, high, low, close, volume FROM klines_1m WHERE symbol = ? AND open_time >= ? AND open_time <= ? ORDER BY open_time ASC",
            (symbol, start_ms, end_ms),
        )
        return cursor.fetchall()


def _calculate_backtest_returns(
    entry: float,
    exit_price: float,
    side: str,
    risk_dist: float,
    tp1_hit: bool,
    tp1_price: Optional[float],
    hold_minutes: int = 1,
) -> Tuple[float, float]:
    """Compute true dynamic gross return % and net R-multiple from real fill prices with funding cost and fee drag."""
    fee_pct = 0.15
    periods_8h = max(0, hold_minutes // 480)
    funding_cost_pct = periods_8h * 0.01

    if tp1_hit and tp1_price is not None:
        r1 = ((tp1_price - entry) / risk_dist) if side == "LONG" else ((entry - tp1_price) / risk_dist)
        r2 = ((exit_price - entry) / risk_dist) if side == "LONG" else ((entry - exit_price) / risk_dist)
        combined_gross_r = (0.5 * r1) + (0.5 * r2)
        gross_ret_pct = ((combined_gross_r * risk_dist) / entry) * 100.0
    else:
        gross_ret_pct = ((exit_price - entry) / entry) * 100.0 if side == "LONG" else ((entry - exit_price) / entry) * 100.0

    net_ret_pct = gross_ret_pct - fee_pct - funding_cost_pct
    r_mult = ((net_ret_pct / 100.0) * entry) / risk_dist if risk_dist > 0 else 0.0
    return round(net_ret_pct, 4), round(r_mult, 4)


def replay_trade_1m(
    symbol: str,
    active_trade: Dict[str, Any],
    entry_ts_ms: int,
    max_duration_hours: int = 72,
) -> Optional[Dict[str, Any]]:
    """Replay an open trade minute-by-minute through 1m candles to resolve intra-bar price path."""
    side = active_trade["side"]
    entry_price = active_trade["entry_price"]
    stop_loss = active_trade["stop_loss"]
    take_profit = active_trade["take_profit"]
    risk_dist = active_trade.get("risk_dist") or abs(entry_price - stop_loss)
    tp1_price = active_trade.get("tp1_price")

    end_window_ms = entry_ts_ms + (max_duration_hours * 3600 * 1000)
    m1_candles = fetch_1m_candles_for_window(symbol, entry_ts_ms, end_window_ms)
    if not m1_candles:
        return None

    if tp1_price is None:
        # Legacy all-or-nothing mode with +1.0R breakeven (backward compatible with test fixtures)
        breakeven_set = False
        breakeven_sl = entry_price * 1.0005 if side == "LONG" else entry_price * 0.9995

        for minute_idx, k in enumerate(m1_candles):
            m_ts, m_open, m_high, m_low, m_close, m_vol = k

            take_profit_hit = (m_high >= take_profit) if side == "LONG" else (m_low <= take_profit)
            stop_loss_hit = (m_low <= stop_loss) if side == "LONG" else (m_high >= stop_loss)

            exit_reason = None
            exit_price = None

            if stop_loss_hit and take_profit_hit:
                exit_reason = "STOP_LOSS"
                exit_price = min(m_open, stop_loss) if side == "LONG" else max(m_open, stop_loss)
            elif stop_loss_hit:
                exit_reason = "STOP_LOSS"
                exit_price = min(m_open, stop_loss) if side == "LONG" else max(m_open, stop_loss)
            elif take_profit_hit:
                exit_reason = "TAKE_PROFIT"
                exit_price = take_profit

            if exit_reason is not None and exit_price is not None:
                net_ret, r_mult = _calculate_backtest_returns(
                    entry=entry_price,
                    exit_price=exit_price,
                    side=side,
                    risk_dist=risk_dist,
                    tp1_hit=False,
                    tp1_price=None,
                    hold_minutes=minute_idx + 1,
                )
                m_dt_str = datetime.fromtimestamp(m_ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
                hold_minutes = minute_idx + 1
                return {
                    "exit_time": m_dt_str,
                    "exit_ts_ms": m_ts,
                    "exit_price": exit_price,
                    "reason": exit_reason,
                    "net_return_pct": net_ret,
                    "r_multiple": r_mult,
                    "hold_minutes": hold_minutes,
                    "breakeven_triggered": breakeven_set,
                }

            # Dynamic Breakeven Stop Adjustment at +1.0R
            if not breakeven_set:
                be_reached = (m_high >= entry_price + risk_dist) if side == "LONG" else (m_low <= entry_price - risk_dist)
                if be_reached:
                    breakeven_set = True
                    stop_loss = breakeven_sl
                    if (side == "LONG" and m_close <= breakeven_sl) or (side == "SHORT" and m_close >= breakeven_sl):
                        net_ret, r_mult = _calculate_backtest_returns(
                            entry=entry_price,
                            exit_price=breakeven_sl,
                            side=side,
                            risk_dist=risk_dist,
                            tp1_hit=False,
                            tp1_price=None,
                            hold_minutes=minute_idx + 1,
                        )
                        m_dt_str = datetime.fromtimestamp(m_ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
                        return {
                            "exit_time": m_dt_str,
                            "exit_ts_ms": m_ts,
                            "exit_price": breakeven_sl,
                            "reason": "STOP_LOSS",
                            "net_return_pct": net_ret,
                            "r_multiple": r_mult,
                            "hold_minutes": minute_idx + 1,
                            "breakeven_triggered": True,
                        }
        return None

    # Stepped Exit Engine v2.0: Multi-stage partial scaling, BE arming, and underwater time-stop
    tp1_hit = active_trade.get("tp1_hit", False)
    be_armed = active_trade.get("be_armed", False) or active_trade.get("breakeven_set", False)
    current_sl = stop_loss
    breakeven_sl = entry_price * 1.0005 if side == "LONG" else entry_price * 0.9995

    for minute_idx, k in enumerate(m1_candles):
        m_ts, m_open, m_high, m_low, m_close, m_vol = k
        hold_minutes = minute_idx + 1
        sl_at_min_start = current_sl
        be_armed_at_min_start = be_armed

        mfe_r = ((m_high - entry_price) / risk_dist) if side == "LONG" else ((entry_price - m_low) / risk_dist) if risk_dist > 0.0 else 0.0
        close_r = ((m_close - entry_price) / risk_dist) if side == "LONG" else ((entry_price - m_close) / risk_dist) if risk_dist > 0.0 else 0.0

        # Step A: 50% partial profit scale-out at +0.75R
        if not tp1_hit:
            tp1_touched = (m_high >= tp1_price) if side == "LONG" else (m_low <= tp1_price)
            if tp1_touched:
                tp1_hit = True

        # Step B: Breakeven arming at +0.85R excursion (avoids the Choke Trap)
        if tp1_hit and not be_armed:
            if mfe_r >= 0.85:
                be_armed = True
                current_sl = breakeven_sl

        # Step C: Take Profit 2 target (+1.33R full exit)
        take_profit_hit = (m_high >= take_profit) if side == "LONG" else (m_low <= take_profit)

        # Step D: Stop Loss hit evaluation
        stop_loss_hit = (m_low <= sl_at_min_start) if side == "LONG" else (m_high >= sl_at_min_start)
        # If BE was newly armed on this candle, check if candle closed back below BE
        if not stop_loss_hit and be_armed and not be_armed_at_min_start:
            if (side == "LONG" and m_close <= breakeven_sl) or (side == "SHORT" and m_close >= breakeven_sl):
                stop_loss_hit = True

        exit_reason = None
        exit_price = None
        net_ret = 0.0
        r_mult = 0.0

        if stop_loss_hit and take_profit_hit:
            # Conservative prioritization: stop loss triggered
            exit_price = min(m_open, sl_at_min_start) if side == "LONG" else max(m_open, sl_at_min_start)
            if be_armed:
                exit_reason = "BREAKEVEN"
            elif tp1_hit:
                exit_reason = "PARTIAL_LOSS"
            else:
                exit_reason = "STOP_LOSS"
            net_ret, r_mult = _calculate_backtest_returns(
                entry=entry_price,
                exit_price=exit_price,
                side=side,
                risk_dist=risk_dist,
                tp1_hit=tp1_hit,
                tp1_price=tp1_price,
                hold_minutes=hold_minutes,
            )

        elif take_profit_hit:
            exit_price = take_profit
            exit_reason = "TAKE_PROFIT"
            net_ret, r_mult = _calculate_backtest_returns(
                entry=entry_price,
                exit_price=exit_price,
                side=side,
                risk_dist=risk_dist,
                tp1_hit=tp1_hit,
                tp1_price=tp1_price,
                hold_minutes=hold_minutes,
            )

        elif stop_loss_hit:
            exit_price = min(m_open, current_sl) if side == "LONG" else max(m_open, current_sl)
            if be_armed:
                exit_reason = "BREAKEVEN"
            elif tp1_hit:
                exit_reason = "PARTIAL_LOSS"
            else:
                exit_reason = "STOP_LOSS"
            net_ret, r_mult = _calculate_backtest_returns(
                entry=entry_price,
                exit_price=exit_price,
                side=side,
                risk_dist=risk_dist,
                tp1_hit=tp1_hit,
                tp1_price=tp1_price,
                hold_minutes=hold_minutes,
            )

        elif not tp1_hit and hold_minutes >= 180 and close_r < -0.15:
            # Step E: Kinetic Stagnancy Exit Lifecycle Management (180m = 12 bars on 15m)
            exit_price = m_close
            exit_reason = "STAGNANCY_SCRATCH" if close_r >= -0.25 else "TIME_STOP_UNDERWATER"
            net_ret, r_mult = _calculate_backtest_returns(
                entry=entry_price,
                exit_price=exit_price,
                side=side,
                risk_dist=risk_dist,
                tp1_hit=False,
                tp1_price=None,
                hold_minutes=hold_minutes,
            )

        if exit_reason is not None and exit_price is not None:
            m_dt_str = datetime.fromtimestamp(m_ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
            return {
                "exit_time": m_dt_str,
                "exit_ts_ms": m_ts,
                "exit_price": exit_price,
                "reason": exit_reason,
                "net_return_pct": net_ret,
                "r_multiple": r_mult,
                "hold_minutes": hold_minutes,
                "breakeven_triggered": be_armed,
            }

    return None


def run_strategy_backtest(
    symbol: str = "XRPUSDT",
    timeframe: str = "15m",
    total_candles: int = 2500,
    db_path: str = "data/backtest_audit.db",
    hifi: bool = False,
    strict_retest: bool = False,
    adaptive_sl: bool = False,
    htf_filter: bool = False,
    tf1h_filter: bool = False,
    micro_filter: bool = False,
    market_structure: bool = False,
    fibonacci_ote: bool = False,
    order_blocks: bool = False,
    force_fresh: bool = False,
) -> None:
    """Run bar-by-bar backtest of the 5-factor strategy with whale invalidation classification."""
    candles = fetch_historical_candles(symbol, timeframe, total_candles, force_fresh=force_fresh)
    if len(candles) < 300:
        print("[FATAL] Insufficient historical candles for backtesting.")
        return

    # Clean previous backtest DB
    if os.path.exists(db_path):
        os.remove(db_path)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    replayer: Optional[HistoricalMicrostructureReplayer] = None
    oi_map: Dict[int, Tuple[int, float, float]] = {}
    funding_map: Dict[int, Tuple[int, float, float]] = {}
    if micro_filter:
        print(f"[MICROSTRUCTURE WARMUP] Ingesting Binance Futures Open Interest & Funding Rates for {symbol}...")
        replayer = HistoricalMicrostructureReplayer(symbol)
        oi_records = fetch_historical_open_interest(symbol, period=timeframe, total_records=total_candles, force_fresh=force_fresh)
        funding_records = fetch_historical_funding_rates(symbol, total_records=1000, force_fresh=force_fresh)
        for r in oi_records:
            oi_map[r[0]] = r
        for f in funding_records:
            funding_map[f[0]] = f
        print(f"  -> Ingested {len(oi_records)} OI snapshots and {len(funding_records)} funding rate events.\n")

    risk_manager = PortfolioRiskManager(
        max_open_positions=1,
        symbol_cooldown_bars=3,
        db_path=db_path,
    )
    trader = PaperTrader(
        symbol=symbol,
        timeframe=timeframe,
        db_path=db_path,
        risk_manager=risk_manager,
        micro_mgr=replayer,
        require_fvg_retest=strict_retest,
        confirm_fvg_rejection=strict_retest,
        adaptive_sl_floor=adaptive_sl,
        htf_trend_filter=htf_filter,
        intermediate_1h_filter=tf1h_filter,
        require_market_structure=market_structure,
        require_fibonacci_ote=fibonacci_ote,
        enable_order_blocks=order_blocks,
    )

    # 1. Warmup first 250 candles
    warmup_period = 250
    print(f"[WARMUP] Priming initial {warmup_period} bars to stabilize EMAs & RSI...")
    for c in candles[:warmup_period]:
        # tuple: (c_ts, o, h, l, c, v)
        if micro_filter and replayer is not None:
            latest_oi = oi_map.get(c[0])
            latest_funding = funding_map.get(c[0])
            replayer.update_step(c[4], latest_oi, latest_funding)
        trader.load_historical_candle(c)

    # 2. Ingest and prime 4h HTF candles if HTF gating is enabled
    htf_queue: deque = deque()
    eval_start_ts = candles[warmup_period][0]
    if htf_filter:
        print(f"[HTF WARMUP] Ingesting 4-hour candles for {symbol} to establish macro trend gating...")
        c4h_count = max(250, int(total_candles / 16) + 100)
        htf_candles = fetch_historical_candles(symbol, timeframe="4h", total_candles=c4h_count, force_fresh=force_fresh)
        priming_htf = [c for c in htf_candles if (c[0] + 14_400_000) <= eval_start_ts]
        for hc in priming_htf:
            trader.load_historical_htf_candle(hc)
        htf_queue = deque([c for c in htf_candles if (c[0] + 14_400_000) > eval_start_ts])
        print(f"  -> Primed {len(priming_htf)} historical 4h bars. Active progression queue: {len(htf_queue)} bars.\n")

    # 3. Ingest and prime 1h intermediate candles if 1h structure gating is enabled
    queue_1h: deque = deque()
    if tf1h_filter:
        print(f"[1H WARMUP] Ingesting 1-hour candles for {symbol} to establish intermediate structure gating...")
        candles_1h_count = max(500, int(total_candles / 4) + 100)
        candles_1h = fetch_historical_candles(symbol, timeframe="1h", total_candles=candles_1h_count, force_fresh=force_fresh)
        priming_1h = [c for c in candles_1h if (c[0] + 3_600_000) <= eval_start_ts]
        for hc in priming_1h:
            trader.load_historical_1h_candle(hc)
        queue_1h = deque([c for c in candles_1h if (c[0] + 3_600_000) > eval_start_ts])
        print(f"  -> Primed {len(priming_1h)} historical 1h bars. Active progression queue: {len(queue_1h)} bars.\n")

    print(f"[EXECUTION] Commencing bar-by-bar strategy replay across remaining {len(candles) - warmup_period} bars...\n")

    # Track trade metadata and whale volume diagnostics
    trade_logs: List[Dict[str, Any]] = []
    realized_equity = 100.0
    m2m_equity_curve: List[float] = [100.0]
    intra_equity_curve: List[float] = [100.0]
    m2m_peak = 100.0
    max_m2m_dd = 0.0
    max_intra_dd = 0.0
    recent_volumes: deque = deque(maxlen=20)
    for c in candles[:warmup_period]:
        recent_volumes.append(c[5])

    open_trade_snapshot: Optional[Dict[str, Any]] = None
    active_hifi_exit: Optional[Dict[str, Any]] = None

    for i, c in enumerate(candles[warmup_period:], start=warmup_period):
        c_ts, o, h, l, close, vol = c
        candle_5 = (o, h, l, close, vol)
        ts_str = datetime.fromtimestamp(c_ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")

        # Roll forward any completed 4h candles strictly as of their close timestamp
        if htf_filter and htf_queue:
            while htf_queue and (htf_queue[0][0] + 14_400_000) <= c_ts:
                next_4h = htf_queue.popleft()
                trader.on_htf_candle(next_4h)

        # Roll forward any completed 1h candles strictly as of their close timestamp
        if tf1h_filter and queue_1h:
            while queue_1h and (queue_1h[0][0] + 3_600_000) <= c_ts:
                next_1h = queue_1h.popleft()
                trader.on_1h_candle(next_1h)

        # Update historical microstructure state for current bar
        if micro_filter and replayer is not None:
            latest_oi = oi_map.get(c_ts)
            latest_funding = funding_map.get(c_ts)
            replayer.update_step(close, latest_oi, latest_funding)

        # In HiFi mode, if an active trade was resolved on the 1m tape:
        if hifi and active_hifi_exit is not None:
            # Check if current 15m candle encompasses or exceeds the 1m exit time
            if c_ts >= active_hifi_exit["exit_ts_ms"]:
                trade_id = open_trade_snapshot["id"] if open_trade_snapshot else None
                with trader._get_connection() as conn:
                    conn.execute(
                        "UPDATE paper_trades SET status = 'CLOSED', exit_price = ?, exit_timestamp = ?, reason = ?, net_return_pct = ?, r_multiple = ? WHERE id = ?",
                        (
                            active_hifi_exit["exit_price"],
                            active_hifi_exit["exit_time"],
                            active_hifi_exit["reason"],
                            active_hifi_exit["net_return_pct"],
                            active_hifi_exit["r_multiple"],
                            trade_id,
                        ),
                    )
                    conn.commit()

                vol_ratio = vol / (sum(recent_volumes) / max(1, len(recent_volumes))) if recent_volumes else 1.0
                is_whale_spike = vol_ratio >= 2.5
                classification = "NORMAL"
                if active_hifi_exit["reason"] == "STOP_LOSS" and is_whale_spike:
                    classification = "WHALE_VOLUME_EXPANSION"
                elif active_hifi_exit["reason"] == "STOP_LOSS":
                    classification = "NORMAL_STOP"
                elif active_hifi_exit["reason"] in ("TIME_STOP_UNDERWATER", "STAGNANCY_SCRATCH"):
                    classification = "TIME_STOP"
                elif active_hifi_exit["reason"] == "BREAKEVEN":
                    classification = "PROTECTED_WIN"
                elif active_hifi_exit["reason"] == "PARTIAL_LOSS":
                    classification = "SCRATCH_STOP"
                else:
                    classification = "TAKE_PROFIT"

                trade_logs.append({
                    "entry_time": open_trade_snapshot["entry_time"],
                    "exit_time": active_hifi_exit["exit_time"],
                    "side": open_trade_snapshot["side"],
                    "entry_price": open_trade_snapshot["entry_price"],
                    "exit_price": active_hifi_exit["exit_price"],
                    "reason": active_hifi_exit["reason"],
                    "net_return_pct": active_hifi_exit["net_return_pct"],
                    "r_multiple": active_hifi_exit["r_multiple"],
                    "exit_vol_ratio": vol_ratio,
                    "classification": classification,
                    "hold_minutes": active_hifi_exit["hold_minutes"],
                    "breakeven_triggered": active_hifi_exit["breakeven_triggered"],
                })
                realized_equity *= (1.0 + active_hifi_exit["r_multiple"] * 0.01)

                trader.active_trade = None
                if trader.risk_manager is not None:
                    trader.risk_manager.record_position_closed(symbol, trader.bar_count)

                open_trade_snapshot = None
                active_hifi_exit = None

        # Check if trade opened on previous bar
        curr_active = trader.get_active_trade()
        if curr_active and open_trade_snapshot is None:
            avg_v_before = sum(recent_volumes) / max(1, len(recent_volumes))
            open_trade_snapshot = {
                "id": curr_active.get("id"),
                "entry_bar": i,
                "entry_time": ts_str,
                "side": curr_active["side"],
                "entry_price": curr_active["entry_price"],
                "stop_loss": curr_active["stop_loss"],
                "initial_stop_loss": curr_active.get("initial_stop_loss", curr_active["stop_loss"]),
                "take_profit": curr_active["take_profit"],
                "tp1_price": curr_active.get("tp1_price"),
                "avg_vol_at_entry": avg_v_before,
            }

            if hifi:
                print(f"[{symbol} HIFI] Replaying 1-minute order flow for {curr_active['side']} trade opened at {ts_str}...")
                hifi_res = replay_trade_1m(symbol, curr_active, c_ts)
                if hifi_res:
                    active_hifi_exit = hifi_res
                    print(f"  -> 1M Resolution Exit: {hifi_res['reason']} at {hifi_res['exit_time']} | Price: {format_price(hifi_res['exit_price'])} | Hold: {hifi_res['hold_minutes']}m | Return: {hifi_res['net_return_pct']:+.2f}%\n")

        # Calculate volume metrics before updating
        avg_vol_20 = sum(recent_volumes) / max(1, len(recent_volumes))
        vol_ratio = vol / avg_vol_20 if avg_vol_20 > 0 else 1.0

        # Run bar evaluation
        if hifi and active_hifi_exit is not None:
            # Update candles and RSI rolling buffer without triggering coarse 15m exit
            trader.bar_count += 1
            trader.candles.append(candle_5)
            all_closes = [cand[3] for cand in trader.candles]
            if len(all_closes) >= 15:
                trader.rsi_history.append(calculate_rsi(all_closes, period=14))
        else:
            trader.on_candle(candle_5, timestamp=ts_str)

        recent_volumes.append(vol)

        # Standard mode exit check
        if not hifi:
            after_active = trader.get_active_trade()
            if open_trade_snapshot is not None and after_active is None:
                # Trade closed during this candle!
                with trader._get_connection() as conn:
                    cur = conn.cursor()
                    cur.execute(
                        "SELECT exit_price, reason, net_return_pct, r_multiple, exit_timestamp FROM paper_trades WHERE symbol = ? AND status = 'CLOSED' ORDER BY id DESC LIMIT 1",
                        (symbol,),
                    )
                    row = cur.fetchone()

                if row:
                    exit_price, reason, net_ret, r_mult, exit_ts = row
                    is_whale_spike = vol_ratio >= 2.5
                    classification = "NORMAL"
                    if reason == "STOP_LOSS" and is_whale_spike:
                        classification = "WHALE_VOLUME_EXPANSION"
                    elif reason == "STOP_LOSS":
                        classification = "NORMAL_STOP"
                    elif reason in ("TIME_STOP_UNDERWATER", "STAGNANCY_SCRATCH"):
                        classification = "TIME_STOP"
                    elif reason == "BREAKEVEN":
                        classification = "PROTECTED_WIN"
                    elif reason == "PARTIAL_LOSS":
                        classification = "SCRATCH_STOP"
                    else:
                        classification = "TAKE_PROFIT"

                    trade_logs.append({
                        "entry_time": open_trade_snapshot["entry_time"],
                        "exit_time": exit_ts,
                        "side": open_trade_snapshot["side"],
                        "entry_price": open_trade_snapshot["entry_price"],
                        "exit_price": exit_price,
                        "reason": reason,
                        "net_return_pct": net_ret,
                        "r_multiple": r_mult,
                        "exit_vol_ratio": vol_ratio,
                        "classification": classification,
                    })
                    realized_equity *= (1.0 + r_mult * 0.01)

                open_trade_snapshot = None

        # Mark-to-Market bar-by-bar tracking
        bar_open_r = 0.0
        bar_adverse_r = 0.0
        if trader.active_trade is not None and open_trade_snapshot is not None:
            snap = open_trade_snapshot
            e_price = snap["entry_price"]
            init_sl = snap.get("initial_stop_loss", snap["stop_loss"])
            r_dist = abs(e_price - init_sl)
            if r_dist <= 0:
                r_dist = e_price * 0.01
            if snap["side"] == "LONG":
                bar_open_r = (close - e_price) / r_dist
                bar_adverse_r = (l - e_price) / r_dist
            else:
                bar_open_r = (e_price - close) / r_dist
                bar_adverse_r = (e_price - h) / r_dist
            bar_adverse_r = max(-1.0, bar_adverse_r)

        current_m2m_eq = realized_equity * (1.0 + bar_open_r * 0.01)
        m2m_equity_curve.append(current_m2m_eq)
        if current_m2m_eq > m2m_peak:
            m2m_peak = current_m2m_eq
        curr_m2m_dd = ((m2m_peak - current_m2m_eq) / m2m_peak) * 100.0
        if curr_m2m_dd > max_m2m_dd:
            max_m2m_dd = curr_m2m_dd

        current_intra_eq = realized_equity * (1.0 + bar_adverse_r * 0.01)
        curr_intra_dd = ((m2m_peak - current_intra_eq) / m2m_peak) * 100.0
        if curr_intra_dd > max_intra_dd:
            max_intra_dd = curr_intra_dd

    # Print comprehensive performance breakdown
    print_backtest_report(
        symbol,
        timeframe,
        len(candles) - warmup_period,
        trade_logs,
        hifi=hifi,
        adaptive_sl=adaptive_sl,
        htf_filter=htf_filter,
        tf1h_filter=tf1h_filter,
        micro_filter=micro_filter,
        max_m2m_dd=max_m2m_dd,
        max_intra_dd=max_intra_dd,
    )


def print_backtest_report(
    symbol: str,
    timeframe: str,
    evaluated_bars: int,
    trades: List[Dict[str, Any]],
    hifi: bool = False,
    adaptive_sl: bool = False,
    htf_filter: bool = False,
    tf1h_filter: bool = False,
    micro_filter: bool = False,
    max_m2m_dd: float = 0.0,
    max_intra_dd: float = 0.0,
) -> None:
    """Print an institutional statistical audit report."""
    features = []
    if hifi:
        features.append("1M INTRA-BAR REPLAY")
    else:
        features.append("BAR-LEVEL")
    if adaptive_sl:
        features.append("ADAPTIVE-SL")
    if tf1h_filter:
        features.append("1H-GATE")
    if htf_filter:
        features.append("4H-HTF-GATE")
    if micro_filter:
        features.append("OI-MICROSTRUCTURE")
    mode_str = " | ".join(features)
    print("=" * 125)
    print(f" [BACKTEST REPORT] {symbol} ({timeframe}) | MODE: {mode_str} | EVALUATED BARS: {evaluated_bars} | TOTAL TRADES: {len(trades)}")
    print("=" * 125)

    if not trades:
        print("[AUDIT] No trades were triggered during this period. Market was either ranging or filters protected capital.")
        print("=" * 125 + "\n")
        return

    # Statistical Aggregations
    total_trades = len(trades)
    winning_trades = [t for t in trades if t["net_return_pct"] > 0]
    losing_trades = [t for t in trades if t["net_return_pct"] <= 0]
    whale_stopped_trades = [t for t in trades if t["classification"] == "WHALE_VOLUME_EXPANSION"]
    normal_stopped_trades = [t for t in trades if t["classification"] in ("NORMAL_STOP", "TIME_STOP", "SCRATCH_STOP")]

    win_count = len(winning_trades)
    loss_count = len(losing_trades)
    win_rate = (win_count / total_trades) * 100.0 if total_trades > 0 else 0.0

    total_net_return = sum(t["net_return_pct"] for t in trades)
    avg_r_multiple = sum(t["r_multiple"] for t in trades) / total_trades if total_trades > 0 else 0.0

    gross_profit = sum(t["net_return_pct"] for t in winning_trades)
    gross_loss = abs(sum(t["net_return_pct"] for t in losing_trades))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (99.9 if gross_profit > 0 else 0.0)

    # Calculate Maximum Drawdown %
    equity_curve = [100.0]
    for t in trades:
        prev = equity_curve[-1]
        equity_curve.append(prev * (1.0 + t["net_return_pct"] / 100.0))

    peak = equity_curve[0]
    max_dd_pct = 0.0
    for eq in equity_curve:
        if eq > peak:
            peak = eq
        dd = ((peak - eq) / peak) * 100.0
        if dd > max_dd_pct:
            max_dd_pct = dd

    # Long vs Short
    long_trades = [t for t in trades if t["side"] == "LONG"]
    short_trades = [t for t in trades if t["side"] == "SHORT"]
    long_wins = len([t for t in long_trades if t["net_return_pct"] > 0])
    short_wins = len([t for t in short_trades if t["net_return_pct"] > 0])

    long_wr = (long_wins / len(long_trades) * 100.0) if long_trades else 0.0
    short_wr = (short_wins / len(short_trades) * 100.0) if short_trades else 0.0

    # Print summary cards
    print(f" TOTAL TRADES:         {total_trades:<6} | WIN RATE:           {win_rate:5.1f}% ({win_count}W / {loss_count}L)")
    print(f" PROFIT FACTOR:        {profit_factor:<6.2f} | CUMULATIVE RETURN:  {total_net_return:+6.2f}% (After 0.15% fee drag)")
    print(f" AVG R-MULTIPLE:       {avg_r_multiple:+5.2f}R | CLOSED MAX DRAWDOWN: {max_dd_pct:5.2f}%")
    if max_m2m_dd > 0:
        print(f" M2M MAX DRAWDOWN:     {max_m2m_dd:5.2f}% | WORST INTRA-BAR DD:   {max_intra_dd:5.2f}%")
    print(f" LONG PERFORMANCE:     {len(long_trades)} trades (Win Rate: {long_wr:.1f}%)")
    print(f" SHORT PERFORMANCE:    {len(short_trades)} trades (Win Rate: {short_wr:.1f}%)")

    if hifi:
        avg_hold = sum(t.get("hold_minutes", 0) for t in trades) / total_trades if total_trades else 0
        print(f" AVG HOLD TIME:        {avg_hold:.0f} mins ({avg_hold / 60:.1f} hours) | 1-MINUTE PRECISION RESOLVED: 100%")
    print("-" * 125)

    # Whale Invalidation Analysis
    print(" [WHALE & ABNORMAL VOLUME ANALYSIS]")
    print(f" Total Losses:         {loss_count}")
    print(f"  - Losses from Abnormal Whale Volume (>2.5x Vol): {len(whale_stopped_trades)} ({len(whale_stopped_trades)/max(1, loss_count)*100:.1f}% of all losses)")
    print(f"  - Losses from Normal Technical Invalidation:      {len(normal_stopped_trades)} ({len(normal_stopped_trades)/max(1, loss_count)*100:.1f}% of all losses)")

    if total_trades - len(whale_stopped_trades) > 0:
        adj_wr = (win_count / (total_trades - len(whale_stopped_trades))) * 100.0
        print(f"  -> Technical Strategy Baseline Win Rate (Excluding Whale Blowouts): {adj_wr:.1f}%")

    print("-" * 125)
    if hifi:
        print(f"{'ENTRY TIME':<16} | {'SIDE':<5} | {'ENTRY':<10} | {'EXIT':<10} | {'REASON':<12} | {'NET %':<8} | {'R-MULT':<7} | {'HOLD':<9} | {'VOL RATIO':<9} | {'CLASSIFICATION':<22}")
    else:
        print(f"{'ENTRY TIME':<16} | {'SIDE':<5} | {'ENTRY':<10} | {'EXIT':<10} | {'REASON':<12} | {'NET %':<8} | {'R-MULT':<7} | {'VOL RATIO':<9} | {'CLASSIFICATION':<22}")
    print("-" * 125)

    for t in trades:
        class_str = t["classification"]
        if class_str == "WHALE_VOLUME_EXPANSION":
            class_str = "[!] WHALE SPIKE"
        elif class_str == "PROTECTED_WIN":
            class_str = "[+] BE WIN (+0.38R)"
        elif class_str == "TAKE_PROFIT":
            class_str = "[+] TARGET HIT"
        elif class_str == "TIME_STOP":
            class_str = "[-] TIME STOP"
        elif class_str == "SCRATCH_STOP":
            class_str = "[-] PARTIAL LOSS"
        else:
            class_str = "[-] NORMAL STOP"

        reason_disp = "TIME_STOP" if t["reason"] in ("TIME_STOP_UNDERWATER", "STAGNANCY_SCRATCH") else t["reason"]

        if hifi:
            hold_str = f"{t.get('hold_minutes', 0)}m"
            print(
                f"{t['entry_time']:<16} | {t['side']:<5} | {format_price(t['entry_price']):<10} | "
                f"{format_price(t['exit_price']):<10} | {reason_disp:<12} | {t['net_return_pct']:+7.2f}% | "
                f"{t['r_multiple']:+6.2f}R | {hold_str:<9} | {t['exit_vol_ratio']:>7.1f}x | {class_str:<22}"
            )
        else:
            print(
                f"{t['entry_time']:<16} | {t['side']:<5} | {format_price(t['entry_price']):<10} | "
                f"{format_price(t['exit_price']):<10} | {reason_disp:<12} | {t['net_return_pct']:+7.2f}% | "
                f"{t['r_multiple']:+6.2f}R | {t['exit_vol_ratio']:>7.1f}x | {class_str:<22}"
            )
    print("=" * 125 + "\n")


def estimate_confluence_priority(
    trader: PaperTrader,
    candle_tuple: Tuple[int, float, float, float, float, float],
    strict_retest: bool = False,
    htf_filter: bool = False,
    tf1h_filter: bool = False,
) -> float:
    """Estimate confluence priority score (0-100) to rank candidate setups on the same bar."""
    if trader.active_trade is not None:
        return -100.0
    if trader.risk_manager is not None and trader.risk_manager.is_symbol_in_cooldown(trader.symbol, trader.bar_count):
        return -100.0

    c_ts, o, h, l, close, vol = candle_tuple
    if len(trader.candles) < 200:
        return 0.0

    all_highs = [c[1] for c in trader.candles] + [h]
    all_lows = [c[2] for c in trader.candles] + [l]
    all_closes = [c[3] for c in trader.candles] + [close]
    all_volumes = [c[4] for c in trader.candles] + [vol]

    ema_50 = calculate_ema(all_closes, 50)
    ema_200 = calculate_ema(all_closes, 200)

    prior_volumes = all_volumes[-21:-1] if len(all_volumes) >= 21 else all_volumes[:-1]
    avg_vol_20 = float(np.mean(prior_volumes)) if len(prior_volumes) > 0 else 1.0
    vol_expansion = (
        avg_vol_20 > 0.0
        and any(v > 0.0 and v >= (1.5 * avg_vol_20) for v in all_volumes[-6:])
    )

    candles_sim = list(trader.candles) + [(o, h, l, close, vol)]
    bull_div, bear_div = detect_rsi_divergences(candles_sim, period=14, lookback=30)
    adx_14 = calculate_adx(all_highs, all_lows, all_closes, period=14)
    if adx_14 < 25.0 or avg_vol_20 <= 0.0:
        return 0.0

    effective_htf = trader.get_htf_trend() if htf_filter else "NEUTRAL"
    effective_1h = trader.get_1h_trend() if tf1h_filter else "NEUTRAL"
    long_1h_allowed = (effective_1h == "BULLISH") or (
        effective_1h == "NEUTRAL" and (not htf_filter or effective_htf == "BULLISH")
    )
    short_1h_allowed = (effective_1h == "BEARISH") or (
        effective_1h == "NEUTRAL" and (not htf_filter or effective_htf == "BEARISH")
    )

    # Evaluate Long candidate
    long_trend = (ema_50 > ema_200) and (close > ema_50)
    long_score = 0.0
    if long_trend and not (htf_filter and effective_htf == "BEARISH") and not (tf1h_filter and not long_1h_allowed):
        long_retest, _ = check_bullish_fvg_retest(candles_sim, lookback=15)
        long_fresh_fvg = check_recent_bullish_fvg(candles_sim, lookback=5)
        long_score += 20.0
        long_score += 15.0 if adx_14 >= 25.0 else 10.0
        if long_retest:
            long_score += 25.0
        elif long_fresh_fvg and not strict_retest:
            long_score += 15.0
        if bull_div:
            long_score += 20.0
        elif len(trader.rsi_history) >= 1 and trader.rsi_history[-1] < 40.0:
            long_score += 10.0
        if vol_expansion:
            long_score += 10.0
        if not long_retest and not (long_fresh_fvg and not strict_retest):
            long_score = min(long_score, 65.0)

    # Evaluate Short candidate
    short_trend = (ema_50 < ema_200) and (close < ema_50)
    short_score = 0.0
    if short_trend and not (htf_filter and effective_htf == "BULLISH") and not (tf1h_filter and not short_1h_allowed):
        short_retest, _ = check_bearish_fvg_retest(candles_sim, lookback=15)
        short_fresh_fvg = check_recent_bearish_fvg(candles_sim, lookback=5)
        short_score += 20.0
        short_score += 15.0 if adx_14 >= 25.0 else 10.0
        if short_retest:
            short_score += 25.0
        elif short_fresh_fvg and not strict_retest:
            short_score += 15.0
        if bear_div:
            short_score += 20.0
        elif len(trader.rsi_history) >= 1 and trader.rsi_history[-1] > 60.0:
            short_score += 10.0
        if vol_expansion:
            short_score += 10.0
        if not short_retest and not (short_fresh_fvg and not strict_retest):
            short_score = min(short_score, 65.0)

    return max(long_score, short_score)


def run_portfolio_backtest(
    symbols: Optional[List[str]] = None,
    timeframe: str = "15m",
    total_candles: int = 2500,
    db_path: str = "data/backtest_portfolio.db",
    hifi: bool = False,
    strict_retest: bool = False,
    adaptive_sl: bool = False,
    htf_filter: bool = False,
    tf1h_filter: bool = False,
    micro_filter: bool = False,
    market_structure: bool = False,
    fibonacci_ote: bool = False,
    order_blocks: bool = False,
    force_fresh: bool = False,
) -> None:
    """Run synchronized bar-by-bar portfolio backtest across multiple assets with unified risk gating."""
    active_symbols = [s.strip().upper() for s in (symbols or DEFAULT_PORTFOLIO_SYMBOLS) if s.strip()]
    if not active_symbols:
        print("[FATAL] No symbols provided for portfolio backtest.")
        return

    # Clean previous portfolio backtest DB
    if os.path.exists(db_path):
        os.remove(db_path)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    print("=" * 130)
    print(f" [PORTFOLIO BACKTEST] MULTI-ASSET SYNCHRONIZED ENGINE | {len(active_symbols)} ASSETS")
    print(f" UNIVERSE:             {', '.join(active_symbols)}")
    print(f" REQUESTED CANDLES:    {total_candles} bars ({timeframe}) | POSITION LIMIT: MAX 3 CONCURRENT | COOLDOWN: 3 BARS")
    print("=" * 130 + "\n")

    candles_15m_map: Dict[str, Dict[int, Tuple[int, float, float, float, float, float]]] = {}
    htf_candles_map: Dict[str, List[Tuple[int, float, float, float, float, float]]] = {}
    candles_1h_map: Dict[str, List[Tuple[int, float, float, float, float, float]]] = {}
    oi_maps: Dict[str, Dict[int, Tuple[int, float, float]]] = {}
    funding_maps: Dict[str, Dict[int, Tuple[int, float, float]]] = {}
    replayers: Dict[str, HistoricalMicrostructureReplayer] = {}

    print(f"[DATA INGESTION] Ingesting multi-timeframe candles and microstructure for {len(active_symbols)} assets...")
    for sym in active_symbols:
        c_15m = fetch_historical_candles(sym, timeframe, total_candles, force_fresh=force_fresh)
        candles_15m_map[sym] = {c[0]: c for c in c_15m}

        if htf_filter:
            c4h_count = max(250, int(total_candles / 16) + 100)
            htf_candles_map[sym] = fetch_historical_candles(sym, timeframe="4h", total_candles=c4h_count, force_fresh=force_fresh)

        if tf1h_filter:
            c1h_count = max(500, int(total_candles / 4) + 100)
            candles_1h_map[sym] = fetch_historical_candles(sym, timeframe="1h", total_candles=c1h_count, force_fresh=force_fresh)

        if micro_filter:
            replayers[sym] = HistoricalMicrostructureReplayer(sym)
            oi_recs = fetch_historical_open_interest(sym, period=timeframe, total_records=total_candles, force_fresh=force_fresh)
            fund_recs = fetch_historical_funding_rates(sym, total_records=1000, force_fresh=force_fresh)
            oi_maps[sym] = {r[0]: r for r in oi_recs}
            funding_maps[sym] = {f[0]: f for f in fund_recs}
        print(f"  - {sym:<8}: {len(c_15m)} 15m bars cached")

    all_ts_sets = [set(candles_15m_map[s].keys()) for s in active_symbols]
    common_ts = sorted(list(set.intersection(*all_ts_sets)))
    if len(common_ts) < 300:
        print(f"[FATAL] Insufficient overlapping bars across portfolio (found {len(common_ts)} common bars, need >= 300).")
        return

    days_span = (len(common_ts) * timeframe_to_ms(timeframe)) / (86400 * 1000)
    start_dt = datetime.fromtimestamp(common_ts[0] / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
    end_dt = datetime.fromtimestamp(common_ts[-1] / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
    print(f"\n[TIMELINE ALIGNMENT] Master timeline established across all {len(active_symbols)} assets:")
    print(f"  -> Total Synchronized Bars: {len(common_ts):,} ({days_span:.1f} days) | Range: {start_dt} UTC to {end_dt} UTC\n")

    risk_manager = PortfolioRiskManager(
        max_open_positions=3,
        symbol_cooldown_bars=3,
        db_path=db_path,
    )

    traders: Dict[str, PaperTrader] = {}
    htf_queues: Dict[str, deque] = {}
    queues_1h: Dict[str, deque] = {}
    recent_volumes: Dict[str, deque] = {}
    open_trade_snapshots: Dict[str, Optional[Dict[str, Any]]] = {}
    active_hifi_exits: Dict[str, Optional[Dict[str, Any]]] = {}

    for sym in active_symbols:
        traders[sym] = PaperTrader(
            symbol=sym,
            timeframe=timeframe,
            db_path=db_path,
            risk_manager=risk_manager,
            micro_mgr=replayers.get(sym),
            require_fvg_retest=strict_retest,
            confirm_fvg_rejection=strict_retest,
            adaptive_sl_floor=adaptive_sl,
            htf_trend_filter=htf_filter,
            intermediate_1h_filter=tf1h_filter,
            require_market_structure=market_structure,
            require_fibonacci_ote=fibonacci_ote,
            enable_order_blocks=order_blocks,
        )
        htf_queues[sym] = deque()
        queues_1h[sym] = deque()
        recent_volumes[sym] = deque(maxlen=20)
        open_trade_snapshots[sym] = None
        active_hifi_exits[sym] = None

    warmup_period = 250
    eval_start_ts = common_ts[warmup_period]
    print(f"[PORTFOLIO WARMUP] Priming initial {warmup_period} synchronized bars across all {len(active_symbols)} coins...")

    for sym in active_symbols:
        for t_ms in common_ts[:warmup_period]:
            c = candles_15m_map[sym][t_ms]
            if micro_filter and sym in replayers:
                latest_oi = oi_maps[sym].get(c[0])
                latest_funding = funding_maps[sym].get(c[0])
                replayers[sym].update_step(c[4], latest_oi, latest_funding)
            traders[sym].load_historical_candle(c)
            recent_volumes[sym].append(c[5])

        if htf_filter:
            htf_candles = htf_candles_map[sym]
            priming_htf = [c for c in htf_candles if (c[0] + 14_400_000) <= eval_start_ts]
            for hc in priming_htf:
                traders[sym].load_historical_htf_candle(hc)
            htf_queues[sym] = deque([c for c in htf_candles if (c[0] + 14_400_000) > eval_start_ts])

        if tf1h_filter:
            c1h = candles_1h_map[sym]
            priming_1h = [c for c in c1h if (c[0] + 3_600_000) <= eval_start_ts]
            for hc in priming_1h:
                traders[sym].load_historical_1h_candle(hc)
            queues_1h[sym] = deque([c for c in c1h if (c[0] + 3_600_000) > eval_start_ts])

    print("  -> Initial warmup complete. Indicators, 4h macro, and 1h intermediate structures locked.\n")
    print(f"[PORTFOLIO EXECUTION] Commencing synchronized replay across {len(common_ts) - warmup_period} bars...\n")

    portfolio_trade_logs: List[Dict[str, Any]] = []
    realized_port_equity = 100.0
    m2m_equity_curve: List[float] = [100.0]
    intra_equity_curve: List[float] = [100.0]
    m2m_peak = 100.0
    max_m2m_dd = 0.0
    max_intra_dd = 0.0
    capacity_counts = {0: 0, 1: 0, 2: 0, 3: 0}
    signals_rejected_portfolio_cap = 0
    signals_rejected_cooldown = 0
    blocked_candidates_by_sym: Dict[str, int] = {s: 0 for s in active_symbols}


    for bar_idx, t_ms in enumerate(common_ts[warmup_period:], start=warmup_period):
        ts_str = datetime.fromtimestamp(t_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")

        # Step A: Roll forward completed 4h & 1h candles
        for sym in active_symbols:
            if htf_filter and htf_queues[sym]:
                while htf_queues[sym] and (htf_queues[sym][0][0] + 14_400_000) <= t_ms:
                    next_4h = htf_queues[sym].popleft()
                    traders[sym].on_htf_candle(next_4h)

            if tf1h_filter and queues_1h[sym]:
                while queues_1h[sym] and (queues_1h[sym][0][0] + 3_600_000) <= t_ms:
                    next_1h = queues_1h[sym].popleft()
                    traders[sym].on_1h_candle(next_1h)

            if micro_filter and sym in replayers:
                c = candles_15m_map[sym][t_ms]
                latest_oi = oi_maps[sym].get(t_ms)
                latest_funding = funding_maps[sym].get(t_ms)
                replayers[sym].update_step(c[4], latest_oi, latest_funding)

        # Step B: Pass 1 - Exits Evaluation across active trades
        active_at_start = {
            sym for sym in active_symbols
            if traders[sym].active_trade is not None or active_hifi_exits[sym] is not None
        }

        for sym in active_symbols:
            c = candles_15m_map[sym][t_ms]
            c_ts, o, h, l, close, vol = c
            candle_5 = (o, h, l, close, vol)
            trader = traders[sym]

            # HiFi exit check
            if hifi and active_hifi_exits[sym] is not None:
                hifi_exit = active_hifi_exits[sym]
                if t_ms >= hifi_exit["exit_ts_ms"]:
                    trade_id = open_trade_snapshots[sym]["id"] if open_trade_snapshots[sym] else None
                    with trader._get_connection() as conn:
                        conn.execute(
                            "UPDATE paper_trades SET status = 'CLOSED', exit_price = ?, exit_timestamp = ?, reason = ?, net_return_pct = ?, r_multiple = ? WHERE id = ?",
                            (
                                hifi_exit["exit_price"],
                                hifi_exit["exit_time"],
                                hifi_exit["reason"],
                                hifi_exit["net_return_pct"],
                                hifi_exit["r_multiple"],
                                trade_id,
                            ),
                        )
                        conn.commit()

                    vol_ratio = vol / (sum(recent_volumes[sym]) / max(1, len(recent_volumes[sym]))) if recent_volumes[sym] else 1.0
                    is_whale_spike = vol_ratio >= 2.5
                    classification = "NORMAL"
                    if hifi_exit["reason"] == "STOP_LOSS" and is_whale_spike:
                        classification = "WHALE_VOLUME_EXPANSION"
                    elif hifi_exit["reason"] == "STOP_LOSS":
                        classification = "NORMAL_STOP"
                    elif hifi_exit["reason"] in ("TIME_STOP_UNDERWATER", "STAGNANCY_SCRATCH"):
                        classification = "TIME_STOP"
                    elif hifi_exit["reason"] == "BREAKEVEN":
                        classification = "PROTECTED_WIN"
                    elif hifi_exit["reason"] == "PARTIAL_LOSS":
                        classification = "SCRATCH_STOP"
                    else:
                        classification = "TAKE_PROFIT"

                    portfolio_trade_logs.append({
                        "symbol": sym,
                        "entry_time": open_trade_snapshots[sym]["entry_time"],
                        "exit_time": hifi_exit["exit_time"],
                        "side": open_trade_snapshots[sym]["side"],
                        "entry_price": open_trade_snapshots[sym]["entry_price"],
                        "exit_price": hifi_exit["exit_price"],
                        "reason": hifi_exit["reason"],
                        "net_return_pct": hifi_exit["net_return_pct"],
                        "r_multiple": hifi_exit["r_multiple"],
                        "exit_vol_ratio": vol_ratio,
                        "classification": classification,
                        "hold_minutes": hifi_exit["hold_minutes"],
                        "breakeven_triggered": hifi_exit["breakeven_triggered"],
                    })
                    realized_port_equity *= (1.0 + hifi_exit["r_multiple"] * 0.01)

                    trader.active_trade = None
                    if trader.risk_manager is not None:
                        trader.risk_manager.record_position_closed(sym, trader.bar_count)

                    open_trade_snapshots[sym] = None
                    active_hifi_exits[sym] = None

            elif not hifi:
                if trader.active_trade is not None:
                    trader.on_candle(candle_5, timestamp=ts_str)
                    if trader.active_trade is None and open_trade_snapshots[sym] is not None:
                        with trader._get_connection() as conn:
                            cur = conn.cursor()
                            cur.execute(
                                "SELECT exit_price, reason, net_return_pct, r_multiple, exit_timestamp FROM paper_trades WHERE symbol = ? AND status = 'CLOSED' ORDER BY id DESC LIMIT 1",
                                (sym,),
                            )
                            row = cur.fetchone()
                        if row:
                            exit_price, reason, net_ret, r_mult, exit_ts = row
                            vol_ratio = vol / (sum(recent_volumes[sym]) / max(1, len(recent_volumes[sym]))) if recent_volumes[sym] else 1.0
                            is_whale_spike = vol_ratio >= 2.5
                            classification = "NORMAL"
                            if reason == "STOP_LOSS" and is_whale_spike:
                                classification = "WHALE_VOLUME_EXPANSION"
                            elif reason == "STOP_LOSS":
                                classification = "NORMAL_STOP"
                            elif reason in ("TIME_STOP_UNDERWATER", "STAGNANCY_SCRATCH"):
                                classification = "TIME_STOP"
                            elif reason == "BREAKEVEN":
                                classification = "PROTECTED_WIN"
                            elif reason == "PARTIAL_LOSS":
                                classification = "SCRATCH_STOP"
                            else:
                                classification = "TAKE_PROFIT"

                            portfolio_trade_logs.append({
                                "symbol": sym,
                                "entry_time": open_trade_snapshots[sym]["entry_time"],
                                "exit_time": exit_ts,
                                "side": open_trade_snapshots[sym]["side"],
                                "entry_price": open_trade_snapshots[sym]["entry_price"],
                                "exit_price": exit_price,
                                "reason": reason,
                                "net_return_pct": net_ret,
                                "r_multiple": r_mult,
                                "exit_vol_ratio": vol_ratio,
                                "classification": classification,
                                "hold_minutes": (trader.bar_count - open_trade_snapshots[sym]["entry_bar"]) * 15,
                                "breakeven_triggered": False,
                            })
                            realized_port_equity *= (1.0 + r_mult * 0.01)
                        open_trade_snapshots[sym] = None

        # Step C: Pass 2 - Candidate Entries Priority Ranking
        # Only symbols that were idle at the start of the bar are eligible for new entries
        idle_symbols = [s for s in active_symbols if s not in active_at_start]
        scores = {
            s: estimate_confluence_priority(
                traders[s],
                candles_15m_map[s][t_ms],
                strict_retest=strict_retest,
                htf_filter=htf_filter,
                tf1h_filter=tf1h_filter,
            )
            for s in idle_symbols
        }

        # Track signals that would trigger but get rejected
        candidate_symbols = [s for s in idle_symbols if scores[s] >= 70.0]
        for cs in candidate_symbols:
            can_open, rej_reason = risk_manager.can_open_position(cs, traders[cs].bar_count)
            if not can_open:
                if "Portfolio limit reached" in rej_reason:
                    signals_rejected_portfolio_cap += 1
                    blocked_candidates_by_sym[cs] += 1
                elif "cooldown" in rej_reason:
                    signals_rejected_cooldown += 1


        # Sort idle symbols by confluence score descending
        ranked_idle = sorted(idle_symbols, key=lambda s: scores[s], reverse=True)

        for sym in ranked_idle:
            c = candles_15m_map[sym][t_ms]
            candle_5 = (c[1], c[2], c[3], c[4], c[5])
            trader = traders[sym]

            trader.on_candle(candle_5, timestamp=ts_str)

            curr_active = trader.get_active_trade()
            if curr_active and open_trade_snapshots[sym] is None:
                avg_v_before = sum(recent_volumes[sym]) / max(1, len(recent_volumes[sym]))
                open_trade_snapshots[sym] = {
                    "id": curr_active.get("id"),
                    "entry_bar": trader.bar_count,
                    "entry_time": ts_str,
                    "side": curr_active["side"],
                    "entry_price": curr_active["entry_price"],
                    "stop_loss": curr_active["stop_loss"],
                    "initial_stop_loss": curr_active.get("initial_stop_loss", curr_active["stop_loss"]),
                    "take_profit": curr_active["take_profit"],
                    "tp1_price": curr_active.get("tp1_price"),
                    "avg_vol_at_entry": avg_v_before,
                }
                if hifi:
                    hifi_res = replay_trade_1m(sym, curr_active, t_ms)
                    if hifi_res:
                        active_hifi_exits[sym] = hifi_res

        # Step D: For HiFi coins that were held throughout this bar without exiting
        if hifi:
            for sym in active_symbols:
                if active_hifi_exits[sym] is not None and sym not in ranked_idle:
                    c = candles_15m_map[sym][t_ms]
                    candle_5 = (c[1], c[2], c[3], c[4], c[5])
                    trader = traders[sym]
                    trader.bar_count += 1
                    trader.candles.append(candle_5)
                    all_closes = [cand[3] for cand in trader.candles]
                    if len(all_closes) >= 15:
                        trader.rsi_history.append(calculate_rsi(all_closes, period=14))

        # Update volume deque for all coins
        for sym in active_symbols:
            recent_volumes[sym].append(candles_15m_map[sym][t_ms][5])

        # Step E: Telemetry & Capacity Utilization
        active_pos_count = risk_manager.get_open_positions_count()
        capacity_counts[min(active_pos_count, 3)] += 1

        # Step F: Mark-to-Market (M2M) Bar-by-Bar Equity & Drawdown Tracking
        bar_open_r_sum = 0.0
        bar_worst_r_sum = 0.0
        for sym in active_symbols:
            if traders[sym].active_trade is not None and open_trade_snapshots[sym] is not None:
                snap = open_trade_snapshots[sym]
                c_bar = candles_15m_map[sym][t_ms]
                c_high = c_bar[2]
                c_low = c_bar[3]
                c_close = c_bar[4]
                e_price = snap["entry_price"]
                init_sl = snap.get("initial_stop_loss", snap["stop_loss"])
                r_dist = abs(e_price - init_sl)
                if r_dist <= 0:
                    r_dist = e_price * 0.01

                if snap["side"] == "LONG":
                    floating_r = (c_close - e_price) / r_dist
                    adverse_r = (c_low - e_price) / r_dist
                else:
                    floating_r = (e_price - c_close) / r_dist
                    adverse_r = (e_price - c_high) / r_dist

                adverse_r = max(-1.0, adverse_r)
                bar_open_r_sum += floating_r
                bar_worst_r_sum += adverse_r

        current_m2m_eq = realized_port_equity * (1.0 + bar_open_r_sum * 0.01)
        m2m_equity_curve.append(current_m2m_eq)
        if current_m2m_eq > m2m_peak:
            m2m_peak = current_m2m_eq
        curr_m2m_dd = ((m2m_peak - current_m2m_eq) / m2m_peak) * 100.0
        if curr_m2m_dd > max_m2m_dd:
            max_m2m_dd = curr_m2m_dd

        current_intra_eq = realized_port_equity * (1.0 + bar_worst_r_sum * 0.01)
        curr_intra_dd = ((m2m_peak - current_intra_eq) / m2m_peak) * 100.0
        if curr_intra_dd > max_intra_dd:
            max_intra_dd = curr_intra_dd

    evaluated_bars = len(common_ts) - warmup_period
    mode_str = "PORTFOLIO (MAX 3 POS, 3-BAR COOLDOWN)"
    if htf_filter:
        mode_str += " | 4H HTF GATE"
    if tf1h_filter:
        mode_str += " | 1H GATE"
    if micro_filter:
        mode_str += " | MICROSTRUCTURE"
    if adaptive_sl:
        mode_str += " | ADAPTIVE SL"
    if market_structure:
        mode_str += " | BOS/CHOCH STRUCTURE"

    print("=" * 130)
    print(f" [PORTFOLIO AUDIT REPORT] | {len(active_symbols)} ASSETS | EVALUATED BARS: {evaluated_bars:,} | TOTAL TRADES: {len(portfolio_trade_logs)}")
    print(f" MODE: {mode_str}")
    print("=" * 130)

    if not portfolio_trade_logs:
        print("[AUDIT] No trades were triggered across the entire portfolio during this evaluation window.")
        print("=" * 130 + "\n")
        return

    # Individual Coin Breakdown Table
    print("\n" + "-" * 130)
    print(f"{'SYMBOL':<10} | {'TRADES':<7} | {'WINS':<5} | {'LOSS':<5} | {'WIN RATE':<9} | {'PROFIT FAC':<11} | {'NET RET %':<10} | {'AVG R':<8} | {'MAX DD %':<9} | {'ADJ WR %':<9}")
    print("-" * 130)

    for sym in active_symbols:
        s_trades = [t for t in portfolio_trade_logs if t["symbol"] == sym]
        if not s_trades:
            print(f"{sym:<10} | {0:<7} | {0:<5} | {0:<5} | {'0.0%':<9} | {'0.00':<11} | {'+0.00%':<10} | {'+0.00R':<8} | {'0.00%':<9} | {'0.0%':<9}")
            continue

        s_wins = [t for t in s_trades if t["net_return_pct"] > 0]
        s_losses = [t for t in s_trades if t["net_return_pct"] <= 0]
        s_whales = [t for t in s_trades if t["classification"] == "WHALE_VOLUME_EXPANSION"]
        s_wr = (len(s_wins) / len(s_trades)) * 100.0
        s_net = sum(t["net_return_pct"] for t in s_trades)
        s_avg_r = sum(t["r_multiple"] for t in s_trades) / len(s_trades)

        s_gp = sum(t["net_return_pct"] for t in s_wins)
        s_gl = abs(sum(t["net_return_pct"] for t in s_losses))
        s_pf = (s_gp / s_gl) if s_gl > 0 else (99.9 if s_gp > 0 else 0.0)

        s_eq = [100.0]
        for t in s_trades:
            s_eq.append(s_eq[-1] * (1.0 + t["net_return_pct"] / 100.0))
        s_peak = s_eq[0]
        s_mdd = 0.0
        for eq in s_eq:
            if eq > s_peak:
                s_peak = eq
            dd = ((s_peak - eq) / s_peak) * 100.0
            if dd > s_mdd:
                s_mdd = dd

        adj_wr = (len(s_wins) / max(1, len(s_trades) - len(s_whales))) * 100.0 if (len(s_trades) - len(s_whales)) > 0 else s_wr

        print(
            f"{sym:<10} | {len(s_trades):<7} | {len(s_wins):<5} | {len(s_losses):<5} | {s_wr:>7.1f}% | "
            f"{s_pf:>10.2f} | {s_net:>+9.2f}% | {s_avg_r:>+6.2f}R | {s_mdd:>8.2f}% | {adj_wr:>7.1f}%"
        )
    print("-" * 130)

    tot_trades = len(portfolio_trade_logs)
    all_wins = [t for t in portfolio_trade_logs if t["net_return_pct"] > 0]
    all_losses = [t for t in portfolio_trade_logs if t["net_return_pct"] <= 0]
    tot_wr = (len(all_wins) / tot_trades) * 100.0 if tot_trades > 0 else 0.0
    tot_gp = sum(t["net_return_pct"] for t in all_wins)
    tot_gl = abs(sum(t["net_return_pct"] for t in all_losses))
    tot_pf = (tot_gp / tot_gl) if tot_gl > 0 else (99.9 if tot_gp > 0 else 0.0)
    tot_r = sum(t["r_multiple"] for t in portfolio_trade_logs)
    avg_r = tot_r / tot_trades if tot_trades > 0 else 0.0

    port_equity = [100.0]
    for t in portfolio_trade_logs:
        port_equity.append(port_equity[-1] * (1.0 + t["r_multiple"] * 0.01))

    port_peak = port_equity[0]
    port_mdd = 0.0
    for eq in port_equity:
        if eq > port_peak:
            port_peak = eq
        dd = ((port_peak - eq) / port_peak) * 100.0
        if dd > port_mdd:
            port_mdd = dd

    final_equity = port_equity[-1]
    net_port_return = ((final_equity - 100.0) / 100.0) * 100.0

    r_multiples = [t["r_multiple"] for t in portfolio_trade_logs]
    if len(r_multiples) > 1 and float(np.std(r_multiples)) > 0:
        port_sharpe = float(np.mean(r_multiples) / np.std(r_multiples)) * np.sqrt(tot_trades)
    else:
        port_sharpe = 0.0

    # Quantitative Expectancy & Risk Metrics
    win_r_list = [t["r_multiple"] for t in all_wins]
    loss_r_list = [t["r_multiple"] for t in all_losses]
    avg_win_r = float(np.mean(win_r_list)) if win_r_list else 0.0
    avg_loss_r = abs(float(np.mean(loss_r_list))) if loss_r_list else 0.0
    ev_per_trade = ((tot_wr / 100.0) * avg_win_r) - (((100.0 - tot_wr) / 100.0) * avg_loss_r)
    payoff_ratio = (avg_win_r / avg_loss_r) if avg_loss_r > 0 else 0.0

    annualized_factor = 365.0 / max(1.0, days_span)
    ann_return_pct = net_port_return * annualized_factor
    calmar_ratio = (ann_return_pct / port_mdd) if port_mdd > 0 else 0.0
    m2m_calmar_ratio = (ann_return_pct / max_m2m_dd) if max_m2m_dd > 0 else 0.0

    print("\n [SYNCHRONIZED PORTFOLIO SUMMARY]")
    print(f" TOTAL PORTFOLIO TRADES:   {tot_trades:<6} | OVERALL WIN RATE:        {tot_wr:5.1f}% ({len(all_wins)}W / {len(all_losses)}L)")
    print(f" PORTFOLIO PROFIT FACTOR: {tot_pf:<6.2f} | CUMULATIVE R-MULTIPLE:    {tot_r:+6.2f}R")
    print(f" EXPECTED VALUE (EV / T): {ev_per_trade:+6.2f}R | WIN/LOSS PAYOFF RATIO:  {payoff_ratio:5.2f}")
    print(f" PORTFOLIO NET RETURN:     {net_port_return:+6.2f}% (1.0% equity risk per trade)")
    print(f" CLOSED-TRADE MAX DD:      {port_mdd:5.2f}% | TRADE-LEVEL SHARPE:      {port_sharpe:5.2f}")
    print(f" MARK-TO-MARKET MAX DD:    {max_m2m_dd:5.2f}% | WORST INTRA-BAR DD:     {max_intra_dd:5.2f}%")
    print(f" ANNUALIZED RETURN:       {ann_return_pct:+6.2f}% | CALMAR RATIO (M2M):     {m2m_calmar_ratio:5.2f} (Closed: {calmar_ratio:.2f})")

    print("\n [CAPITAL & CAPACITY UTILIZATION (% of time)]")
    p0 = (capacity_counts[0] / max(1, evaluated_bars)) * 100.0
    p1 = (capacity_counts[1] / max(1, evaluated_bars)) * 100.0
    p2 = (capacity_counts[2] / max(1, evaluated_bars)) * 100.0
    p3 = (capacity_counts[3] / max(1, evaluated_bars)) * 100.0
    print(f"  - 0 Positions Active (Idle Cash):     {p0:5.1f}% ({capacity_counts[0]:,} bars)")
    print(f"  - 1 Position Active (Low Exposure):   {p1:5.1f}% ({capacity_counts[1]:,} bars)")
    print(f"  - 2 Positions Active (Moderate):      {p2:5.1f}% ({capacity_counts[2]:,} bars)")
    print(f"  - 3 Positions Active (100% Capacity): {p3:5.1f}% ({capacity_counts[3]:,} bars)")
    print(f"  - Signals Blocked by 3-Position Cap:  {signals_rejected_portfolio_cap} candidate setups")
    print(f"  - Signals Blocked by Symbol Cooldown: {signals_rejected_cooldown} candidate setups")

    if signals_rejected_portfolio_cap > 0:
        print("\n [PER-ASSET BLOCKED SIGNALS (Slot Cannibalization Attribution)]")
        for sym, b_count in sorted(blocked_candidates_by_sym.items(), key=lambda x: x[1], reverse=True):
            if b_count > 0:
                print(f"  - {sym:<8}: {b_count:>3} candidate setups blocked by 3-position cap")

    print("\n" + "-" * 130)
    print(f"{'ENTRY TIME':<16} | {'SYMBOL':<8} | {'SIDE':<5} | {'ENTRY':<10} | {'EXIT':<10} | {'REASON':<12} | {'NET %':<8} | {'R-MULT':<7} | {'VOL RATIO':<9} | {'CLASSIFICATION':<22}")
    print("-" * 130)

    for t in portfolio_trade_logs:
        class_str = t["classification"]
        if class_str == "WHALE_VOLUME_EXPANSION":
            class_str = "[!] WHALE SPIKE"
        elif class_str == "PROTECTED_WIN":
            class_str = "[+] BE WIN (+0.38R)"
        elif class_str == "TAKE_PROFIT":
            class_str = "[+] TARGET HIT"
        elif class_str == "TIME_STOP":
            class_str = "[-] TIME STOP"
        elif class_str == "SCRATCH_STOP":
            class_str = "[-] PARTIAL LOSS"
        else:
            class_str = "[-] NORMAL STOP"

        reason_disp = "TIME_STOP" if t["reason"] in ("TIME_STOP_UNDERWATER", "STAGNANCY_SCRATCH") else t["reason"]
        print(
            f"{t['entry_time']:<16} | {t['symbol']:<8} | {t['side']:<5} | {format_price(t['entry_price']):<10} | "
            f"{format_price(t['exit_price']):<10} | {reason_disp:<12} | {t['net_return_pct']:+7.2f}% | "
            f"{t['r_multiple']:+6.2f}R | {t['exit_vol_ratio']:>7.1f}x | {class_str:<22}"
        )
    print("=" * 130 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Institutional Crypto Strategy Backtester")
    parser.add_argument("--portfolio", action="store_true", help="Run synchronized multi-coin portfolio backtest across top 10 assets")
    parser.add_argument("--tier1-only", action="store_true", help="Restrict portfolio to verified Tier-1 Alpha assets (BTC, SOL, AVAX, ADA, XRP)")
    parser.add_argument("--symbols", type=str, default=",".join(DEFAULT_PORTFOLIO_SYMBOLS), help="Comma-separated symbols for portfolio mode")
    parser.add_argument("--symbol", type=str, default="XRPUSDT", help="Symbol to backtest in single-asset mode (e.g. XRPUSDT)")
    parser.add_argument("--timeframe", type=str, default="15m", help="Candle timeframe interval (e.g. 15m, 1h)")
    parser.add_argument("--candles", type=int, default=2500, help="Total historical candles to ingest (e.g. 2500, 10000, 15000)")
    parser.add_argument("--fresh", action="store_true", help="Bypass local SQLite cache and fetch fresh candles from Binance")
    parser.add_argument("--hifi", action="store_true", help="Enable 1-minute intra-bar resolution replay")
    parser.add_argument("--strict-retest", action="store_true", help="Require strict FVG retest mitigation before opening trade")
    parser.add_argument("--adaptive-sl", action="store_true", help="Enable asset-adaptive volatility floor (BTC: 0.50%%, SOL: 0.80%%, alts: 1.20%%)")
    parser.add_argument("--tf1h-filter", action="store_true", help="Enable 1-hour intermediate structure gating")
    parser.add_argument("--htf-filter", action="store_true", help="Enable 4-hour higher-timeframe trend gating")
    parser.add_argument("--micro", action="store_true", help="Enable historical Open Interest & Funding Rate microstructure gating")
    parser.add_argument("--market-structure", action="store_true", help="Enable Market Structure BOS/CHoCH alignment gate")
    parser.add_argument("--fibonacci-ote", action="store_true", help="Enable Pillar 2 Fibonacci Optimal Trade Entry (OTE) and Discount/Premium pricing matrix gate")
    parser.add_argument("--order-blocks", action="store_true", help="Enable Pillar 3 Institutional Order Blocks (OB) and Breaker Blocks (BB) confluence multiplier")
    args = parser.parse_args()

    if args.portfolio:
        if args.tier1_only:
            sym_list = TIER1_ALPHA_SYMBOLS
        else:
            sym_list = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        run_portfolio_backtest(
            symbols=sym_list,
            timeframe=args.timeframe,
            total_candles=args.candles,
            hifi=args.hifi,
            strict_retest=args.strict_retest,
            adaptive_sl=args.adaptive_sl,
            htf_filter=args.htf_filter,
            tf1h_filter=args.tf1h_filter,
            micro_filter=args.micro,
            market_structure=args.market_structure,
            fibonacci_ote=args.fibonacci_ote,
            order_blocks=args.order_blocks,
            force_fresh=args.fresh,
        )
    else:
        run_strategy_backtest(
            symbol=args.symbol,
            timeframe=args.timeframe,
            total_candles=args.candles,
            hifi=args.hifi,
            strict_retest=args.strict_retest,
            adaptive_sl=args.adaptive_sl,
            htf_filter=args.htf_filter,
            tf1h_filter=args.tf1h_filter,
            micro_filter=args.micro,
            market_structure=args.market_structure,
            fibonacci_ote=args.fibonacci_ote,
            order_blocks=args.order_blocks,
            force_fresh=args.fresh,
        )


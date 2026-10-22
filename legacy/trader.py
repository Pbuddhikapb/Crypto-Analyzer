# Legacy, unvalidated, not used by the terminal.
import os
import sqlite3
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Sequence, Tuple, Union

import numpy as np
from indicators import (
    calculate_adx,
    calculate_atr,
    calculate_ema,
    calculate_rsi,
    check_bearish_fvg_retest,
    check_bullish_fvg_retest,
    check_fibonacci_ote_confluence,
    check_fvg_rejection_confirmation,
    check_market_overextension,
    check_market_structure_alignment,
    check_order_block_confluence,
    check_recent_bullish_fvg,
    check_recent_bearish_fvg,
    detect_rsi_divergences,
    format_price,
)

CandleInput = Union[Tuple[Any, ...], Sequence[Any]]

DEFAULT_ASSET_SL_FLOORS: Dict[str, float] = {
    "BTCUSDT": 0.0095,  # 0.95% (Tier 1: Mega-cap calibrated to ~2.2x 15m ATR, clearing $620 noise band)
    "ETHUSDT": 0.0110,  # 1.10% (Tier 1: Mega-cap)
    "BNBUSDT": 0.0105,  # 1.05% (Tier 2: Large-cap calibrated to ~2.2x 15m ATR)
    "SOLUSDT": 0.0120,  # 1.20% (Tier 2: Large-cap)
    "XRPUSDT": 0.0125,  # 1.25% (Tier 2: Large-cap - Proven 76% Golden Baseline)
    "AVAXUSDT": 0.0135, # 1.35% (Tier 3: Mid-cap)
    "ADAUSDT": 0.0130,  # 1.30% (Tier 3: Mid-cap)
    "LINKUSDT": 0.0140, # 1.40% (Tier 3: Mid-cap)
    "SUIUSDT": 0.0165,  # 1.65% (Tier 3: High-beta momentum)
    "DOGEUSDT": 0.0175, # 1.75% (Tier 3: High-dispersion)
}


class PaperTrader:
    _initialized_dbs: set = set()

    @staticmethod
    def _parse_timeframe_to_minutes(tf: str) -> int:
        """Parse Binance timeframe string (e.g. '15m', '1h', '4h', '1d') into interval minutes."""
        if not tf:
            return 15
        tf_str = str(tf).strip().lower()
        try:
            if tf_str.endswith("m"):
                return max(1, int(tf_str[:-1]))
            elif tf_str.endswith("h"):
                return max(1, int(tf_str[:-1]) * 60)
            elif tf_str.endswith("d"):
                return max(1, int(tf_str[:-1]) * 1440)
            elif tf_str.endswith("w"):
                return max(1, int(tf_str[:-1]) * 10080)
            return max(1, int(tf_str))
        except Exception:
            return 15

    def __init__(
        self,
        symbol: str = "BTCUSDT",
        timeframe: str = "1m",
        db_path: str = "data/signals_audit.db",
        risk_manager: Optional[Any] = None,
        micro_mgr: Optional[Any] = None,
        require_fvg_retest: bool = False,
        adaptive_sl_floor: bool = False,
        min_sl_pct: Optional[float] = None,
        htf_trend_filter: bool = False,
        intermediate_1h_filter: bool = False,
        confirm_fvg_rejection: bool = False,
        tp1_ratio: float = 0.80,
        require_market_structure: bool = False,
        require_fibonacci_ote: bool = False,
        enable_order_blocks: bool = False,
        enable_collinearity_cap: bool = False,
        order_book_engine: Optional[Any] = None,
        max_stagnant_bars: int = 12,
        interval_minutes: Optional[int] = None,
    ):
        self.symbol = symbol
        self.timeframe = timeframe
        self.interval_minutes: int = (
            int(interval_minutes)
            if interval_minutes is not None
            else self._parse_timeframe_to_minutes(self.timeframe)
        )
        self.db_path = db_path
        self.risk_manager = risk_manager
        self.micro_mgr = micro_mgr
        self.order_book_engine = order_book_engine
        self.require_fvg_retest = require_fvg_retest
        self.confirm_fvg_rejection = confirm_fvg_rejection
        self.require_market_structure = require_market_structure
        self.require_fibonacci_ote = require_fibonacci_ote
        self.enable_order_blocks = enable_order_blocks
        self.enable_collinearity_cap = enable_collinearity_cap
        self.max_stagnant_bars = max_stagnant_bars
        self.adaptive_sl_floor = adaptive_sl_floor
        self.min_sl_pct = min_sl_pct if min_sl_pct is not None else (DEFAULT_ASSET_SL_FLOORS.get(symbol, 0.0120) if adaptive_sl_floor else 0.0120)
        self.tp1_ratio = tp1_ratio
        self.htf_trend_filter = htf_trend_filter
        self.htf_trend: Optional[str] = None
        self.htf_candles: deque = deque(maxlen=250)
        self.intermediate_1h_filter = intermediate_1h_filter
        self.trend_1h: Optional[str] = None
        self.candles_1h: deque = deque(maxlen=250)

        # Rolling buffers & monotonic bar counter
        self.candles: deque = deque(maxlen=250)
        self.rsi_history: deque = deque(maxlen=5)
        self.bar_count: int = 0

        # Active trade state
        self.active_trade: Optional[Dict[str, Any]] = None
        self.pending_retest: Optional[Dict[str, Any]] = None
        self.trade_closed_this_bar: bool = False
        self.in_chop_regime: bool = False

        # Initialize SQLite database and resume any open trade
        self._init_db()

    def get_min_sl_dist(self, entry_price: float) -> float:
        """Calculate minimum stop-loss distance according to asset volatility floor."""
        return entry_price * self.min_sl_pct

    def get_whale_threshold(self, symbol: Optional[str] = None) -> float:
        """Calculate dynamic whale net delta threshold scaled by asset capitalization tier."""
        sym = (symbol or self.symbol).upper()
        if "BTC" in sym:
            return 500_000.0
        elif any(t in sym for t in ("ETH", "SOL", "BNB", "XRP")):
            return 150_000.0
        else:
            return 50_000.0

    def check_order_book_liquidity_gate(
        self,
        side: str,
        entry_price: float,
        atr: float,
        tp1_price: float,
    ) -> Tuple[bool, str]:
        """Audit live L2 Order Book depth state to shield execution from adverse liquidity cliffs.

        Veto Rules:
        1. Opposing Wall Proximity:
           - LONG: Check if an ask wall (resting sell wall >= 2.5x mean) exists between entry and entry + 0.5 * ATR,
             or between entry and TP1. If an opposing wall directly blocks entry or path to TP1, veto.
           - SHORT: Check if a bid wall exists between entry - 0.5 * ATR and entry,
             or between TP1 and entry. If an opposing wall directly blocks entry or path to TP1, veto.
        2. Severe Order Book Imbalance (OBI):
           - LONG: If OBI < -25% (heavy sell depth skew), veto.
           - SHORT: If OBI > +25% (heavy buy depth skew), veto.

        Returns:
            (allowed: bool, reason: str)
        """
        if self.order_book_engine is None:
            return True, "OK (No OrderBookEngine configured)"

        snapshot = self.order_book_engine.get_snapshot(self.symbol, fallback_price=entry_price)
        if snapshot is None:
            return True, "OK (OrderBook snapshot unavailable)"

        side_upper = side.upper()
        # 1. Opposing Wall Checks
        if side_upper == "LONG":
            # Check ask walls within 0.5 * ATR or between entry and TP1
            wall_ceiling = max(entry_price + (0.5 * atr), tp1_price)
            for wall_price, wall_qty in getattr(snapshot, "ask_walls", ()):
                if entry_price <= wall_price <= wall_ceiling:
                    return False, f"Opposing Ask Wall detected at ${wall_price:,.2f} (qty: {wall_qty:,.2f}) within target path"

            # Check severe negative OBI (heavy ask volume pushing down)
            imbalance = getattr(snapshot, "imbalance_pct", 0.0)
            if imbalance < -25.0:
                return False, f"Heavy Ask Imbalance (OBI={imbalance:.1f}% < -25.0%)"

        elif side_upper == "SHORT":
            # Check bid walls within 0.5 * ATR or between entry and TP1
            wall_floor = min(entry_price - (0.5 * atr), tp1_price)
            for wall_price, wall_qty in getattr(snapshot, "bid_walls", ()):
                if wall_floor <= wall_price <= entry_price:
                    return False, f"Opposing Bid Wall detected at ${wall_price:,.2f} (qty: {wall_qty:,.2f}) within target path"

            # Check severe positive OBI (heavy bid volume pushing up)
            imbalance = getattr(snapshot, "imbalance_pct", 0.0)
            if imbalance > 25.0:
                return False, f"Heavy Bid Imbalance (OBI={imbalance:.1f}% > +25.0%)"

        return True, "OK"

    def set_htf_trend(self, trend: Optional[str]) -> None:
        """Explicitly set or mock the 4h higher-timeframe trend ('BULLISH', 'BEARISH', 'NEUTRAL', or None)."""
        self.htf_trend = trend.upper() if trend else None

    def get_htf_trend(self) -> Optional[str]:
        """Resolve current HTF trend, computing from htf_candles if not explicitly set."""
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
                # Dynamic buffer: Holding above EMA200 during active bull alignment is BULLISH (pullback discount)
                return "BULLISH" if curr_close >= ema_200 else "NEUTRAL"
            elif ema_50 < ema_200:
                # Holding below EMA200 during active bear alignment is BEARISH
                return "BEARISH" if curr_close <= ema_200 else "NEUTRAL"
            return "NEUTRAL"
        else:
            if curr_close >= ema_50:
                return "BULLISH"
            elif curr_close < ema_50:
                return "BEARISH"
            return "NEUTRAL"

    def load_historical_htf_candle(self, candle_data: CandleInput) -> None:
        """Load historical 4h candle for HTF trend priming."""
        self.htf_candles.append(candle_data)

    def on_htf_candle(self, candle_data: CandleInput) -> None:
        """Process incoming closed 4h candle."""
        self.htf_candles.append(candle_data)

    def set_1h_trend(self, trend: Optional[str]) -> None:
        """Explicitly set or mock the 1h intermediate trend ('BULLISH', 'BEARISH', 'NEUTRAL', or None)."""
        self.trend_1h = trend.upper() if trend else None

    def get_1h_trend(self) -> Optional[str]:
        """Resolve current 1h intermediate trend, computing from candles_1h if not explicitly set."""
        if self.trend_1h is not None:
            return self.trend_1h
        if len(self.candles_1h) < 20:
            return None

        closes = [c[3] for c in self.candles_1h]
        curr_close = closes[-1]
        if len(closes) >= 50:
            ema_20 = calculate_ema(closes, 20)
            ema_50 = calculate_ema(closes, 50)
            if ema_20 > ema_50:
                # 1h Buffer: Holding above 1h EMA50 in a bull alignment is healthy BULLISH pullback
                return "BULLISH" if curr_close >= ema_50 else "NEUTRAL"
            elif ema_20 < ema_50:
                # Holding below 1h EMA50 in a bear alignment is BEARISH
                return "BEARISH" if curr_close <= ema_50 else "NEUTRAL"
            return "NEUTRAL"
        else:
            ema_20 = calculate_ema(closes, 20)
            if curr_close >= ema_20:
                return "BULLISH"
            elif curr_close < ema_20:
                return "BEARISH"
            return "NEUTRAL"

    def load_historical_1h_candle(self, candle_data: CandleInput) -> None:
        """Load historical 1h candle for intermediate structure priming."""
        self.candles_1h.append(candle_data)

    def on_1h_candle(self, candle_data: CandleInput) -> None:
        """Process incoming closed 1h candle."""
        self.candles_1h.append(candle_data)

    @contextmanager
    def _get_connection(self):
        """Create a database connection with 30s busy timeout, ensuring automatic cleanup."""
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=30000;")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def checkpoint(self) -> None:
        """Flush WAL journal changes into the main database file."""
        with self._get_connection() as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")

    def _update_trade_stop_loss(self, trade_id: int, new_sl: float, be_armed: bool = True) -> None:
        """Update the stop loss level and breakeven status for an active trade in SQLite database."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE paper_trades SET stop_loss = ?, be_armed = ? WHERE id = ?",
                (new_sl, 1 if be_armed else 0, trade_id),
            )
            now_iso = datetime.now(timezone.utc).isoformat()
            cursor.execute(
                "UPDATE synthetic_oco_orders SET trigger_price = ?, updated_at = ? WHERE trade_id = ? AND order_type = 'STOP_LOSS' AND status = 'ACTIVE'",
                (new_sl, now_iso, trade_id),
            )
            conn.commit()

    def _update_trade_tp1_hit(self, trade_id: int) -> None:
        """Record that TP1 partial profit (50%) has been locked in SQLite database."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE paper_trades SET tp1_hit = 1 WHERE id = ?",
                (trade_id,),
            )
            now_iso = datetime.now(timezone.utc).isoformat()
            cursor.execute(
                "UPDATE synthetic_oco_orders SET status = 'FILLED', updated_at = ? WHERE trade_id = ? AND order_type = 'TAKE_PROFIT_1' AND status = 'ACTIVE'",
                (now_iso, trade_id),
            )
            conn.commit()

    def _register_synthetic_oco_bracket(
        self,
        trade_id: int,
        symbol: str,
        side: str,
        sl: float,
        tp2: float,
        tp1: Optional[float],
        qty: float,
        timestamp: str,
    ) -> None:
        """Register active synthetic Stop-Loss and Take-Profit bracket orders in SQLite."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO synthetic_oco_orders (
                    trade_id, symbol, side, order_type, trigger_price, quantity, status, created_at
                ) VALUES (?, ?, ?, 'STOP_LOSS', ?, ?, 'ACTIVE', ?)
                """,
                (trade_id, symbol, side, sl, qty, timestamp),
            )
            if tp1 is not None:
                cursor.execute(
                    """
                    INSERT INTO synthetic_oco_orders (
                        trade_id, symbol, side, order_type, trigger_price, quantity, status, created_at
                    ) VALUES (?, ?, ?, 'TAKE_PROFIT_1', ?, ?, 'ACTIVE', ?)
                    """,
                    (trade_id, symbol, side, tp1, qty * 0.5, timestamp),
                )
            cursor.execute(
                """
                INSERT INTO synthetic_oco_orders (
                    trade_id, symbol, side, order_type, trigger_price, quantity, status, created_at
                ) VALUES (?, ?, ?, 'TAKE_PROFIT_2', ?, ?, 'ACTIVE', ?)
                """,
                (trade_id, symbol, side, tp2, qty * 0.5 if tp1 is not None else qty, timestamp),
            )
            conn.commit()

    def _resolve_synthetic_oco_bracket(
        self,
        trade_id: int,
        reason: str,
        timestamp: str,
    ) -> None:
        """Update synthetic OCO bracket orders when a leg is filled or trade is closed."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if "TAKE_PROFIT" in reason:
                cursor.execute(
                    "UPDATE synthetic_oco_orders SET status = 'FILLED', updated_at = ? WHERE trade_id = ? AND order_type = 'TAKE_PROFIT_2' AND status = 'ACTIVE'",
                    (timestamp, trade_id),
                )
            elif "STOP_LOSS" in reason or "BREAKEVEN" in reason or "PARTIAL_LOSS" in reason or "STAGNANCY" in reason or "PREEMPTION" in reason:
                cursor.execute(
                    "UPDATE synthetic_oco_orders SET status = 'FILLED', updated_at = ? WHERE trade_id = ? AND order_type = 'STOP_LOSS' AND status = 'ACTIVE'",
                    (timestamp, trade_id),
                )
            cursor.execute(
                "UPDATE synthetic_oco_orders SET status = 'CANCELLED_BY_OCO', updated_at = ? WHERE trade_id = ? AND status = 'ACTIVE'",
                (timestamp, trade_id),
            )
            conn.commit()

    def preempt_trade(
        self,
        exit_price: float,
        exit_timestamp: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Preemptively scratch an active stagnant position to reallocate the slot to an elite setup."""
        if self.active_trade is None or exit_price <= 0.0:
            return None

        if exit_timestamp is None:
            exit_timestamp = datetime.now(timezone.utc).isoformat()

        entry = self.active_trade["entry_price"]
        sl = self.active_trade.get("initial_stop_loss", self.active_trade.get("stop_loss", entry))
        risk_dist = abs(entry - sl)
        side = self.active_trade.get("side", "LONG")
        trade_id = self.active_trade.get("id")

        gross_ret = ((exit_price - entry) / entry) * 100.0 if side == "LONG" else ((entry - exit_price) / entry) * 100.0
        net_return_pct = gross_ret - 0.15
        r_multiple = ((net_return_pct / 100.0) * entry) / risk_dist if risk_dist > 0.0 else 0.0

        closed_snapshot = {
            "id": trade_id,
            "symbol": self.symbol,
            "side": side,
            "entry_price": entry,
            "exit_price": exit_price,
            "exit_timestamp": exit_timestamp,
            "reason": "PREEMPTION_REALLOCATION",
            "net_return_pct": net_return_pct,
            "r_multiple": r_multiple,
        }

        self._record_closed_trade(
            exit_timestamp=exit_timestamp,
            exit_price=exit_price,
            reason="PREEMPTION_REALLOCATION",
            net_return_pct=net_return_pct,
            r_multiple=r_multiple,
        )

        if self.risk_manager is not None:
            self.risk_manager.record_position_closed(self.symbol, self.bar_count)

        self.active_trade = None
        self.trade_closed_this_bar = True
        return closed_snapshot

    def _init_db(self) -> None:
        db_dir = os.path.dirname(self.db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

        with self._get_connection() as conn:
            cursor = conn.cursor()

            if self.db_path not in PaperTrader._initialized_dbs:
                conn.execute("PRAGMA journal_mode=WAL;")
                # Inspect columns if table already exists
                cursor.execute("PRAGMA table_info(paper_trades)")
                existing_cols = [col[1] for col in cursor.fetchall()]

                if existing_cols and "symbol" not in existing_cols:
                    # Recreate table with complete forward-compatible schema
                    cursor.execute("DROP TABLE paper_trades")

                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS paper_trades (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        symbol TEXT NOT NULL,
                        timeframe TEXT NOT NULL,
                        side TEXT NOT NULL,
                        status TEXT NOT NULL,
                        timestamp TEXT NOT NULL,
                        entry_price REAL NOT NULL,
                        stop_loss REAL NOT NULL,
                        initial_stop_loss REAL,
                        take_profit REAL NOT NULL,
                        exit_price REAL,
                        exit_timestamp TEXT,
                        reason TEXT,
                        net_return_pct REAL,
                        r_multiple REAL,
                        tp1_price REAL,
                        tp1_hit INTEGER DEFAULT 0,
                        be_armed INTEGER DEFAULT 0,
                        entry_bar INTEGER DEFAULT 0,
                        quantity REAL,
                        notional_usd REAL,
                        leverage REAL
                    )
                    """
                )

                # Migration: Add missing columns if table already existed without them
                cursor.execute("PRAGMA table_info(paper_trades)")
                current_cols = [col[1] for col in cursor.fetchall()]
                for col_name, col_type in [
                    ("tp1_price", "REAL"),
                    ("tp1_hit", "INTEGER DEFAULT 0"),
                    ("be_armed", "INTEGER DEFAULT 0"),
                    ("entry_bar", "INTEGER DEFAULT 0"),
                    ("initial_stop_loss", "REAL"),
                    ("quantity", "REAL"),
                    ("notional_usd", "REAL"),
                    ("leverage", "REAL"),
                ]:
                    if current_cols and col_name not in current_cols:
                        cursor.execute(f"ALTER TABLE paper_trades ADD COLUMN {col_name} {col_type}")

                cursor.execute("CREATE INDEX IF NOT EXISTS idx_paper_trades_sym_status ON paper_trades(symbol, status)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_paper_trades_sym_tf_status ON paper_trades(symbol, timeframe, status)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_paper_trades_status ON paper_trades(status)")

                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS synthetic_oco_orders (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        trade_id INTEGER NOT NULL,
                        symbol TEXT NOT NULL,
                        side TEXT NOT NULL,
                        order_type TEXT NOT NULL,
                        trigger_price REAL NOT NULL,
                        quantity REAL,
                        status TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT,
                        FOREIGN KEY(trade_id) REFERENCES paper_trades(id)
                    )
                    """
                )
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_oco_trade_status ON synthetic_oco_orders(trade_id, status)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_oco_sym_status ON synthetic_oco_orders(symbol, status)")
                PaperTrader._initialized_dbs.add(self.db_path)

            # Check if there is an existing OPEN trade for this symbol and timeframe to resume
            cursor.execute(
                """
                SELECT id, symbol, timeframe, side, status, timestamp, entry_price, stop_loss, take_profit,
                       tp1_price, tp1_hit, be_armed, entry_bar, quantity, notional_usd, leverage
                FROM paper_trades
                WHERE symbol = ? AND timeframe = ? AND status = 'OPEN'
                ORDER BY id DESC LIMIT 1
                """,
                (self.symbol, self.timeframe),
            )
            row = cursor.fetchone()
            if row:
                self.active_trade = {
                    "id": row[0],
                    "symbol": row[1],
                    "timeframe": row[2],
                    "side": row[3],
                    "status": row[4],
                    "timestamp": row[5],
                    "entry_price": row[6],
                    "stop_loss": row[7],
                    "take_profit": row[8],
                    "tp1_price": row[9] if len(row) > 9 else None,
                    "tp1_hit": bool(row[10]) if len(row) > 10 and row[10] is not None else False,
                    "be_armed": bool(row[11]) if len(row) > 11 and row[11] is not None else False,
                    "entry_bar": row[12] if len(row) > 12 and row[12] is not None else self.bar_count,
                    "risk_dist": abs(row[6] - row[7]),
                    "breakeven_set": bool(row[11]) if len(row) > 11 and row[11] is not None else False,
                    "quantity": float(row[13]) if len(row) > 13 and row[13] is not None else 0.0,
                    "notional_usd": float(row[14]) if len(row) > 14 and row[14] is not None else 0.0,
                    "leverage": float(row[15]) if len(row) > 15 and row[15] is not None else 0.0,
                }
                print(
                    f"[{self.symbol}] Resumed active {row[3]} trade from database | "
                    f"Entry: {format_price(row[6])} | SL: {format_price(row[7])} | TP: {format_price(row[8])}"
                )

            conn.commit()

    def load_historical_candle(self, candle: CandleInput) -> None:
        """Load a historical candle into rolling buffers without evaluating entry triggers.
        
        If an active trade was resumed from the database on startup, checks whether this
        historical candle triggered its exit conditions while the application was offline.
        """
        if len(candle) == 6:
            c_ts, o, h, l, c, v = candle
            candle_data = (float(o), float(h), float(l), float(c), float(v))
            ts_str = str(c_ts)
        elif len(candle) == 5:
            o, h, l, c, v = candle
            candle_data = (float(o), float(h), float(l), float(c), float(v))
            ts_str = datetime.now(timezone.utc).isoformat()
        else:
            raise ValueError(f"Expected candle tuple of length 5 or 6, got {len(candle)}")

        self.candles.append(candle_data)
        self.bar_count += 1
        all_closes = [c[3] for c in self.candles]
        if len(all_closes) >= 15:
            current_rsi = calculate_rsi(all_closes, period=14)
            self.rsi_history.append(current_rsi)

        # Check offline exit reconciliation if an active trade was resumed
        if self.active_trade is not None:
            candle_high = candle_data[1]
            candle_low = candle_data[2]
            tp = self.active_trade["take_profit"]
            sl = self.active_trade["stop_loss"]
            entry = self.active_trade["entry_price"]
            side = self.active_trade.get("side", "LONG") or "LONG"

            if side == "LONG":
                take_profit_hit = candle_high >= tp
                stop_loss_hit = candle_low <= sl
            else:  # SHORT
                take_profit_hit = candle_low <= tp
                stop_loss_hit = candle_high >= sl

            exit_reason = None
            exit_price = None

            if stop_loss_hit and take_profit_hit:
                exit_reason = "OFFLINE_STOP_LOSS"
                candle_open = candle_data[0]
                exit_price = min(candle_open, sl) if side == "LONG" else max(candle_open, sl)
            elif stop_loss_hit:
                exit_reason = "OFFLINE_STOP_LOSS"
                candle_open = candle_data[0]
                exit_price = min(candle_open, sl) if side == "LONG" else max(candle_open, sl)
            elif take_profit_hit:
                exit_reason = "OFFLINE_TAKE_PROFIT"
                exit_price = tp

            if exit_reason is not None and exit_price is not None:
                if side == "LONG":
                    gross_return_pct = ((exit_price - entry) / entry) * 100.0
                    risk_per_unit = entry - sl
                else:
                    gross_return_pct = ((entry - exit_price) / entry) * 100.0
                    risk_per_unit = sl - entry

                net_return_pct = gross_return_pct - 0.15
                if risk_per_unit > 0.0:
                    net_dollar_gain_per_unit = (net_return_pct / 100.0) * entry
                    r_multiple = net_dollar_gain_per_unit / risk_per_unit
                else:
                    r_multiple = 0.0

                self._record_closed_trade(
                    exit_timestamp=ts_str,
                    exit_price=exit_price,
                    reason=exit_reason,
                    net_return_pct=net_return_pct,
                    r_multiple=r_multiple,
                )
                if self.risk_manager is not None:
                    self.risk_manager.record_position_closed(self.symbol, self.bar_count)
                self.active_trade = None

    def _record_closed_trade(
        self,
        exit_timestamp: str,
        exit_price: float,
        reason: str,
        net_return_pct: float,
        r_multiple: float,
    ) -> None:
        if self.active_trade is None:
            return

        trade_id = self.active_trade.get("id")
        entry_price = self.active_trade["entry_price"]
        side = self.active_trade.get("side", "LONG")

        with self._get_connection() as conn:
            cursor = conn.cursor()
            if trade_id:
                cursor.execute(
                    """
                    UPDATE paper_trades
                    SET status = 'CLOSED', exit_price = ?, exit_timestamp = ?, reason = ?, net_return_pct = ?, r_multiple = ?
                    WHERE id = ?
                    """,
                    (
                        exit_price,
                        exit_timestamp,
                        reason,
                        net_return_pct,
                        r_multiple,
                        trade_id,
                    ),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO paper_trades (
                        symbol, timeframe, side, status, timestamp, entry_price, stop_loss, take_profit,
                        exit_price, exit_timestamp, reason, net_return_pct, r_multiple
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        self.symbol,
                        self.timeframe,
                        side,
                        "CLOSED",
                        self.active_trade.get("timestamp", exit_timestamp),
                        entry_price,
                        self.active_trade["stop_loss"],
                        self.active_trade["take_profit"],
                        exit_price,
                        exit_timestamp,
                        reason,
                        net_return_pct,
                        r_multiple,
                    ),
                )
            conn.commit()

        if trade_id:
            self._resolve_synthetic_oco_bracket(trade_id, reason, exit_timestamp)

        print(
            f"[{self.symbol} {side} CLOSED] Reason: {reason} | Entry: {format_price(entry_price)} | "
            f"Exit: {format_price(exit_price)} | Net Return: {net_return_pct:.2f}% | R-Multiple: {r_multiple:.2f}R"
        )

    def _calculate_trade_returns(
        self,
        entry: float,
        exit_price: float,
        side: str,
        risk_dist: float,
        tp1_hit: bool,
        tp1_price: Optional[float],
        bars_held: int = 1,
        interval_minutes: Optional[int] = None,
    ) -> Tuple[float, float]:
        """Compute true dynamic gross return % and net R-multiple from real fill prices."""
        fee_pct = 0.15
        eff_interval = interval_minutes if interval_minutes is not None else getattr(self, "interval_minutes", 15)
        periods_8h = max(0, (bars_held * eff_interval) // 480)
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

    def on_tick(
        self,
        price: float,
        timestamp: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Process a real-time price tick for sub-second intra-candle exit monitoring.
        
        Evaluates real-time price against active trade TP1, TP2, Breakeven arming, and Stop Loss
        without waiting for the 15-minute candle to close.
        Returns a dict of closed trade details if an exit was executed, or None.
        """
        if self.active_trade is None or price <= 0.0:
            return None

        if timestamp is None:
            timestamp = datetime.now(timezone.utc).isoformat()

        entry = self.active_trade["entry_price"]
        # Basic sanity corridor: ignore impossible price spikes (>15% deviation from entry in single tick)
        if abs(price - entry) / entry > 0.15:
            return None

        side = self.active_trade.get("side", "LONG") or "LONG"
        sl = self.active_trade["stop_loss"]
        tp2 = self.active_trade["take_profit"]
        tp1 = self.active_trade.get("tp1_price")
        tp1_hit = self.active_trade.get("tp1_hit", False)
        be_armed = self.active_trade.get("be_armed", False) or self.active_trade.get("breakeven_set", False)
        risk_dist = self.active_trade.get("risk_dist") or abs(entry - sl)
        trade_id = self.active_trade.get("id")

        curr_r = ((price - entry) / risk_dist) if side == "LONG" else ((entry - price) / risk_dist) if risk_dist > 0 else 0.0

        # Step 1: TP1 Partial Scale-Out Check (+0.85R)
        if tp1 is not None and not tp1_hit:
            if (price >= tp1 if side == "LONG" else price <= tp1):
                tp1_hit = True
                self.active_trade["tp1_hit"] = True
                if trade_id:
                    self._update_trade_tp1_hit(trade_id)
                # Immediately arm Breakeven
                if not be_armed:
                    be_armed = True
                    new_sl = entry * 1.0005 if side == "LONG" else entry * 0.9995
                    self.active_trade["stop_loss"] = new_sl
                    self.active_trade["be_armed"] = True
                    self.active_trade["breakeven_set"] = True
                    if trade_id:
                        self._update_trade_stop_loss(trade_id, new_sl, be_armed=True)

        # Step 2: Breakeven Arming Trigger (+1.00R)
        if (tp1_hit or tp1 is None) and not be_armed:
            if curr_r >= 1.00:
                be_armed = True
                new_sl = entry * 1.0005 if side == "LONG" else entry * 0.9995
                self.active_trade["stop_loss"] = new_sl
                self.active_trade["be_armed"] = True
                self.active_trade["breakeven_set"] = True
                if trade_id:
                    self._update_trade_stop_loss(trade_id, new_sl, be_armed=True)

        # Step 3: Check Stop Loss Hit & Take Profit 2 with Pessimistic Collision Resolution
        curr_sl = self.active_trade["stop_loss"]
        stop_loss_hit = (price <= curr_sl if side == "LONG" else price >= curr_sl)
        take_profit_hit = (price >= tp2 if side == "LONG" else price <= tp2)

        exit_reason = None
        exit_price = None
        net_return_pct = 0.0
        r_multiple = 0.0

        # Pessimistic collision resolution: If both touched, Stop Loss executes first!
        if stop_loss_hit and take_profit_hit:
            stop_loss_hit = True
            take_profit_hit = False

        if stop_loss_hit:
            exit_price = curr_sl
            if be_armed:
                exit_reason = "BREAKEVEN"
            elif tp1_hit:
                exit_reason = "PARTIAL_LOSS"
            else:
                exit_reason = "STOP_LOSS"

            net_return_pct, r_multiple = self._calculate_trade_returns(
                entry=entry,
                exit_price=exit_price,
                side=side,
                risk_dist=risk_dist,
                tp1_hit=tp1_hit,
                tp1_price=tp1,
                bars_held=1,
            )

        elif take_profit_hit:
            exit_price = tp2
            exit_reason = "TAKE_PROFIT"
            net_return_pct, r_multiple = self._calculate_trade_returns(
                entry=entry,
                exit_price=exit_price,
                side=side,
                risk_dist=risk_dist,
                tp1_hit=tp1_hit,
                tp1_price=tp1,
                bars_held=1,
            )

        if exit_reason is not None and exit_price is not None:
            closed_snapshot = {
                "id": trade_id,
                "symbol": self.symbol,
                "side": side,
                "entry_price": entry,
                "exit_price": exit_price,
                "exit_timestamp": timestamp,
                "reason": exit_reason,
                "net_return_pct": net_return_pct,
                "r_multiple": r_multiple,
            }
            self._record_closed_trade(
                exit_timestamp=timestamp,
                exit_price=exit_price,
                reason=exit_reason,
                net_return_pct=net_return_pct,
                r_multiple=r_multiple,
            )
            if self.risk_manager is not None:
                self.risk_manager.record_position_closed(self.symbol, self.bar_count)
            self.active_trade = None
            self.trade_closed_this_bar = True
            return closed_snapshot

        return None

    def on_candle(
        self,
        candle: CandleInput,
        timestamp: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Process a new closed candle.
        
        Candle formats supported:
        - (open, high, low, close, volume)
        - (timestamp, open, high, low, close, volume)
        """
        # Parse candle
        if len(candle) == 6:
            c_ts, o, h, l, c, v = candle
            candle_data = (float(o), float(h), float(l), float(c), float(v))
            if timestamp is None:
                timestamp = str(c_ts)
        elif len(candle) == 5:
            o, h, l, c, v = candle
            candle_data = (float(o), float(h), float(l), float(c), float(v))
            if timestamp is None:
                timestamp = datetime.now(timezone.utc).isoformat()
        else:
            raise ValueError(f"Expected candle tuple of length 5 or 6, got {len(candle)}")

        candle_open = candle_data[0]
        candle_high = candle_data[1]
        candle_low = candle_data[2]
        candle_close = candle_data[3]
        candle_vol = candle_data[4]  # noqa: F841  # Candle volume unpacked from tuple for potential volume filtering / sizing

        # Increment monotonic bar counter for this arriving candle
        self.bar_count += 1
        closed_during_this_interval = getattr(self, "trade_closed_this_bar", False)
        self.trade_closed_this_bar = False
        trade_closed_this_candle = False

        # 1. Check open trade exit conditions first
        if self.active_trade is not None:
            tp2 = self.active_trade["take_profit"]
            sl_at_bar_start = self.active_trade["stop_loss"]
            sl = sl_at_bar_start
            entry = self.active_trade["entry_price"]
            side = self.active_trade.get("side", "LONG") or "LONG"
            risk_dist = self.active_trade.get("risk_dist") or abs(entry - sl_at_bar_start)
            tp1 = self.active_trade.get("tp1_price")
            tp1_hit = self.active_trade.get("tp1_hit", False)
            be_armed = self.active_trade.get("be_armed", False) or self.active_trade.get("breakeven_set", False)
            trade_id = self.active_trade.get("id")
            entry_bar = self.active_trade.get("entry_bar", self.bar_count)
            bars_held = self.bar_count - entry_bar

            # Current excursion in R units
            mfe_r = ((candle_high - entry) / risk_dist) if side == "LONG" else ((entry - candle_low) / risk_dist) if risk_dist > 0 else 0.0
            close_r = ((candle_close - entry) / risk_dist) if side == "LONG" else ((entry - candle_close) / risk_dist) if risk_dist > 0 else 0.0

            # Step A: Check TP1 Partial Scale-Out (50% position profit locked at +0.85R)
            if tp1 is not None and not tp1_hit:
                if (candle_high >= tp1 if side == "LONG" else candle_low <= tp1):
                    tp1_hit = True
                    self.active_trade["tp1_hit"] = True
                    if trade_id:
                        self._update_trade_tp1_hit(trade_id)
                    print(
                        f"[{self.symbol} {side} TP1 REACHED] 50% partial profit banked at {format_price(tp1)} (+0.85R) | "
                        f"Remaining 50% targeting {format_price(tp2)} (+1.75R)"
                    )
                    # Immediately arm Breakeven upon TP1 to eliminate reverse risk
                    if not be_armed:
                        be_armed = True
                        new_sl = entry * 1.0005 if side == "LONG" else entry * 0.9995
                        self.active_trade["stop_loss"] = new_sl
                        self.active_trade["be_armed"] = True
                        self.active_trade["breakeven_set"] = True
                        if trade_id:
                            self._update_trade_stop_loss(trade_id, new_sl, be_armed=True)
                        print(f"[{self.symbol} {side} BREAKEVEN ARMED ON TP1] Stop Loss adjusted to {format_price(new_sl)} (Risk neutralized)")

            # Step B: Check Breakeven Arming Trigger (+1.00R excursion)
            # Shields the trade once +1.00R is reached before shifting stop
            if (tp1_hit or tp1 is None) and not be_armed:
                be_threshold_r = 1.00 if tp1 is not None else 1.0
                if mfe_r >= be_threshold_r:
                    be_armed = True
                    new_sl = entry * 1.0005 if side == "LONG" else entry * 0.9995
                    self.active_trade["stop_loss"] = new_sl
                    self.active_trade["be_armed"] = True
                    self.active_trade["breakeven_set"] = True
                    if trade_id:
                        self._update_trade_stop_loss(trade_id, new_sl, be_armed=True)
                    print(f"[{self.symbol} {side} BREAKEVEN ARMED] Stop Loss adjusted to {format_price(new_sl)} (Risk neutralized)")

            # Step C: Check Take Profit 2 (Full Exit)
            take_profit_hit = (candle_high >= tp2 if side == "LONG" else candle_low <= tp2)

            # Step D: Check Stop Loss Hit against stop level at start of candle
            stop_loss_hit = (candle_low <= sl_at_bar_start if side == "LONG" else candle_high >= sl_at_bar_start)

            exit_reason = None
            exit_price = None
            net_return_pct = 0.0
            r_multiple = 0.0

            if stop_loss_hit and take_profit_hit:
                # Conservative rule: if both touched in same candle, prioritize stop loss with slippage protection
                candle_open = candle_data[0]
                exit_price = min(candle_open, sl_at_bar_start) if side == "LONG" else max(candle_open, sl_at_bar_start)
                exit_reason = "STOP_LOSS"
                net_return_pct, r_multiple = self._calculate_trade_returns(
                    entry=entry,
                    exit_price=exit_price,
                    side=side,
                    risk_dist=risk_dist,
                    tp1_hit=tp1_hit,
                    tp1_price=tp1,
                    bars_held=bars_held,
                )

            elif stop_loss_hit:
                candle_open = candle_data[0]
                exit_price = min(candle_open, sl) if side == "LONG" else max(candle_open, sl)
                if be_armed:
                    exit_reason = "BREAKEVEN"
                elif tp1_hit:
                    exit_reason = "PARTIAL_LOSS"
                else:
                    exit_reason = "STOP_LOSS"

                net_return_pct, r_multiple = self._calculate_trade_returns(
                    entry=entry,
                    exit_price=exit_price,
                    side=side,
                    risk_dist=risk_dist,
                    tp1_hit=tp1_hit,
                    tp1_price=tp1,
                    bars_held=bars_held,
                )

            elif take_profit_hit:
                exit_price = tp2
                exit_reason = "TAKE_PROFIT"
                net_return_pct, r_multiple = self._calculate_trade_returns(
                    entry=entry,
                    exit_price=exit_price,
                    side=side,
                    risk_dist=risk_dist,
                    tp1_hit=tp1_hit,
                    tp1_price=tp1,
                    bars_held=bars_held,
                )

            elif not tp1_hit and bars_held >= self.max_stagnant_bars and close_r < -0.15:
                # Step E: Kinetic Stagnancy Exit Lifecycle Management
                # Automatically liquidates stagnant trades held >= max_stagnant_bars (default 12 bars = 3h on 15m)
                # that remain underwater (close_r < -0.15R), cutting losses before they drag to -0.51R or full SL.
                exit_price = candle_close
                exit_reason = "STAGNANCY_SCRATCH" if close_r >= -0.25 else "TIME_STOP_UNDERWATER"
                net_return_pct, r_multiple = self._calculate_trade_returns(
                    entry=entry,
                    exit_price=exit_price,
                    side=side,
                    risk_dist=risk_dist,
                    tp1_hit=False,
                    tp1_price=None,
                    bars_held=bars_held,
                )

            if exit_reason is not None and exit_price is not None:
                self._record_closed_trade(
                    exit_timestamp=timestamp,
                    exit_price=exit_price,
                    reason=exit_reason,
                    net_return_pct=net_return_pct,
                    r_multiple=r_multiple,
                )
                if self.risk_manager is not None:
                    self.risk_manager.record_position_closed(self.symbol, self.bar_count)
                self.active_trade = None
                trade_closed_this_candle = True
                self.trade_closed_this_bar = True

        # Add candle to rolling history
        self.candles.append(candle_data)

        # Update RSI history
        all_closes = [c[3] for c in self.candles]
        if len(all_closes) >= 15:
            current_rsi = calculate_rsi(all_closes, period=14)
            self.rsi_history.append(current_rsi)

        # 2. Check entry conditions if at least 200 candles and no open trade (and did not exit on this same bar)
        if len(self.candles) >= 200 and self.active_trade is None and not trade_closed_this_candle and not closed_during_this_interval:
            all_highs = [c[1] for c in self.candles]
            all_lows = [c[2] for c in self.candles]
            all_volumes = [c[4] for c in self.candles]

            ema_20 = calculate_ema(all_closes, 20)
            ema_50 = calculate_ema(all_closes, 50)
            ema_200 = calculate_ema(all_closes, 200)

            # Sequential impulse volume and notional turnover
            all_turnovers = [float(v) * float(c) for v, c in zip(all_volumes, all_closes)]
            prior_volumes = all_volumes[-21:-1] if len(all_volumes) >= 21 else all_volumes[:-1]
            prior_turnovers = all_turnovers[-21:-1] if len(all_turnovers) >= 21 else all_turnovers[:-1]
            avg_vol_20 = float(np.mean(prior_volumes)) if len(prior_volumes) > 0 else 1.0
            avg_turnover_20 = float(np.mean(prior_turnovers)) if len(prior_turnovers) > 0 else 1.0
            vol_expansion = (
                (avg_vol_20 > 0.0 and any(v > 0.0 and v >= (1.5 * avg_vol_20) for v in all_volumes[-6:]))
                or (avg_turnover_20 > 0.0 and any(t > 0.0 and t >= (1.5 * avg_turnover_20) for t in all_turnovers[-6:]))
            )

            # RSI momentum and fractal divergence triggers
            has_rsi_history = len(self.rsi_history) >= 2
            prev_rsi = self.rsi_history[-2] if has_rsi_history else 50.0
            curr_rsi = self.rsi_history[-1] if has_rsi_history else 50.0
            bull_div, bear_div = detect_rsi_divergences(list(self.candles), period=14, lookback=30)

            # ----------------------------------------------------
            # Strategy Engine v2.0: Confluence Scoring Matrix (0-100)
            # ----------------------------------------------------
            adx_14 = calculate_adx(all_highs, all_lows, all_closes, period=14)
            atr_14 = calculate_atr(all_highs, all_lows, all_closes, period=14)

            # ADX Hysteresis Buffer: Dual-threshold Schmitt trigger to eliminate cliff-edge flickering
            is_major = self.symbol in ("BTCUSDT", "ETHUSDT")
            adx_high = 26.0 if is_major else 23.0
            adx_low = 24.0 if is_major else 21.0

            if getattr(self, "in_chop_regime", False):
                if adx_14 >= adx_high:
                    self.in_chop_regime = False
            else:
                if adx_14 < adx_low:
                    self.in_chop_regime = True

            is_chop_regime = self.in_chop_regime
            min_adx = adx_high if self.in_chop_regime else adx_low

            # -----------------
            # 1. LONG SCORING
            # -----------------
            long_trend = (ema_50 > ema_200) and (candle_close > ema_50)
            long_retest, long_retest_info = check_bullish_fvg_retest(list(self.candles), lookback=15)
            if not long_retest and self.pending_retest is not None:
                if self.pending_retest.get("side") == "LONG" and self.bar_count <= self.pending_retest.get("bar", 0) + 1:
                    fvg_lower = self.pending_retest["info"]["lower"]
                    if candle_close < fvg_lower:
                        self.pending_retest = None
                    elif check_fvg_rejection_confirmation(self.candles[-1], side="LONG"):
                        long_retest = True
                        long_retest_info = self.pending_retest["info"]
                        self.pending_retest = None
                else:
                    self.pending_retest = None
            long_fresh_fvg = check_recent_bullish_fvg(list(self.candles), lookback=5)

            long_score = 0.0
            long_blocked = False
            long_block_reason = ""

            effective_htf = self.get_htf_trend()
            effective_1h = self.get_1h_trend()
            long_1h_allowed = (effective_1h == "BULLISH") or (
                effective_1h == "NEUTRAL" and (not self.htf_trend_filter or effective_htf == "BULLISH")
            )

            is_overextended, ext_reason = check_market_overextension(
                candle_close, ema_50, atr_14, curr_rsi, side="LONG", ema20=ema_20
            )
            candle_range = candle_high - candle_low
            upper_wick = candle_high - max(candle_open, candle_close)
            curr_turnover = all_turnovers[-1]
            is_vol_climax_seller = (
                ((avg_vol_20 > 0.0 and all_volumes[-1] > (3.5 * avg_vol_20))
                 or (avg_turnover_20 > 0.0 and curr_turnover > (3.5 * avg_turnover_20)))
                and candle_range > 0.0
                and ((upper_wick / candle_range) > 0.40)
            )

            if not long_trend:
                long_blocked = True
                long_block_reason = "Macro Trend not aligned"
            elif self.htf_trend_filter and effective_htf == "BEARISH":
                long_blocked = True
                long_block_reason = "HTF (4h) Trend is BEARISH (Opposing macro trend)"
            elif self.intermediate_1h_filter and not long_1h_allowed:
                long_blocked = True
                long_block_reason = f"1H Intermediate Trend is {effective_1h} (Opposing intermediate trend)"
            elif is_chop_regime:
                long_blocked = True
                long_block_reason = f"Chop Regime Detected (ADX {adx_14:.1f} < {min_adx:.1f})"
            elif is_overextended:
                long_blocked = True
                long_block_reason = f"Market Overextended ({ext_reason})"
            elif is_vol_climax_seller:
                long_blocked = True
                long_block_reason = f"Volume Climax with Heavy Selling Wick ({upper_wick / candle_range:.1%} upper wick)"
            elif self.confirm_fvg_rejection and long_retest and not check_fvg_rejection_confirmation(self.candles[-1], side="LONG"):
                long_blocked = True
                long_block_reason = "FVG Rejection Unconfirmed (No absorption wick or positive close)"
                if long_retest_info:
                    self.pending_retest = {"side": "LONG", "info": long_retest_info, "bar": self.bar_count}
            elif self.require_market_structure and not check_market_structure_alignment(list(self.candles), side="LONG", max_bars_since_event=16)[0]:
                long_blocked = True
                _, long_block_reason, _ = check_market_structure_alignment(list(self.candles), side="LONG", max_bars_since_event=16)
            elif self.require_fibonacci_ote and not check_fibonacci_ote_confluence(candle_close, list(self.candles), side="LONG")[0]:
                long_blocked = True
                _, long_block_reason, _ = check_fibonacci_ote_confluence(candle_close, list(self.candles), side="LONG")
            elif avg_vol_20 <= 0.0 or all_volumes[-1] <= 0.0:
                long_blocked = True
                long_block_reason = "Zero volume detected"
            else:
                # 0. Market Structure Alignment (BOS: +15, CHOCH: +20)
                if self.require_market_structure:
                    _, _, ms_info = check_market_structure_alignment(list(self.candles), side="LONG", max_bars_since_event=16)
                    long_score += 20.0 if ms_info.get("last_event") == "CHOCH" else 15.0

                # 0.1 Fibonacci OTE & Discount/Premium Pricing Matrix (+15 for OTE, +10 for Discount)
                if self.require_fibonacci_ote:
                    _, _, fib_info = check_fibonacci_ote_confluence(candle_close, list(self.candles), side="LONG")
                    if fib_info.get("is_ote"):
                        long_score += 15.0
                    elif fib_info.get("zone") in ("SHALLOW_DISCOUNT", "DEEP_DISCOUNT"):
                        long_score += 10.0

                # 0.2 Pillar 3: Institutional Order Block / Breaker Block Confluence (+15 pts with touch decay)
                if self.enable_order_blocks:
                    has_ob_conf, ob_msg, ob_info = check_order_block_confluence(candle_close, list(self.candles), side="LONG")
                    if has_ob_conf:
                        long_score += ob_info.get("freshness_score", 15.0)

                # 1. Macro Trend, 2. Trend Strength (ADX), 5. Volume Expansion
                if self.enable_collinearity_cap:
                    trend_adx_vol_pts = 20.0 + (15.0 if adx_14 >= 25.0 else 10.0) + (10.0 if vol_expansion else 0.0)
                    long_score += min(trend_adx_vol_pts, 35.0)
                else:
                    long_score += 20.0
                    long_score += 15.0 if adx_14 >= 25.0 else 10.0
                    if vol_expansion:
                        long_score += 10.0

                # 3. SMC FVG Retest Mitigation: 25 pts, or Fresh FVG: 15 pts (if not require_fvg_retest)
                if long_retest:
                    long_score += 25.0
                elif long_fresh_fvg and not self.require_fvg_retest:
                    long_score += 15.0

                # 4. Momentum: Fractal Bull Divergence: 20 pts, or RSI Recovery Cross: 10 pts
                has_rsi_cross = has_rsi_history and (prev_rsi < 40.0) and (curr_rsi >= 40.0)
                if bull_div:
                    long_score += 20.0
                elif has_rsi_cross:
                    long_score += 10.0

                # 6. Microstructure & Order Flow Alignment: 10 pts
                if self.micro_mgr is not None:
                    if hasattr(self.micro_mgr, "oi_engine"):
                        oi_regime = self.micro_mgr.oi_engine.get_regime(self.symbol)
                        if oi_regime == "SHORT_BUILDUP":
                            # De-confliction: If actively retesting an FVG with confirmed absorption wick,
                            # do not block: this is passive institutional limit buying absorbing sell flow!
                            if long_retest and check_fvg_rejection_confirmation(self.candles[-1], side="LONG"):
                                long_score += 2.5  # Institutional limit absorption credited
                            else:
                                long_blocked = True
                                long_block_reason = f"OI Regime is {oi_regime}"
                        elif oi_regime in ("LONG_BUILDUP", "SHORT_SQUEEZE"):
                            long_score += 5.0
                        else:
                            long_score += 5.0
                    else:
                        long_score += 5.0

                    if hasattr(self.micro_mgr, "liq_tracker"):
                        try:
                            _, is_long_cascade = self.micro_mgr.liq_tracker.get_rolling_summary(self.symbol, side="LONG")
                            if is_long_cascade:
                                long_blocked = True
                                long_block_reason = "Long Liquidation Cascade Active (Falling Knife)"
                        except TypeError:
                            _, is_cascade = self.micro_mgr.liq_tracker.get_rolling_summary(self.symbol)
                            if is_cascade:
                                long_blocked = True
                                long_block_reason = "Liquidation Cascade Active"

                    if hasattr(self.micro_mgr, "whale_filter"):
                        if hasattr(self.micro_mgr.whale_filter, "get_time_decayed_net_delta"):
                            whale_threshold = self.get_whale_threshold(self.symbol)
                            tape_empty = not getattr(self.micro_mgr.whale_filter, "tape", None)
                            if tape_empty:
                                long_score += 5.0
                            else:
                                net_whale_delta = self.micro_mgr.whale_filter.get_time_decayed_net_delta(self.symbol)
                                if net_whale_delta < -whale_threshold:
                                    long_blocked = True
                                    long_block_reason = f"Whale Net Selling Delta (${net_whale_delta:,.2f}) < -${whale_threshold:,.0f}"
                                elif net_whale_delta > 0:
                                    long_score += 5.0
                                else:
                                    long_score += 2.5
                        elif hasattr(self.micro_mgr.whale_filter, "tape"):
                            sym_trades = [t for t in self.micro_mgr.whale_filter.tape if t.symbol in (self.symbol, getattr(self.micro_mgr, "futures_to_spot", {}).get(t.symbol, t.symbol))]
                            if sym_trades:
                                net_whale_delta = sum(t.notional if t.side == "BUY" else -t.notional for t in sym_trades)
                                whale_threshold = self.get_whale_threshold(self.symbol)
                                if net_whale_delta < -whale_threshold:
                                    long_blocked = True
                                    long_block_reason = f"Whale Net Selling Delta (${net_whale_delta:,.2f}) < -${whale_threshold:,.0f}"
                                elif net_whale_delta > 0:
                                    long_score += 5.0
                                else:
                                    long_score += 2.5
                            else:
                                long_score += 5.0
                        else:
                            long_score += 5.0
                    else:
                        long_score += 5.0
                else:
                    # Offline / Backtest baseline: 0.0 pts (microstructure telemetry offline)
                    pass

            # Mandatory FVG requirement: setup cannot pass threshold without an FVG
            if not long_retest and not (long_fresh_fvg and not self.require_fvg_retest):
                long_score = min(long_score, 65.0)

            if not long_blocked and long_score >= 70.0:
                if self.risk_manager is not None:
                    allowed, reason = self.risk_manager.can_open_position(self.symbol, self.bar_count, side="LONG")
                    if not allowed:
                        print(f"[{self.symbol} LONG REJECTED BY RISK MANAGER] {reason}")
                        return None

                atr_14 = calculate_atr(all_highs, all_lows, all_closes, period=14)
                entry_price = candle_close * 1.0002
                min_sl_dist = self.get_min_sl_dist(entry_price)
                if long_retest and long_retest_info:
                    fvg_invalidation_dist = (entry_price - long_retest_info["lower"]) + (0.35 * atr_14)
                    sl_distance = max(1.8 * atr_14, fvg_invalidation_dist, min_sl_dist)
                else:
                    sl_distance = max(1.8 * atr_14, min_sl_dist)
                stop_loss = max(entry_price - sl_distance, entry_price * 0.01)  # Sub-dollar protection
                tp1_price = entry_price + (sl_distance * self.tp1_ratio)  # 50% scale-out at tp1_ratio (+0.80R)
                take_profit = entry_price + (sl_distance * 1.75)  # Empirically proven 1.75R final target

                if self.order_book_engine is not None:
                    allowed, ob_reason = self.check_order_book_liquidity_gate(
                        side="LONG",
                        entry_price=entry_price,
                        atr=atr_14,
                        tp1_price=tp1_price,
                    )
                    if not allowed:
                        print(f"[{self.symbol} LONG VETOED BY ORDER BOOK SHIELD] {ob_reason}")
                        return None

                # Calculate position sizing and leverage bounds
                sizing = {}
                if self.risk_manager is not None and hasattr(self.risk_manager, "calculate_position_size"):
                    sizing = self.risk_manager.calculate_position_size(entry_price, stop_loss, self.symbol)
                qty = sizing.get("quantity", 0.0)
                notional_usd = sizing.get("notional_usd", 0.0)
                lev = sizing.get("leverage", 0.0)

                # Persist OPEN trade immediately into SQLite
                with self._get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        """
                        INSERT INTO paper_trades (
                            symbol, timeframe, side, status, timestamp, entry_price, stop_loss, initial_stop_loss, take_profit,
                            tp1_price, tp1_hit, be_armed, entry_bar, quantity, notional_usd, leverage
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?, ?)
                        """,
                        (
                            self.symbol,
                            self.timeframe,
                            "LONG",
                            "OPEN",
                            timestamp,
                            entry_price,
                            stop_loss,
                            stop_loss,
                            take_profit,
                            tp1_price,
                            self.bar_count,
                            qty,
                            notional_usd,
                            lev,
                        ),
                    )
                    trade_id = cursor.lastrowid
                    conn.commit()

                if trade_id:
                    self._register_synthetic_oco_bracket(
                        trade_id=trade_id,
                        symbol=self.symbol,
                        side="LONG",
                        sl=stop_loss,
                        tp2=take_profit,
                        tp1=tp1_price,
                        qty=qty,
                        timestamp=timestamp,
                    )

                if self.risk_manager is not None and hasattr(self.risk_manager, "record_position_opened"):
                    self.risk_manager.record_position_opened(self.symbol, self.bar_count)

                self.active_trade = {
                    "id": trade_id,
                    "symbol": self.symbol,
                    "timeframe": self.timeframe,
                    "side": "LONG",
                    "status": "OPEN",
                    "timestamp": timestamp,
                    "entry_price": entry_price,
                    "stop_loss": stop_loss,
                    "initial_stop_loss": stop_loss,
                    "take_profit": take_profit,
                    "tp1_price": tp1_price,
                    "tp1_hit": False,
                    "be_armed": False,
                    "breakeven_set": False,
                    "entry_bar": self.bar_count,
                    "risk_dist": sl_distance,
                    "confluence_score": long_score,
                    "quantity": qty,
                    "notional_usd": notional_usd,
                    "leverage": lev,
                }

                print(
                    f"[{self.symbol} LONG OPENED (Confluence: {long_score:.0f}/100)] Entry: {format_price(entry_price)} | "
                    f"Stop Loss: {format_price(stop_loss)} | Take Profit: {format_price(take_profit)} | Size: {qty:.4f} (${notional_usd:,.2f}, {lev:.2f}x lev)"
                )
                return self.active_trade
            elif long_blocked and long_block_reason and ("Chop" in long_block_reason or "OI Regime" in long_block_reason or "Whale" in long_block_reason or "Liquidation" in long_block_reason or "HTF" in long_block_reason or "1H" in long_block_reason or "Overextended" in long_block_reason or "Volume Climax" in long_block_reason or "FVG Rejection" in long_block_reason or "Market structure" in long_block_reason or "Structural momentum" in long_block_reason or "Trapped in horizontal" in long_block_reason or "PREMIUM" in long_block_reason or "broken below Swing" in long_block_reason or "Dealing range" in long_block_reason):
                if "PREMIUM" in long_block_reason or "broken below Swing" in long_block_reason or "Dealing range" in long_block_reason:
                    print(f"[{self.symbol} LONG BLOCKED BY FIBONACCI OTE] {long_block_reason}")
                elif "Market structure" in long_block_reason or "Structural momentum" in long_block_reason or "Trapped in horizontal" in long_block_reason:
                    print(f"[{self.symbol} LONG BLOCKED BY MARKET STRUCTURE] {long_block_reason}")
                elif "Chop" in long_block_reason:
                    print(f"[{self.symbol} LONG BLOCKED BY CHOP FILTER] ADX {adx_14:.1f} < {min_adx:.1f}")
                elif "Overextended" in long_block_reason:
                    print(f"[{self.symbol} LONG BLOCKED BY OVEREXTENSION] {long_block_reason}")
                elif "Volume Climax" in long_block_reason or "FVG Rejection" in long_block_reason:
                    print(f"[{self.symbol} LONG BLOCKED BY PRICE ACTION] {long_block_reason}")
                elif "HTF" in long_block_reason:
                    print(f"[{self.symbol} LONG BLOCKED BY HTF TREND FILTER] {long_block_reason}")
                elif "1H" in long_block_reason:
                    print(f"[{self.symbol} LONG BLOCKED BY 1H STRUCTURE GATE] {long_block_reason}")
                elif "OI Regime" in long_block_reason or "Whale" in long_block_reason or "Liquidation" in long_block_reason:
                    print(f"[{self.symbol} LONG BLOCKED BY MICROSTRUCTURE] {long_block_reason}")

            # ------------------
            # 2. SHORT SCORING
            # ------------------
            short_trend = (ema_50 < ema_200) and (candle_close < ema_50)
            short_retest, short_retest_info = check_bearish_fvg_retest(list(self.candles), lookback=15)
            if not short_retest and self.pending_retest is not None:
                if self.pending_retest.get("side") == "SHORT" and self.bar_count <= self.pending_retest.get("bar", 0) + 1:
                    fvg_upper = self.pending_retest["info"]["upper"]
                    if candle_close > fvg_upper:
                        self.pending_retest = None
                    elif check_fvg_rejection_confirmation(self.candles[-1], side="SHORT"):
                        short_retest = True
                        short_retest_info = self.pending_retest["info"]
                        self.pending_retest = None
                else:
                    self.pending_retest = None
            short_fresh_fvg = check_recent_bearish_fvg(list(self.candles), lookback=5)

            short_score = 0.0
            short_blocked = False
            short_block_reason = ""

            short_1h_allowed = (effective_1h == "BEARISH") or (
                effective_1h == "NEUTRAL" and (not self.htf_trend_filter or effective_htf == "BEARISH")
            )

            is_overextended_short, ext_reason_short = check_market_overextension(
                candle_close, ema_50, atr_14, curr_rsi, side="SHORT", ema20=ema_20
            )
            lower_wick = min(candle_open, candle_close) - candle_low
            is_vol_climax_buyer = (
                ((avg_vol_20 > 0.0 and all_volumes[-1] > (3.5 * avg_vol_20))
                 or (avg_turnover_20 > 0.0 and curr_turnover > (3.5 * avg_turnover_20)))
                and candle_range > 0.0
                and ((lower_wick / candle_range) > 0.40)
            )

            if not short_trend:
                short_blocked = True
                short_block_reason = "Macro Trend not aligned"
            elif self.htf_trend_filter and effective_htf == "BULLISH":
                short_blocked = True
                short_block_reason = "HTF (4h) Trend is BULLISH (Opposing macro trend)"
            elif self.intermediate_1h_filter and not short_1h_allowed:
                short_blocked = True
                short_block_reason = f"1H Intermediate Trend is {effective_1h} (Opposing intermediate trend)"
            elif is_chop_regime:
                short_blocked = True
                short_block_reason = f"Chop Regime Detected (ADX {adx_14:.1f} < {min_adx:.1f})"
            elif is_overextended_short:
                short_blocked = True
                short_block_reason = f"Market Overextended ({ext_reason_short})"
            elif is_vol_climax_buyer:
                short_blocked = True
                short_block_reason = f"Volume Climax with Heavy Buying Wick ({lower_wick / candle_range:.1%} lower wick)"
            elif self.confirm_fvg_rejection and short_retest and not check_fvg_rejection_confirmation(self.candles[-1], side="SHORT"):
                short_blocked = True
                short_block_reason = "FVG Rejection Unconfirmed (No defense wick or negative close)"
                if short_retest_info:
                    self.pending_retest = {"side": "SHORT", "info": short_retest_info, "bar": self.bar_count}
            elif self.require_market_structure and not check_market_structure_alignment(list(self.candles), side="SHORT", max_bars_since_event=16)[0]:
                short_blocked = True
                _, short_block_reason, _ = check_market_structure_alignment(list(self.candles), side="SHORT", max_bars_since_event=16)
            elif self.require_fibonacci_ote and not check_fibonacci_ote_confluence(candle_close, list(self.candles), side="SHORT")[0]:
                short_blocked = True
                _, short_block_reason, _ = check_fibonacci_ote_confluence(candle_close, list(self.candles), side="SHORT")
            elif avg_vol_20 <= 0.0 or all_volumes[-1] <= 0.0:
                short_blocked = True
                short_block_reason = "Zero volume detected"
            else:
                # 0. Market Structure Alignment (BOS: +15, CHOCH: +20)
                if self.require_market_structure:
                    _, _, ms_info = check_market_structure_alignment(list(self.candles), side="SHORT", max_bars_since_event=16)
                    short_score += 20.0 if ms_info.get("last_event") == "CHOCH" else 15.0

                # 0.1 Fibonacci OTE & Discount/Premium Pricing Matrix (+15 for OTE, +10 for Premium)
                if self.require_fibonacci_ote:
                    _, _, fib_info = check_fibonacci_ote_confluence(candle_close, list(self.candles), side="SHORT")
                    if fib_info.get("is_ote"):
                        short_score += 15.0
                    elif fib_info.get("zone") in ("SHALLOW_PREMIUM", "DEEP_PREMIUM"):
                        short_score += 10.0

                # 0.2 Pillar 3: Institutional Order Block / Breaker Block Confluence (+15 pts with touch decay)
                if self.enable_order_blocks:
                    has_ob_conf, ob_msg, ob_info = check_order_block_confluence(candle_close, list(self.candles), side="SHORT")
                    if has_ob_conf:
                        short_score += ob_info.get("freshness_score", 15.0)

                # 1. Macro Trend, 2. Trend Strength (ADX), 5. Volume Expansion
                if self.enable_collinearity_cap:
                    trend_adx_vol_pts = 20.0 + (15.0 if adx_14 >= 25.0 else 10.0) + (10.0 if vol_expansion else 0.0)
                    short_score += min(trend_adx_vol_pts, 35.0)
                else:
                    short_score += 20.0
                    short_score += 15.0 if adx_14 >= 25.0 else 10.0
                    if vol_expansion:
                        short_score += 10.0

                # 3. SMC FVG Retest Mitigation: 25 pts, or Fresh FVG: 15 pts (if not require_fvg_retest)
                if short_retest:
                    short_score += 25.0
                elif short_fresh_fvg and not self.require_fvg_retest:
                    short_score += 15.0

                # 4. Momentum: Fractal Bear Divergence: 20 pts, or RSI Recovery Cross: 10 pts
                has_rsi_cross = has_rsi_history and (prev_rsi > 60.0) and (curr_rsi <= 60.0)
                if bear_div:
                    short_score += 20.0
                elif has_rsi_cross:
                    short_score += 10.0

                # 6. Microstructure & Order Flow Alignment: 10 pts
                if self.micro_mgr is not None:
                    if hasattr(self.micro_mgr, "oi_engine"):
                        oi_regime = self.micro_mgr.oi_engine.get_regime(self.symbol)
                        if oi_regime == "LONG_BUILDUP":
                            # De-confliction: If actively retesting an FVG with confirmed defense wick,
                            # do not block: this is passive institutional limit selling defending resistance!
                            if short_retest and check_fvg_rejection_confirmation(self.candles[-1], side="SHORT"):
                                short_score += 2.5  # Institutional limit defense credited
                            else:
                                short_blocked = True
                                short_block_reason = f"OI Regime is {oi_regime}"
                        elif oi_regime in ("SHORT_BUILDUP", "LONG_LIQUIDATION"):
                            short_score += 5.0
                        else:
                            short_score += 5.0
                    else:
                        short_score += 5.0

                    if hasattr(self.micro_mgr, "liq_tracker"):
                        try:
                            _, is_short_cascade = self.micro_mgr.liq_tracker.get_rolling_summary(self.symbol, side="SHORT")
                            if is_short_cascade:
                                short_blocked = True
                                short_block_reason = "Short Liquidation Cascade Active (Squeeze)"
                        except TypeError:
                            _, is_cascade = self.micro_mgr.liq_tracker.get_rolling_summary(self.symbol)
                            if is_cascade:
                                short_blocked = True
                                short_block_reason = "Liquidation Cascade Active"

                    if hasattr(self.micro_mgr, "whale_filter"):
                        if hasattr(self.micro_mgr.whale_filter, "get_time_decayed_net_delta"):
                            whale_threshold = self.get_whale_threshold(self.symbol)
                            tape_empty = not getattr(self.micro_mgr.whale_filter, "tape", None)
                            if tape_empty:
                                short_score += 5.0
                            else:
                                net_whale_delta = self.micro_mgr.whale_filter.get_time_decayed_net_delta(self.symbol)
                                if net_whale_delta > whale_threshold:
                                    short_blocked = True
                                    short_block_reason = f"Whale Net Buying Delta (${net_whale_delta:,.2f}) > +${whale_threshold:,.0f}"
                                elif net_whale_delta < 0:
                                    short_score += 5.0
                                else:
                                    short_score += 2.5
                        elif hasattr(self.micro_mgr.whale_filter, "tape"):
                            sym_trades = [t for t in self.micro_mgr.whale_filter.tape if t.symbol in (self.symbol, getattr(self.micro_mgr, "futures_to_spot", {}).get(t.symbol, t.symbol))]
                            if sym_trades:
                                net_whale_delta = sum(t.notional if t.side == "BUY" else -t.notional for t in sym_trades)
                                whale_threshold = self.get_whale_threshold(self.symbol)
                                if net_whale_delta > whale_threshold:
                                    short_blocked = True
                                    short_block_reason = f"Whale Net Buying Delta (${net_whale_delta:,.2f}) > +${whale_threshold:,.0f}"
                                elif net_whale_delta < 0:
                                    short_score += 5.0
                                else:
                                    short_score += 2.5
                            else:
                                short_score += 5.0
                        else:
                            short_score += 5.0
                    else:
                        short_score += 5.0
                else:
                    # Offline / Backtest baseline: 0.0 pts (microstructure telemetry offline)
                    pass

            # Mandatory FVG requirement: setup cannot pass threshold without an FVG
            if not short_retest and not (short_fresh_fvg and not self.require_fvg_retest):
                short_score = min(short_score, 65.0)

            if not short_blocked and short_score >= 70.0:
                if self.risk_manager is not None:
                    allowed, reason = self.risk_manager.can_open_position(self.symbol, self.bar_count, side="SHORT")
                    if not allowed:
                        print(f"[{self.symbol} SHORT REJECTED BY RISK MANAGER] {reason}")
                        return None

                atr_14 = calculate_atr(all_highs, all_lows, all_closes, period=14)
                entry_price = candle_close * 0.9998
                min_sl_dist = self.get_min_sl_dist(entry_price)
                if short_retest and short_retest_info:
                    fvg_invalidation_dist = (short_retest_info["upper"] - entry_price) + (0.35 * atr_14)
                    sl_distance = max(1.8 * atr_14, fvg_invalidation_dist, min_sl_dist)
                else:
                    sl_distance = max(1.8 * atr_14, min_sl_dist)
                stop_loss = entry_price + sl_distance
                tp1_price = entry_price - (sl_distance * self.tp1_ratio)  # 50% scale-out at tp1_ratio (+0.80R)
                raw_tp = entry_price - (sl_distance * 1.75)  # Empirically proven 1.75R final target
                take_profit = max(raw_tp, entry_price * 0.01)  # Sub-dollar protection

                if self.order_book_engine is not None:
                    allowed, ob_reason = self.check_order_book_liquidity_gate(
                        side="SHORT",
                        entry_price=entry_price,
                        atr=atr_14,
                        tp1_price=tp1_price,
                    )
                    if not allowed:
                        print(f"[{self.symbol} SHORT VETOED BY ORDER BOOK SHIELD] {ob_reason}")
                        return None

                # Calculate position sizing and leverage bounds
                sizing = {}
                if self.risk_manager is not None and hasattr(self.risk_manager, "calculate_position_size"):
                    sizing = self.risk_manager.calculate_position_size(entry_price, stop_loss, self.symbol)
                qty = sizing.get("quantity", 0.0)
                notional_usd = sizing.get("notional_usd", 0.0)
                lev = sizing.get("leverage", 0.0)

                # Persist OPEN trade immediately into SQLite
                with self._get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        """
                        INSERT INTO paper_trades (
                            symbol, timeframe, side, status, timestamp, entry_price, stop_loss, initial_stop_loss, take_profit,
                            tp1_price, tp1_hit, be_armed, entry_bar, quantity, notional_usd, leverage
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?, ?)
                        """,
                        (
                            self.symbol,
                            self.timeframe,
                            "SHORT",
                            "OPEN",
                            timestamp,
                            entry_price,
                            stop_loss,
                            stop_loss,
                            take_profit,
                            tp1_price,
                            self.bar_count,
                            qty,
                            notional_usd,
                            lev,
                        ),
                    )
                    trade_id = cursor.lastrowid
                    conn.commit()

                if trade_id:
                    self._register_synthetic_oco_bracket(
                        trade_id=trade_id,
                        symbol=self.symbol,
                        side="SHORT",
                        sl=stop_loss,
                        tp2=take_profit,
                        tp1=tp1_price,
                        qty=qty,
                        timestamp=timestamp,
                    )

                if self.risk_manager is not None and hasattr(self.risk_manager, "record_position_opened"):
                    self.risk_manager.record_position_opened(self.symbol, self.bar_count)

                self.active_trade = {
                    "id": trade_id,
                    "symbol": self.symbol,
                    "timeframe": self.timeframe,
                    "side": "SHORT",
                    "status": "OPEN",
                    "timestamp": timestamp,
                    "entry_price": entry_price,
                    "stop_loss": stop_loss,
                    "initial_stop_loss": stop_loss,
                    "take_profit": take_profit,
                    "tp1_price": tp1_price,
                    "tp1_hit": False,
                    "be_armed": False,
                    "breakeven_set": False,
                    "entry_bar": self.bar_count,
                    "risk_dist": sl_distance,
                    "confluence_score": short_score,
                    "quantity": qty,
                    "notional_usd": notional_usd,
                    "leverage": lev,
                }

                print(
                    f"[{self.symbol} SHORT OPENED (Confluence: {short_score:.0f}/100)] Entry: {format_price(entry_price)} | "
                    f"Stop Loss: {format_price(stop_loss)} | Take Profit: {format_price(take_profit)} | Size: {qty:.4f} (${notional_usd:,.2f}, {lev:.2f}x lev)"
                )
                return self.active_trade
            elif short_blocked and short_block_reason and ("Chop" in short_block_reason or "OI Regime" in short_block_reason or "Whale" in short_block_reason or "Liquidation" in short_block_reason or "HTF" in short_block_reason or "1H" in short_block_reason or "Overextended" in short_block_reason or "Volume Climax" in short_block_reason or "FVG Rejection" in short_block_reason or "Market structure" in short_block_reason or "Structural momentum" in short_block_reason or "Trapped in horizontal" in short_block_reason or "DISCOUNT" in short_block_reason or "broken above Swing" in short_block_reason or "Dealing range" in short_block_reason):
                if "DISCOUNT" in short_block_reason or "broken above Swing" in short_block_reason or "Dealing range" in short_block_reason:
                    print(f"[{self.symbol} SHORT BLOCKED BY FIBONACCI OTE] {short_block_reason}")
                elif "Market structure" in short_block_reason or "Structural momentum" in short_block_reason or "Trapped in horizontal" in short_block_reason:
                    print(f"[{self.symbol} SHORT BLOCKED BY MARKET STRUCTURE] {short_block_reason}")
                elif "Chop" in short_block_reason:
                    print(f"[{self.symbol} SHORT BLOCKED BY CHOP FILTER] ADX {adx_14:.1f} < {min_adx:.1f}")
                elif "Overextended" in short_block_reason:
                    print(f"[{self.symbol} SHORT BLOCKED BY OVEREXTENSION] {short_block_reason}")
                elif "Volume Climax" in short_block_reason or "FVG Rejection" in short_block_reason:
                    print(f"[{self.symbol} SHORT BLOCKED BY PRICE ACTION] {short_block_reason}")
                elif "HTF" in short_block_reason:
                    print(f"[{self.symbol} SHORT BLOCKED BY HTF TREND FILTER] {short_block_reason}")
                elif "1H" in short_block_reason:
                    print(f"[{self.symbol} SHORT BLOCKED BY 1H STRUCTURE GATE] {short_block_reason}")
                elif "OI Regime" in short_block_reason or "Whale" in short_block_reason or "Liquidation" in short_block_reason:
                    print(f"[{self.symbol} SHORT BLOCKED BY MICROSTRUCTURE] {short_block_reason}")

        return None

    def estimate_confluence_priority(
        self,
        candle_data: Tuple[float, float, float, float, float],
    ) -> float:
        """Estimate confluence priority score (0-100) to rank candidate setups on the same bar.
        
        Returns:
            Confluence score (0.0 to 100.0), or -100.0 if symbol is already in an active trade
            or locked in risk manager cooldown.
        """
        if self.active_trade is not None:
            return -100.0
        if self.risk_manager is not None and self.risk_manager.is_symbol_in_cooldown(self.symbol, self.bar_count):
            return -100.0
        if len(self.candles) < 200:
            return 0.0

        o, h, l, close, vol = candle_data[:5]
        all_highs = [c[1] for c in self.candles] + [h]
        all_lows = [c[2] for c in self.candles] + [l]
        all_closes = [c[3] for c in self.candles] + [close]
        all_volumes = [c[4] for c in self.candles] + [vol]

        ema_50 = calculate_ema(all_closes, 50)
        ema_200 = calculate_ema(all_closes, 200)

        prior_volumes = all_volumes[-21:-1] if len(all_volumes) >= 21 else all_volumes[:-1]
        avg_vol_20 = float(np.mean(prior_volumes)) if len(prior_volumes) > 0 else 1.0
        vol_expansion = (
            avg_vol_20 > 0.0
            and any(v > 0.0 and v >= (1.5 * avg_vol_20) for v in all_volumes[-6:])
        )

        candles_sim = list(self.candles) + [(o, h, l, close, vol)]
        bull_div, bear_div = detect_rsi_divergences(candles_sim, period=14, lookback=30)
        adx_14 = calculate_adx(all_highs, all_lows, all_closes, period=14)
        is_major = self.symbol in ("BTCUSDT", "ETHUSDT")
        adx_high = 26.0 if is_major else 23.0
        adx_low = 24.0 if is_major else 21.0
        in_chop = getattr(self, "in_chop_regime", False)
        if (in_chop and adx_14 < adx_high) or (not in_chop and adx_14 < adx_low) or avg_vol_20 <= 0.0:
            return 0.0

        curr_rsi = calculate_rsi(all_closes, period=14) if len(all_closes) >= 15 else 50.0
        prev_rsi = self.rsi_history[-1] if len(self.rsi_history) >= 1 else 50.0
        has_rsi_cross_long = (prev_rsi < 40.0) and (curr_rsi >= 40.0)
        has_rsi_cross_short = (prev_rsi > 60.0) and (curr_rsi <= 60.0)

        effective_htf = self.get_htf_trend() if self.htf_trend_filter else "NEUTRAL"
        effective_1h = self.get_1h_trend() if self.intermediate_1h_filter else "NEUTRAL"
        long_1h_allowed = (effective_1h == "BULLISH") or (
            effective_1h == "NEUTRAL" and (not self.htf_trend_filter or effective_htf == "BULLISH")
        )
        short_1h_allowed = (effective_1h == "BEARISH") or (
            effective_1h == "NEUTRAL" and (not self.htf_trend_filter or effective_htf == "BEARISH")
        )

        # Long Candidate
        long_score = 0.0
        if (ema_50 > ema_200) and (close > ema_50) and not (self.htf_trend_filter and effective_htf == "BEARISH") and not (self.intermediate_1h_filter and not long_1h_allowed):
            long_retest, _ = check_bullish_fvg_retest(candles_sim, lookback=15)
            long_fresh_fvg = check_recent_bullish_fvg(candles_sim, lookback=5)
            if self.enable_collinearity_cap:
                long_score += min(20.0 + (15.0 if adx_14 >= 25.0 else 10.0) + (10.0 if vol_expansion else 0.0), 35.0)
            else:
                long_score += 20.0
                long_score += 15.0 if adx_14 >= 25.0 else 10.0
                if vol_expansion:
                    long_score += 10.0
            if long_retest:
                long_score += 25.0
            elif long_fresh_fvg and not self.require_fvg_retest:
                long_score += 15.0
            if bull_div:
                long_score += 20.0
            elif has_rsi_cross_long:
                long_score += 10.0
            if self.enable_order_blocks:
                has_ob, _, ob_info = check_order_block_confluence(close, candles_sim, side="LONG")
                if has_ob:
                    long_score += ob_info.get("freshness_score", 15.0)

        # Short Candidate
        short_score = 0.0
        if (ema_50 < ema_200) and (close < ema_50) and not (self.htf_trend_filter and effective_htf == "BULLISH") and not (self.intermediate_1h_filter and not short_1h_allowed):
            short_retest, _ = check_bearish_fvg_retest(candles_sim, lookback=15)
            short_fresh_fvg = check_recent_bearish_fvg(candles_sim, lookback=5)
            if self.enable_collinearity_cap:
                short_score += min(20.0 + (15.0 if adx_14 >= 25.0 else 10.0) + (10.0 if vol_expansion else 0.0), 35.0)
            else:
                short_score += 20.0
                short_score += 15.0 if adx_14 >= 25.0 else 10.0
                if vol_expansion:
                    short_score += 10.0
            if short_retest:
                short_score += 25.0
            elif short_fresh_fvg and not self.require_fvg_retest:
                short_score += 15.0
            if bear_div:
                short_score += 20.0
            elif has_rsi_cross_short:
                short_score += 10.0
            if self.enable_order_blocks:
                has_ob, _, ob_info = check_order_block_confluence(close, candles_sim, side="SHORT")
                if has_ob:
                    short_score += ob_info.get("freshness_score", 15.0)

        return max(long_score, short_score)

    def get_active_trade(self) -> Optional[Dict[str, Any]]:
        """Return the current active open trade, if any."""
        return self.active_trade

    def get_performance_summary(self) -> Dict[str, Any]:
        """Calculate cumulative paper trading performance from SQLite database."""
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT net_return_pct, r_multiple, reason
                FROM paper_trades
                WHERE symbol = ? AND status = 'CLOSED'
                """,
                (self.symbol,),
            )
            rows = cursor.fetchall()

        total_trades = len(rows)
        if total_trades == 0:
            return {
                "symbol": self.symbol,
                "total_trades": 0,
                "win_rate_pct": 0.0,
                "total_net_return_pct": 0.0,
                "avg_r_multiple": 0.0,
                "winning_trades": 0,
                "losing_trades": 0,
                "target_wins": 0,
                "breakeven_scratches": 0,
                "target_win_rate_pct": 0.0,
                "breakeven_pct": 0.0,
                "expectancy_r": 0.0,
                "profit_factor": 0.0,
            }

        returns = [float(r["net_return_pct"]) for r in rows if r["net_return_pct"] is not None]
        r_multiples = [float(r["r_multiple"]) for r in rows if r["r_multiple"] is not None]
        wins = sum(1 for ret in returns if ret > 0)
        losses = sum(1 for ret in returns if ret <= 0)
        target_wins = sum(1 for r in r_multiples if r >= 0.8)
        breakeven_scratches = sum(1 for r in r_multiples if 0.0 <= r < 0.8)
        gross_profit = sum(ret for ret in returns if ret > 0)
        gross_loss = sum(abs(ret) for ret in returns if ret < 0)
        profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)

        return {
            "symbol": self.symbol,
            "total_trades": total_trades,
            "win_rate_pct": (wins / total_trades) * 100.0 if total_trades > 0 else 0.0,
            "total_net_return_pct": sum(returns),
            "avg_r_multiple": float(np.mean(r_multiples)) if r_multiples else 0.0,
            "winning_trades": wins,
            "losing_trades": losses,
            "target_wins": target_wins,
            "breakeven_scratches": breakeven_scratches,
            "target_win_rate_pct": (target_wins / total_trades) * 100.0 if total_trades > 0 else 0.0,
            "breakeven_pct": (breakeven_scratches / total_trades) * 100.0 if total_trades > 0 else 0.0,
            "expectancy_r": float(np.mean(r_multiples)) if r_multiples else 0.0,
            "profit_factor": profit_factor,
        }

    # Alias for convenience
    on_new_candle = on_candle

import os
import sqlite3
from contextlib import contextmanager
from typing import Any, Dict, Optional, Tuple


class PortfolioRiskManager:
    """Centralized portfolio risk manager enforcing position limits and symbol cooldowns."""

    def __init__(
        self,
        max_open_positions: int = 3,
        symbol_cooldown_bars: int = 3,
        max_entries_per_bar: int = 1,
        max_directional_positions: int = 2,
        account_equity_usd: float = 10000.0,
        risk_per_trade_pct: float = 1.0,
        max_leverage: float = 3.0,
        allow_preemption: bool = False,
        preemption_min_candidate_score: float = 85.0,
        preemption_min_bars_held: int = 8,
        preemption_max_r_abs: float = 0.15,
        db_path: str = "data/signals_audit.db",
    ):
        self.max_open_positions = max_open_positions
        self.symbol_cooldown_bars = symbol_cooldown_bars
        self.max_entries_per_bar = max_entries_per_bar
        self.max_directional_positions = max_directional_positions
        self.account_equity_usd = float(account_equity_usd)
        self.risk_per_trade_pct = float(risk_per_trade_pct)
        self.max_leverage = float(max_leverage)
        self.allow_preemption = allow_preemption
        self.preemption_min_candidate_score = float(preemption_min_candidate_score)
        self.preemption_min_bars_held = int(preemption_min_bars_held)
        self.preemption_max_r_abs = float(preemption_max_r_abs)
        self.db_path = db_path
        # Tracks the bar count when a trade closed on each symbol: symbol -> closed_at_bar_index
        self._cooldown_trackers: Dict[str, int] = {}
        # Tracks entries opened per bar index: bar_index -> count
        self._entries_this_bar: Dict[int, int] = {}
        self._init_db()

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

    def _init_db(self) -> None:
        db_dir = os.path.dirname(self.db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        with self._get_connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute(
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
                    take_profit REAL NOT NULL,
                    exit_price REAL,
                    exit_timestamp TEXT,
                    reason TEXT,
                    net_return_pct REAL,
                    r_multiple REAL,
                    initial_stop_loss REAL,
                    quantity REAL,
                    notional_usd REAL,
                    leverage REAL,
                    entry_bar INTEGER DEFAULT 0,
                    tp1_price REAL,
                    tp1_hit INTEGER DEFAULT 0,
                    be_armed INTEGER DEFAULT 0
                )
                """
            )
            # Migration check: ensure sizing and tracking columns exist on legacy databases
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(paper_trades)")
            current_cols = [col[1] for col in cursor.fetchall()]
            for col_name, col_type in [
                ("initial_stop_loss", "REAL"),
                ("quantity", "REAL"),
                ("notional_usd", "REAL"),
                ("leverage", "REAL"),
                ("entry_bar", "INTEGER DEFAULT 0"),
                ("tp1_price", "REAL"),
                ("tp1_hit", "INTEGER DEFAULT 0"),
                ("be_armed", "INTEGER DEFAULT 0"),
            ]:
                if current_cols and col_name not in current_cols:
                    cursor.execute(f"ALTER TABLE paper_trades ADD COLUMN {col_name} {col_type}")

            conn.execute("CREATE INDEX IF NOT EXISTS idx_paper_trades_sym_status ON paper_trades(symbol, status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_paper_trades_sym_tf_status ON paper_trades(symbol, timeframe, status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_paper_trades_status ON paper_trades(status)")

    def calculate_position_size(
        self,
        entry_price: float,
        stop_loss: float,
        symbol: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Calculate exact contract quantity, notional value, and leverage.
        
        Enforces a hard max_leverage clamp (default 3.0x account equity) to prevent
        Binance margin rejection errors on hyper-tight stop losses.
        """
        if entry_price <= 0.0:
            return {"quantity": 0.0, "notional_usd": 0.0, "risk_usd": 0.0, "leverage": 0.0, "is_clamped": False}

        sl_dist = abs(entry_price - stop_loss)
        if sl_dist <= 0.0:
            sl_dist = entry_price * 0.01  # Fallback 1% distance

        target_risk_pct = self.risk_per_trade_pct
        if symbol and symbol != "BTCUSDT":
            side_deduced = "LONG" if entry_price > stop_loss else "SHORT"
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT side FROM paper_trades WHERE symbol = 'BTCUSDT' AND status = 'OPEN' LIMIT 1"
                )
                btc_row = cursor.fetchone()
                if btc_row and btc_row[0].upper() == side_deduced:
                    # Halve altcoin risk when correlated BTC is open in same direction
                    target_risk_pct = self.risk_per_trade_pct * 0.5

        target_risk_usd = self.account_equity_usd * (target_risk_pct / 100.0)
        unclamped_qty = target_risk_usd / sl_dist
        unclamped_notional = unclamped_qty * entry_price
        max_notional = self.account_equity_usd * self.max_leverage

        if unclamped_notional > max_notional:
            final_notional = max_notional
            final_qty = final_notional / entry_price
            effective_risk_usd = final_qty * sl_dist
            is_clamped = True
        else:
            final_notional = unclamped_notional
            final_qty = unclamped_qty
            effective_risk_usd = target_risk_usd
            is_clamped = False

        effective_leverage = final_notional / self.account_equity_usd if self.account_equity_usd > 0.0 else 0.0

        return {
            "quantity": final_qty,
            "notional_usd": final_notional,
            "risk_usd": effective_risk_usd,
            "leverage": effective_leverage,
            "is_clamped": is_clamped,
        }

    def has_open_position(self, symbol: str) -> bool:
        """Check if an active OPEN trade already exists for the given symbol."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT 1 FROM paper_trades WHERE symbol = ? AND status = 'OPEN' LIMIT 1",
                (symbol,),
            )
            return cursor.fetchone() is not None

    def get_open_positions_count(self) -> int:
        """Return the number of currently active OPEN trades across all symbols."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM paper_trades WHERE status = 'OPEN'")
            row = cursor.fetchone()
            return int(row[0]) if row else 0

    def is_symbol_in_cooldown(self, symbol: str, current_bar_index: int) -> bool:
        """Check if symbol is currently in its post-trade cooldown period."""
        if symbol not in self._cooldown_trackers:
            return False
        closed_at = self._cooldown_trackers[symbol]
        bars_since = current_bar_index - closed_at
        return bars_since < self.symbol_cooldown_bars

    def can_open_position(
        self, symbol: str, current_bar_index: int, side: Optional[str] = None
    ) -> Tuple[bool, str]:
        """Determine if a new position can be opened for symbol under portfolio risk rules.
        
        Returns:
            (is_approved, reason_message)
        """
        # 1. Duplicate position check for this symbol
        if self.has_open_position(symbol):
            return (
                False,
                f"Symbol {symbol} already has an active OPEN position",
            )

        # 2. Cooldown check
        if self.is_symbol_in_cooldown(symbol, current_bar_index):
            closed_at = self._cooldown_trackers[symbol]
            bars_since = current_bar_index - closed_at
            return (
                False,
                f"Symbol {symbol} in cooldown ({bars_since}/{self.symbol_cooldown_bars} bars elapsed)",
            )

        # 3. Same-bar entry limit check
        if self._entries_this_bar.get(current_bar_index, 0) >= self.max_entries_per_bar:
            return (
                False,
                f"Max entries per bar reached ({self._entries_this_bar.get(current_bar_index, 0)}/{self.max_entries_per_bar})",
            )

        # 4. Directional exposure limit check
        if side is not None:
            norm_side = side.upper()
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM paper_trades WHERE status = 'OPEN' AND side = ?", (norm_side,))
                row = cursor.fetchone()
                side_count = int(row[0]) if row else 0
            if side_count >= self.max_directional_positions:
                return (
                    False,
                    f"Directional exposure limit reached ({side_count}/{self.max_directional_positions} {norm_side}s active)",
                )

        # 5. Cross-asset correlation check (BTC benchmark)
        # When BTC is open in direction D, altcoins in direction D are capped to at most 1 concurrent position
        if side is not None and symbol != "BTCUSDT":
            norm_side = side.upper()
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT side FROM paper_trades WHERE symbol = 'BTCUSDT' AND status = 'OPEN' LIMIT 1"
                )
                btc_row = cursor.fetchone()
                if btc_row and btc_row[0].upper() == norm_side:
                    cursor.execute(
                        "SELECT COUNT(*) FROM paper_trades WHERE status = 'OPEN' AND side = ? AND symbol != 'BTCUSDT'",
                        (norm_side,),
                    )
                    alt_count = int(cursor.fetchone()[0])
                    if alt_count >= 1:
                        return (
                            False,
                            f"Correlation Gate: BTCUSDT is already {norm_side}; max 1 concurrent correlated altcoin allowed ({alt_count}/1 active)",
                        )

        # 6. Portfolio-wide active position limit check
        open_count = self.get_open_positions_count()
        if open_count >= self.max_open_positions:
            return (
                False,
                f"Portfolio limit reached ({open_count}/{self.max_open_positions} positions active)",
            )

        return True, "Approved"

    def record_position_opened(self, symbol: str, current_bar_index: int) -> None:
        """Record a new position entry on current_bar_index to enforce max_entries_per_bar."""
        self._entries_this_bar[current_bar_index] = self._entries_this_bar.get(current_bar_index, 0) + 1

    def record_position_closed(self, symbol: str, current_bar_index: int) -> None:
        """Record a position closure event to initiate symbol cooldown."""
        self._cooldown_trackers[symbol] = current_bar_index

    def find_preemption_candidate(
        self,
        active_traders: Dict[str, Any],
        candidate_score: float,
    ) -> Tuple[Optional[str], str]:
        """Evaluate open positions for stagnancy to allow an elite candidate to preempt a slot.
        
        Returns:
            (preempt_symbol, reason_description) if an eligible stagnant position is found,
            or (None, "") if no preemption is permitted.
        """
        if not self.allow_preemption:
            return None, "Preemption disabled"

        if candidate_score < self.preemption_min_candidate_score:
            return None, f"Candidate score {candidate_score:.1f} below preemption threshold {self.preemption_min_candidate_score:.1f}"

        open_count = self.get_open_positions_count()
        if open_count < self.max_open_positions:
            return None, "Portfolio not at capacity"

        candidates = []
        for sym, trader in active_traders.items():
            trade = trader.get_active_trade() if hasattr(trader, "get_active_trade") else None
            if not trade:
                continue

            # Immunity 1: If TP1 was already banked, position is protected
            if trade.get("tp1_hit", False):
                continue

            entry_bar = trade.get("entry_bar", 0)
            current_bar = getattr(trader, "bar_count", 0)
            bars_held = max(0, current_bar - entry_bar)

            # Stagnancy condition 1: Must be held for at least preemption_min_bars_held
            if bars_held < self.preemption_min_bars_held:
                continue

            # Stagnancy condition 2: Current floating R-multiple must be within deadband [-0.15R, +0.15R]
            entry = trade.get("entry_price", 0.0)
            sl = trade.get("initial_stop_loss", trade.get("stop_loss", 0.0))
            risk_dist = abs(entry - sl)
            side = trade.get("side", "LONG")
            curr_price = entry  # fallback
            if hasattr(trader, "candles") and len(trader.candles) > 0:
                curr_price = float(trader.candles[-1][3])

            curr_r = ((curr_price - entry) / risk_dist) if side == "LONG" else ((entry - curr_price) / risk_dist) if risk_dist > 0.0 else 0.0

            # If trade is winning (> +0.15R), it is immune
            if curr_r > self.preemption_max_r_abs:
                continue

            # If trade is severely underwater (< -0.50R), normal SL / time stop handles it
            if abs(curr_r) <= self.preemption_max_r_abs:
                candidates.append((bars_held, curr_r, sym))

        if not candidates:
            return None, "No active positions meet stagnancy preemption criteria"

        # Preempt the most stagnant position (highest bars held)
        candidates.sort(key=lambda x: x[0], reverse=True)
        chosen_bars, chosen_r, chosen_sym = candidates[0]
        return chosen_sym, f"Preempting stagnant {chosen_sym} (held {chosen_bars} bars, floating {chosen_r:+.2f}R) for elite score {candidate_score:.1f}"

    def get_portfolio_summary(self) -> Dict[str, Any]:
        """Aggregate total closed trades, win rate, returns, and capital allocation across all symbols."""
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT symbol, net_return_pct, r_multiple, reason
                FROM paper_trades
                WHERE status = 'CLOSED'
                """
            )
            rows = cursor.fetchall()

            cursor.execute(
                """
                SELECT symbol, notional_usd, quantity, leverage, side, entry_price
                FROM paper_trades
                WHERE status = 'OPEN'
                """
            )
            open_rows = cursor.fetchall()

        allocated_notional = sum(float(r["notional_usd"] or 0.0) for r in open_rows)
        portfolio_leverage = (allocated_notional / self.account_equity_usd) if self.account_equity_usd > 0.0 else 0.0
        available_equity = max(0.0, self.account_equity_usd - (allocated_notional / self.max_leverage))

        total_trades = len(rows)
        if total_trades == 0:
            return {
                "total_trades": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "win_rate_pct": 0.0,
                "total_net_return_pct": 0.0,
                "avg_r_multiple": 0.0,
                "symbols_traded": [],
                "account_equity_usd": self.account_equity_usd,
                "allocated_notional_usd": round(allocated_notional, 2),
                "portfolio_leverage": round(portfolio_leverage, 2),
                "available_equity_usd": round(available_equity, 2),
                "max_leverage": self.max_leverage,
                "max_open_positions": self.max_open_positions,
            }

        returns = [float(r["net_return_pct"]) for r in rows if r["net_return_pct"] is not None]
        r_multiples = [float(r["r_multiple"]) for r in rows if r["r_multiple"] is not None]
        wins = sum(1 for ret in returns if ret > 0)
        losses = sum(1 for ret in returns if ret <= 0)
        symbols = sorted(list(set(r["symbol"] for r in rows if r["symbol"] is not None)))

        return {
            "total_trades": total_trades,
            "winning_trades": wins,
            "losing_trades": losses,
            "win_rate_pct": (wins / total_trades) * 100.0,
            "total_net_return_pct": sum(returns),
            "avg_r_multiple": float(sum(r_multiples) / len(r_multiples)) if r_multiples else 0.0,
            "symbols_traded": symbols,
            "account_equity_usd": self.account_equity_usd,
            "allocated_notional_usd": round(allocated_notional, 2),
            "portfolio_leverage": round(portfolio_leverage, 2),
            "available_equity_usd": round(available_equity, 2),
            "max_leverage": self.max_leverage,
            "max_open_positions": self.max_open_positions,
        }

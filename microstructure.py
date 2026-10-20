import collections
import math
import time
from dataclasses import dataclass
from typing import Any, Deque, Dict, List, Optional, Tuple
import urllib.request
import json
import os
import sqlite3


@dataclass(frozen=True)
class WhaleTrade:
    """Represents a filtered high-notional trade (>= $50k)."""
    symbol: str
    price: float
    quantity: float
    notional: float
    side: str  # "BUY" or "SELL" (taker/aggressive side)
    badge: str  # "DOLPHIN", "SHARK", "WHALE"
    timestamp: float


@dataclass(frozen=True)
class LiquidationEvent:
    """Represents a forced liquidation order on Binance Futures."""
    symbol: str
    liquidated_position: str  # "LONG" or "SHORT"
    quantity: float
    price: float
    notional: float
    timestamp: float


class SymbolNormalizer:
    """Handles mapping between Spot symbols and Binance Futures contract tickers.
    
    For example: PEPEUSDT <-> 1000PEPEUSDT, SHIBUSDT <-> 1000SHIBUSDT.
    """
    _SPOT_TO_FUTURES = {
        "PEPEUSDT": "1000PEPEUSDT",
        "SHIBUSDT": "1000SHIBUSDT",
        "LUNCUSDT": "1000LUNCUSDT",
        "BONKUSDT": "1000BONKUSDT",
        "FLOKIUSDT": "1000FLOKIUSDT",
        "SATSUSDT": "1000SATSUSDT",
        "RATSUSDT": "1000RATSUSDT",
    }
    _FUTURES_TO_SPOT = {v: k for k, v in _SPOT_TO_FUTURES.items()}

    @classmethod
    def to_futures_symbol(cls, symbol: str) -> str:
        sym = symbol.upper()
        return cls._SPOT_TO_FUTURES.get(sym, sym)

    @classmethod
    def to_spot_symbol(cls, symbol: str) -> str:
        sym = symbol.upper()
        return cls._FUTURES_TO_SPOT.get(sym, sym)


WHALE_TIER_THRESHOLDS: Dict[str, Tuple[float, float, float]] = {
    # Tier 1: Mega-Cap (BTC)
    "BTCUSDT": (50_000.0, 100_000.0, 500_000.0),
    # Tier 2: Large-Cap (ETH, SOL, BNB, XRP)
    "ETHUSDT": (25_000.0, 50_000.0, 150_000.0),
    "SOLUSDT": (25_000.0, 50_000.0, 150_000.0),
    "BNBUSDT": (25_000.0, 50_000.0, 150_000.0),
    "XRPUSDT": (25_000.0, 50_000.0, 150_000.0),
    # Tier 3: Mid-Cap / Alts (Default)
    "DEFAULT": (10_000.0, 25_000.0, 50_000.0),
}


def get_whale_thresholds(symbol: Optional[str] = None) -> Tuple[float, float, float]:
    """Return (dolphin_threshold, shark_threshold, whale_threshold) scaled by asset tier."""
    if not symbol:
        return WHALE_TIER_THRESHOLDS["BTCUSDT"]
    sym = symbol.upper()
    return WHALE_TIER_THRESHOLDS.get(sym, WHALE_TIER_THRESHOLDS["DEFAULT"])


class WhaleTradeFilter:
    """Filters Binance aggTrade streams for institutional notional volume scaled by asset tier."""

    def __init__(
        self,
        dolphin_threshold: Optional[float] = None,
        shark_threshold: Optional[float] = None,
        whale_threshold: Optional[float] = None,
        symbol: Optional[str] = None,
        max_tape_len: int = 50,
    ):
        self.symbol = symbol.upper() if symbol else None
        def_dolphin, def_shark, def_whale = get_whale_thresholds(self.symbol)
        self.dolphin_threshold = dolphin_threshold if dolphin_threshold is not None else def_dolphin
        self.shark_threshold = shark_threshold if shark_threshold is not None else def_shark
        self.whale_threshold = whale_threshold if whale_threshold is not None else def_whale
        self.tape: Deque[WhaleTrade] = collections.deque(maxlen=max_tape_len)

    def process_trade(self, raw_data: dict) -> Optional[WhaleTrade]:
        """Process a Binance aggTrade payload.
        
        Binance payload format:
        - "s": symbol
        - "p": price
        - "q": quantity
        - "m": is_buyer_maker (True = Buyer is maker -> Seller is taker -> Market SELL)
        - "T": trade_time_ms
        """
        symbol = str(raw_data.get("s", "")).upper()
        price = float(raw_data.get("p", 0.0))
        qty = float(raw_data.get("q", 0.0))
        notional = price * qty

        if notional < self.dolphin_threshold:
            return None

        # Maker/Taker mapping: m: True means buyer was maker -> Market SELL
        taker_side = "SELL" if raw_data.get("m") is True else "BUY"

        if notional >= self.whale_threshold:
            badge = "WHALE"
        elif notional >= self.shark_threshold:
            badge = "SHARK"
        else:
            badge = "DOLPHIN"

        raw_ts = raw_data.get("T")
        ts = (float(raw_ts) / 1000.0) if raw_ts else time.time()

        trade = WhaleTrade(
            symbol=symbol,
            price=price,
            quantity=qty,
            notional=notional,
            side=taker_side,
            badge=badge,
            timestamp=ts,
        )
        self.tape.append(trade)
        return trade

    def get_time_decayed_net_delta(
        self,
        symbol: Optional[str] = None,
        window_seconds: float = 900.0,
        tau: float = 300.0,
        current_time: Optional[float] = None,
    ) -> float:
        """Calculate exponential time-decayed net whale delta ($).

        Formula: weight = exp(-(now - trade_ts) / tau)
        Net delta = sum(sign * notional * weight)
        where sign is +1 for BUY (taker buying) and -1 for SELL (taker selling).
        Trades older than window_seconds are ignored.
        """
        now = time.time() if current_time is None else current_time
        sym = symbol.upper() if symbol else None
        decayed_delta = 0.0

        for trade in self.tape:
            if sym is not None:
                trade_sym = getattr(trade, "symbol", "").upper()
                if (
                    trade_sym != sym
                    and SymbolNormalizer.to_spot_symbol(trade_sym) != sym
                    and SymbolNormalizer.to_futures_symbol(sym) != trade_sym
                ):
                    continue

            ts = getattr(trade, "timestamp", now)
            age_sec = now - ts
            if age_sec < 0:
                age_sec = 0.0
            if window_seconds is not None and age_sec > window_seconds:
                continue

            weight = math.exp(-age_sec / tau) if tau > 0 else 1.0
            sign = 1.0 if getattr(trade, "side", "BUY") == "BUY" else -1.0
            decayed_delta += sign * float(getattr(trade, "notional", 0.0)) * weight

        return decayed_delta


class LiquidationTracker:
    """Tracks forced liquidation orders, rolling 60s volume, and cascade alerts."""

    def __init__(
        self,
        cascade_threshold: float = 1_000_000.0,
        window_seconds: float = 60.0,
        max_events: int = 500,
    ):
        self.cascade_threshold = cascade_threshold
        self.window_seconds = window_seconds
        self.liquidations: Deque[LiquidationEvent] = collections.deque(maxlen=max_events)

    def process_order(self, raw_order: dict) -> Optional[LiquidationEvent]:
        """Process a Binance forceOrder 'o' payload.
        
        Binance forceOrder format:
        - "s": symbol
        - "S": side ("SELL" = forced sale of Long; "BUY" = forced cover of Short)
        - "q": quantity
        - "p": price
        - "T": trade_time_ms
        """
        symbol = str(raw_order.get("s", "")).upper()
        raw_side = str(raw_order.get("S", "")).upper()
        qty = float(raw_order.get("q", 0.0))
        price = float(raw_order.get("p", 0.0))
        notional = qty * price

        raw_ts = raw_order.get("T")
        ts = (float(raw_ts) / 1000.0) if raw_ts else time.time()

        # Direction mapping: S: "SELL" means a LONG position was liquidated
        liquidated_pos = "LONG" if raw_side == "SELL" else "SHORT"

        event = LiquidationEvent(
            symbol=symbol,
            liquidated_position=liquidated_pos,
            quantity=qty,
            price=price,
            notional=notional,
            timestamp=ts,
        )
        self.liquidations.append(event)
        return event

    def get_rolling_summary(
        self,
        symbol: Optional[str] = None,
        side: Optional[str] = None,
        current_time: Optional[float] = None,
    ) -> Tuple[float, bool]:
        """Returns (rolling_60s_notional, is_cascade_active).
        
        If side is provided ("LONG" or "SHORT"), only liquidations of that position side are counted.
        """
        now = time.time() if current_time is None else current_time
        cutoff = now - self.window_seconds
        req_side = side.upper() if side else None
        matching = [
            liq.notional
            for liq in self.liquidations
            if liq.timestamp >= cutoff
            and (symbol is None or liq.symbol == symbol.upper() or SymbolNormalizer.to_spot_symbol(liq.symbol) == symbol.upper())
            and (req_side is None or getattr(liq, "liquidated_position", "") == req_side)
        ]
        total_vol = sum(matching)
        return total_vol, total_vol >= self.cascade_threshold

    def get_directional_summary(
        self,
        symbol: Optional[str] = None,
        current_time: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Returns directional liquidation metrics: long vs short volume and cascade status."""
        long_vol, is_long_cascade = self.get_rolling_summary(symbol=symbol, side="LONG", current_time=current_time)
        short_vol, is_short_cascade = self.get_rolling_summary(symbol=symbol, side="SHORT", current_time=current_time)
        total_vol, is_any_cascade = self.get_rolling_summary(symbol=symbol, side=None, current_time=current_time)
        return {
            "total_vol": total_vol,
            "is_cascade": is_any_cascade,
            "long_liq_vol": long_vol,
            "is_long_cascade": is_long_cascade,
            "short_liq_vol": short_vol,
            "is_short_cascade": is_short_cascade,
        }


class OpenInterestEngine:
    """Evaluates 4-Quadrant institutional OI diagnostic matrix with deadband noise filtering."""

    def __init__(
        self,
        min_oi_delta_pct: float = 0.2,
        min_price_delta_pct: float = 0.1,
        max_history_len: int = 30,
    ):
        self.min_oi_delta = min_oi_delta_pct
        self.min_price_delta = min_price_delta_pct
        self.max_history_len = max_history_len
        # symbol -> [(timestamp, price, open_interest)]
        self.history: Dict[str, List[Tuple[float, float, float]]] = {}
        self.current_regimes: Dict[str, str] = {}

    def update(
        self,
        symbol: str,
        price: float,
        open_interest: float,
        timestamp: Optional[float] = None,
    ) -> str:
        """Ingest a new price and Open Interest snapshot and determine the market regime."""
        sym = symbol.upper()
        if timestamp is None:
            timestamp = time.time()

        if sym not in self.history:
            self.history[sym] = []
            self.current_regimes[sym] = "NEUTRAL"

        hist = self.history[sym]
        hist.append((timestamp, price, open_interest))
        if len(hist) > self.max_history_len:
            hist.pop(0)

        # Tactical window: evaluate price and OI delta across last 4 bars (1h on 15m), rather than stale 30 bars (7.5h)
        compare_idx = -4 if len(hist) >= 4 else 0
        prev_price = hist[compare_idx][1]
        prev_oi = hist[compare_idx][2]

        if prev_price <= 0.0 or prev_oi <= 0.0:
            return self.current_regimes[sym]

        price_pct = ((price - prev_price) / prev_price) * 100.0
        oi_pct = ((open_interest - prev_oi) / prev_oi) * 100.0

        # Deadband filter to reject noise
        if abs(oi_pct) < self.min_oi_delta or abs(price_pct) < self.min_price_delta:
            return self.current_regimes[sym]

        if price_pct > 0 and oi_pct > 0:
            regime = "LONG_BUILDUP"
        elif price_pct > 0 and oi_pct < 0:
            regime = "SHORT_SQUEEZE"
        elif price_pct < 0 and oi_pct > 0:
            regime = "SHORT_BUILDUP"
        else:
            regime = "LONG_LIQUIDATION"

        self.current_regimes[sym] = regime
        return regime

    def get_regime(self, symbol: str) -> str:
        """Return the current active regime for a symbol."""
        return self.current_regimes.get(symbol.upper(), "NEUTRAL")


class FundingRateTracker:
    """Tracks live funding rates, dynamic countdown, and annualized APR %."""

    def __init__(self):
        self.rates: Dict[str, float] = {}  # symbol -> last_funding_rate
        self.next_funding_times: Dict[str, int] = {}  # symbol -> next_funding_time_ms
        self.cycle_hours: Dict[str, float] = {}  # symbol -> cycle_hours (usually 8.0)

    def update_funding(
        self,
        symbol: str,
        funding_rate: float,
        next_funding_time_ms: int,
        cycle_hours: float = 8.0,
    ) -> None:
        sym = symbol.upper()
        self.rates[sym] = funding_rate
        self.next_funding_times[sym] = next_funding_time_ms
        self.cycle_hours[sym] = cycle_hours

    @staticmethod
    def calculate_apr(funding_rate: float, cycle_hours: float = 8.0) -> float:
        """Calculate annualized APR % based on funding rate and cycle frequency."""
        if cycle_hours <= 0:
            cycle_hours = 8.0
        periods_per_day = 24.0 / cycle_hours
        return funding_rate * periods_per_day * 365.0 * 100.0

    @staticmethod
    def format_countdown(next_funding_time_ms: int, current_time_ms: Optional[int] = None) -> str:
        """Format remaining countdown to next funding event as HH:MM:SS."""
        if current_time_ms is None:
            current_time_ms = int(time.time() * 1000)
        diff_sec = max(0, int((next_funding_time_ms - current_time_ms) / 1000))
        hours, remainder = divmod(diff_sec, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    def get_summary(self, symbol: str) -> Tuple[float, str]:
        """Returns (annualized_apr_pct, countdown_str)."""
        sym = symbol.upper()
        if sym not in self.rates:
            return 0.0, "--:--:--"
        rate = self.rates[sym]
        cycle = self.cycle_hours.get(sym, 8.0)
        next_t = self.next_funding_times.get(sym, 0)
        apr = self.calculate_apr(rate, cycle)
        countdown = self.format_countdown(next_t)
        return apr, countdown


class FuturesHealthProber:
    """Probes Binance Futures connectivity on startup, detecting regional firewall/geo-blocks."""

    @staticmethod
    def probe_connectivity(timeout: float = 3.0) -> Tuple[bool, str]:
        """Probe Binance Futures ping endpoint.
        
        Returns:
            (is_available, status_message)
        """
        url = "https://fapi.binance.com/fapi/v1/ping"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                if response.status == 200:
                    return True, "Binance Futures Online (HTTP 200)"
                return False, f"Unexpected HTTP status: {response.status}"
        except urllib.error.HTTPError as e:
            if e.code in (403, 451):
                return False, f"Binance Futures Geoblocked/Restricted (HTTP {e.code})"
            return False, f"Binance Futures HTTP Error: {e.code}"
        except Exception as e:
            return False, f"Binance Futures Network Error: {e}"


def fetch_historical_open_interest(
    symbol: str,
    period: str = "15m",
    total_records: int = 2500,
    cache_db: str = "data/klines_cache.db",
    force_fresh: bool = False,
) -> List[Tuple[int, float, float]]:
    """Fetch and locally cache historical Binance Futures Open Interest snapshots.

    Returns:
        List of (timestamp_ms, sum_open_interest, sum_open_interest_value) sorted ascending.
    """
    os.makedirs(os.path.dirname(cache_db), exist_ok=True)
    futures_sym = SymbolNormalizer.to_futures_symbol(symbol).upper()
    now_ms = int(time.time() * 1000)

    with sqlite3.connect(cache_db) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS historical_oi (
                symbol TEXT NOT NULL,
                timestamp INTEGER NOT NULL,
                sum_open_interest REAL NOT NULL,
                sum_open_interest_value REAL NOT NULL,
                PRIMARY KEY (symbol, timestamp)
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_historical_oi_lookup
            ON historical_oi (symbol, timestamp ASC)
            """
        )
        conn.commit()

        cursor = conn.cursor()

        if force_fresh:
            cursor.execute("DELETE FROM historical_oi WHERE symbol = ?", (futures_sym,))
            conn.commit()

        cursor.execute(
            "SELECT MIN(timestamp), MAX(timestamp), COUNT(*) FROM historical_oi WHERE symbol = ?",
            (futures_sym,),
        )
        row = cursor.fetchone()
        min_ts, max_ts, cached_count = row if row else (None, None, 0)
        cached_count = cached_count or 0

        # Step 1: Forward top-up if max_ts is behind current time
        interval_ms = 900_000 if period == "15m" else 3_600_000
        if not force_fresh and cached_count > 0 and max_ts is not None:
            if (now_ms - max_ts) > (interval_ms * 2):
                curr_start = max(max_ts + 1, now_ms - (28 * 86400 * 1000))
                while curr_start < now_ms:
                    url = f"https://fapi.binance.com/futures/data/openInterestHist?symbol={futures_sym}&period={period}&startTime={curr_start}&limit=500"
                    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
                    try:
                        with urllib.request.urlopen(req, timeout=10) as resp:
                            if resp.status == 200:
                                data = json.loads(resp.read().decode())
                                if not data:
                                    break
                                batch = [
                                    (futures_sym, int(d["timestamp"]), float(d["sumOpenInterest"]), float(d["sumOpenInterestValue"]))
                                    for d in data
                                ]
                                cursor.executemany(
                                    "INSERT OR IGNORE INTO historical_oi (symbol, timestamp, sum_open_interest, sum_open_interest_value) VALUES (?, ?, ?, ?)",
                                    batch,
                                )
                                conn.commit()
                                curr_start = batch[-1][1] + interval_ms
                                if len(data) < 500:
                                    break
                                time.sleep(0.1)
                            else:
                                break
                    except Exception:
                        break

                cursor.execute(
                    "SELECT MIN(timestamp), MAX(timestamp), COUNT(*) FROM historical_oi WHERE symbol = ?",
                    (futures_sym,),
                )
                min_ts, max_ts, cached_count = cursor.fetchone()
                cached_count = cached_count or 0

        # Step 2: Backward backfill if cached_count < total_records
        if cached_count < total_records:
            needed = total_records - cached_count
            print(f"[OI INGESTION] SQLite has {cached_count} snapshots. Fetching historical OI for {futures_sym} from Binance...")
            end_time = (min_ts - 1) if (min_ts is not None and cached_count > 0) else None
            thirty_days_cutoff = now_ms - (29 * 86400 * 1000)

            while needed > 0:
                if end_time is not None and end_time < thirty_days_cutoff:
                    break

                limit = min(500, needed)
                url = f"https://fapi.binance.com/futures/data/openInterestHist?symbol={futures_sym}&period={period}&limit={limit}"
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
                                (futures_sym, int(d["timestamp"]), float(d["sumOpenInterest"]), float(d["sumOpenInterestValue"]))
                                for d in data
                            ]
                            cursor.executemany(
                                "INSERT OR IGNORE INTO historical_oi (symbol, timestamp, sum_open_interest, sum_open_interest_value) VALUES (?, ?, ?, ?)",
                                batch,
                            )
                            conn.commit()
                            earliest_ms = batch[0][1]
                            end_time = earliest_ms - 1
                            needed -= len(batch)
                            if len(data) < limit:
                                break
                            time.sleep(0.15)
                        else:
                            break
                except Exception:
                    break
        else:
            print(f"[OI CACHE HIT] Loaded {total_records} OI snapshots for {futures_sym} from local SQLite cache (<5ms).")

        # Step 3: Query and return requested total_records
        cursor.execute(
            """
            SELECT timestamp, sum_open_interest, sum_open_interest_value
            FROM (
                SELECT timestamp, sum_open_interest, sum_open_interest_value
                FROM historical_oi
                WHERE symbol = ?
                ORDER BY timestamp DESC
                LIMIT ?
            )
            ORDER BY timestamp ASC
            """,
            (futures_sym, total_records),
        )
        rows = cursor.fetchall()
        return [(int(r[0]), float(r[1]), float(r[2])) for r in rows]


def fetch_historical_funding_rates(
    symbol: str,
    total_records: int = 1000,
    cache_db: str = "data/klines_cache.db",
    force_fresh: bool = False,
) -> List[Tuple[int, float, float]]:
    """Fetch and locally cache historical Binance Futures funding rates.

    Returns:
        List of (funding_time_ms, funding_rate, mark_price) sorted ascending.
    """
    os.makedirs(os.path.dirname(cache_db), exist_ok=True)
    futures_sym = SymbolNormalizer.to_futures_symbol(symbol).upper()

    with sqlite3.connect(cache_db) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS historical_funding (
                symbol TEXT NOT NULL,
                funding_time INTEGER NOT NULL,
                funding_rate REAL NOT NULL,
                mark_price REAL NOT NULL,
                PRIMARY KEY (symbol, funding_time)
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_historical_funding_lookup
            ON historical_funding (symbol, funding_time ASC)
            """
        )
        conn.commit()

        cursor = conn.cursor()

        if force_fresh:
            cursor.execute("DELETE FROM historical_funding WHERE symbol = ?", (futures_sym,))
            conn.commit()

        cursor.execute(
            "SELECT MIN(funding_time), MAX(funding_time), COUNT(*) FROM historical_funding WHERE symbol = ?",
            (futures_sym,),
        )
        row = cursor.fetchone()
        min_ts, max_ts, cached_count = row if row else (None, None, 0)
        cached_count = cached_count or 0

        if cached_count < total_records:
            needed = total_records - cached_count
            end_time = (min_ts - 1) if (min_ts is not None and cached_count > 0) else None

            while needed > 0:
                limit = min(1000, needed)
                url = f"https://fapi.binance.com/fapi/v1/fundingRate?symbol={futures_sym}&limit={limit}"
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
                                (futures_sym, int(d["fundingTime"]), float(d["fundingRate"]), float(d.get("markPrice", 0.0)))
                                for d in data
                            ]
                            cursor.executemany(
                                "INSERT OR IGNORE INTO historical_funding (symbol, funding_time, funding_rate, mark_price) VALUES (?, ?, ?, ?)",
                                batch,
                            )
                            conn.commit()
                            earliest_ms = batch[0][1]
                            end_time = earliest_ms - 1
                            needed -= len(batch)
                            if len(data) < limit:
                                break
                            time.sleep(0.15)
                        else:
                            break
                except Exception:
                    break

        cursor.execute(
            """
            SELECT funding_time, funding_rate, mark_price
            FROM (
                SELECT funding_time, funding_rate, mark_price
                FROM historical_funding
                WHERE symbol = ?
                ORDER BY funding_time DESC
                LIMIT ?
            )
            ORDER BY funding_time ASC
            """,
            (futures_sym, total_records),
        )
        rows = cursor.fetchall()
        return [(int(r[0]), float(r[1]), float(r[2])) for r in rows]


class HistoricalMicrostructureReplayer:
    """Historical microstructure replay adapter compatible with PaperTrader.

    Provides duck-typed access to:
    - replayer.oi_engine.get_regime(symbol)
    - replayer.funding_tracker.get_summary(symbol)
    - replayer.futures_to_spot
    """

    def __init__(self, symbol: str):
        self.symbol = symbol.upper()
        self.futures_symbol = SymbolNormalizer.to_futures_symbol(self.symbol)
        self.futures_to_spot = {self.futures_symbol: self.symbol}
        self.oi_engine = OpenInterestEngine()
        self.funding_tracker = FundingRateTracker()

    def update_step(
        self,
        price: float,
        oi_record: Optional[Tuple[int, float, float]] = None,
        funding_record: Optional[Tuple[int, float, float]] = None,
    ) -> None:
        """Update chronological microstructure engines with the latest bar's closed snapshot."""
        if oi_record:
            oi_ts_ms, sum_oi, sum_oi_val = oi_record
            self.oi_engine.update(
                self.symbol,
                price=price,
                open_interest=sum_oi,
                timestamp=oi_ts_ms / 1000.0,
            )
        if funding_record:
            funding_ts_ms, funding_rate, mark_price = funding_record
            self.funding_tracker.update_funding(
                self.symbol,
                funding_rate=funding_rate,
                next_funding_time_ms=funding_ts_ms + (8 * 3600 * 1000),
            )


@dataclass(frozen=True)
class OrderBookSnapshot:
    """Represents a validated L2 Order Book depth state with quantitative microstructure metrics."""
    symbol: str
    timestamp_ms: int
    bids: Tuple[Tuple[float, float, float], ...]  # ((price, qty, cumulative_total), ...)
    asks: Tuple[Tuple[float, float, float], ...]  # ((price, qty, cumulative_total), ...)
    best_bid: float
    best_ask: float
    spread: float
    spread_bps: float
    mid_price: float
    weighted_mid: float
    imbalance_pct: float  # -100 to +100
    bid_walls: Tuple[Tuple[float, float], ...]  # ((price, qty), ...)
    ask_walls: Tuple[Tuple[float, float], ...]  # ((price, qty), ...)
    status: str = "LIVE"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timestamp_ms": self.timestamp_ms,
            "bids": [{"price": b[0], "qty": b[1], "total": b[2]} for b in self.bids],
            "asks": [{"price": a[0], "qty": a[1], "total": a[2]} for a in self.asks],
            "best_bid": self.best_bid,
            "best_ask": self.best_ask,
            "spread": round(self.spread, 6),
            "spread_bps": round(self.spread_bps, 2),
            "mid_price": self.mid_price,
            "weighted_mid": self.weighted_mid,
            "imbalance_pct": round(self.imbalance_pct, 1),
            "bid_walls": [{"price": w[0], "qty": w[1]} for w in self.bid_walls],
            "ask_walls": [{"price": w[0], "qty": w[1]} for w in self.ask_walls],
            "status": self.status,
        }


class OrderBookEngine:
    """Institutional Order Book L2 Depth Engine.

    Features:
    - Maintains top-20 bid/ask depth ladders with running cumulative volume totals.
    - Vectorized Order Book Imbalance (OBI) computation with deadband noise filtering.
    - Institutional wall detector identifying resting limit clusters >= wall_multiplier * mean depth.
    - Volume-weighted micro-price calculation.
    """

    def __init__(self, wall_multiplier: float = 2.5, depth_levels: int = 20):
        self.wall_multiplier = wall_multiplier
        self.depth_levels = depth_levels
        self._books: Dict[str, OrderBookSnapshot] = {}

    def process_depth_payload(self, symbol: str, payload: dict) -> OrderBookSnapshot:
        """Process a Binance @depth20 or @depth payload and calculate microstructure metrics."""
        sym = symbol.upper()
        raw_bids = payload.get("bids") or payload.get("b") or []
        raw_asks = payload.get("asks") or payload.get("a") or []
        raw_ts = payload.get("E") or payload.get("T")
        ts_ms = int(raw_ts) if raw_ts else int(time.time() * 1000)

        # Parse and sort bids (descending) & asks (ascending)
        parsed_bids = []
        for item in raw_bids[:self.depth_levels]:
            try:
                p, q = float(item[0]), float(item[1])
                if p > 0.0 and q > 0.0:
                    parsed_bids.append((p, q))
            except (ValueError, TypeError, IndexError):
                continue
        parsed_bids.sort(key=lambda x: -x[0])

        parsed_asks = []
        for item in raw_asks[:self.depth_levels]:
            try:
                p, q = float(item[0]), float(item[1])
                if p > 0.0 and q > 0.0:
                    parsed_asks.append((p, q))
            except (ValueError, TypeError, IndexError):
                continue
        parsed_asks.sort(key=lambda x: x[0])

        # If empty payload, return cached or fallback
        if not parsed_bids or not parsed_asks:
            if sym in self._books:
                return self._books[sym]
            return self._create_fallback_snapshot(sym, ts_ms)

        # Calculate cumulative totals
        bids_with_tot = []
        cum_bid = 0.0
        for p, q in parsed_bids:
            cum_bid += q
            bids_with_tot.append((p, q, round(cum_bid, 4)))

        asks_with_tot = []
        cum_ask = 0.0
        for p, q in parsed_asks:
            cum_ask += q
            asks_with_tot.append((p, q, round(cum_ask, 4)))

        best_bid = parsed_bids[0][0]
        best_bid_q = parsed_bids[0][1]
        best_ask = parsed_asks[0][0]
        best_ask_q = parsed_asks[0][1]

        spread = max(0.0, best_ask - best_bid)
        mid_price = (best_bid + best_ask) / 2.0 if (best_bid + best_ask) > 0 else best_bid
        spread_bps = (spread / mid_price * 10000.0) if mid_price > 0 else 0.0

        # Weighted micro-price
        top_qty_sum = best_bid_q + best_ask_q
        if top_qty_sum > 0:
            weighted_mid = (best_bid * best_ask_q + best_ask * best_bid_q) / top_qty_sum
        else:
            weighted_mid = mid_price

        # Order Book Imbalance (OBI) across all top-20 levels
        tot_depth_vol = cum_bid + cum_ask
        if tot_depth_vol > 0:
            imbalance_pct = ((cum_bid - cum_ask) / tot_depth_vol) * 100.0
        else:
            imbalance_pct = 0.0

        # Liquidity wall detection
        mean_bid_qty = (cum_bid / len(parsed_bids)) if parsed_bids else 1.0
        mean_ask_qty = (cum_ask / len(parsed_asks)) if parsed_asks else 1.0
        bid_wall_threshold = mean_bid_qty * self.wall_multiplier
        ask_wall_threshold = mean_ask_qty * self.wall_multiplier

        bid_walls = tuple((p, q) for p, q, _ in bids_with_tot if q >= bid_wall_threshold)
        ask_walls = tuple((p, q) for p, q, _ in asks_with_tot if q >= ask_wall_threshold)

        snapshot = OrderBookSnapshot(
            symbol=sym,
            timestamp_ms=ts_ms,
            bids=tuple(bids_with_tot),
            asks=tuple(asks_with_tot),
            best_bid=best_bid,
            best_ask=best_ask,
            spread=spread,
            spread_bps=spread_bps,
            mid_price=mid_price,
            weighted_mid=weighted_mid,
            imbalance_pct=imbalance_pct,
            bid_walls=bid_walls,
            ask_walls=ask_walls,
        )
        self._books[sym] = snapshot
        return snapshot

    def get_snapshot(self, symbol: str, fallback_price: float = 50000.0) -> OrderBookSnapshot:
        """Retrieve latest snapshot for symbol, or generate a valid fallback."""
        sym = symbol.upper()
        if sym in self._books:
            return self._books[sym]
        return self._create_fallback_snapshot(sym, int(time.time() * 1000), fallback_price=fallback_price)

    def _create_fallback_snapshot(
        self,
        symbol: str,
        timestamp_ms: int,
        fallback_price: float = 50000.0,
    ) -> OrderBookSnapshot:
        """Create a valid baseline snapshot when exchange feed is warming up."""
        step = fallback_price * 0.0002
        bids = []
        cum_b = 0.0
        for i in range(1, 9):
            p = round(fallback_price - i * step, 2)
            q = round(1.0 + (i * 0.2), 3)
            cum_b += q
            bids.append((p, q, round(cum_b, 4)))

        asks = []
        cum_a = 0.0
        for i in range(1, 9):
            p = round(fallback_price + i * step, 2)
            q = round(1.0 + (i * 0.2), 3)
            cum_a += q
            asks.append((p, q, round(cum_a, 4)))

        return OrderBookSnapshot(
            symbol=symbol,
            timestamp_ms=timestamp_ms,
            bids=tuple(bids),
            asks=tuple(asks),
            best_bid=bids[0][0],
            best_ask=asks[0][0],
            spread=step,
            spread_bps=(step / fallback_price) * 10000.0,
            mid_price=fallback_price,
            weighted_mid=fallback_price,
            imbalance_pct=0.0,
            bid_walls=(),
            ask_walls=(),
            status="DISCONNECTED",
        )


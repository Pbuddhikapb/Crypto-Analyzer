import collections
import time
from dataclasses import dataclass
from typing import Deque, Dict, List, Optional, Tuple
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

    def get_rolling_summary(self, symbol: Optional[str] = None) -> Tuple[float, bool]:
        """Returns (rolling_60s_notional, is_cascade_active)."""
        now = time.time()
        cutoff = now - self.window_seconds
        matching = [
            liq.notional
            for liq in self.liquidations
            if liq.timestamp >= cutoff and (symbol is None or liq.symbol == symbol.upper())
        ]
        total_vol = sum(matching)
        return total_vol, total_vol >= self.cascade_threshold


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

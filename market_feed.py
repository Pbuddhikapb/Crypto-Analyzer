"""
Market Feed Module (Area A: Data).

Provides Binance USD-M Futures real-time market data transport, including:
- Combined multiplexed WebSocket stream URL generation
- Parallel REST candle warmup with connection pooling
- Periodic higher-timeframe (HTF) candle polling
- Real-time futures microstructure stream management
"""

import asyncio
import sys
import time
from collections import deque
from typing import Any, Dict, List, Optional, Sequence, Tuple

import aiohttp

from indicators import calculate_ema

_calc_ema = calculate_ema


def timeframe_to_seconds(tf: str) -> int:
    """Convert timeframe string (e.g. 1m, 15m, 1h, 4h, 1d) to integer seconds."""
    if tf.endswith("m"):
        return int(tf[:-1]) * 60
    elif tf.endswith("h"):
        return int(tf[:-1]) * 3600
    elif tf.endswith("d"):
        return int(tf[:-1]) * 86400
    return 60




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
        self.history_source: str = "none"  # "rest_futures" | "parquet" | "none"
        self.history_end_ms: Optional[int] = None
        self.gap_detected: bool = False
        self.live_start_ms: Optional[int] = None

    def handle_live_data_start(
        self,
        live_start_ms: int,
        timestamps_list: Optional[List[int]] = None,
    ) -> bool:
        """
        Handle start of live data. If live data starts more than 2 candle intervals
        after history_end_ms, treat it as a gap: start the series after the gap
        and do not compute indicators across it.
        Returns True if a gap was detected, False otherwise.
        """
        if self.live_start_ms is None:
            self.live_start_ms = live_start_ms

        if self.history_end_ms is not None and not self.gap_detected:
            interval_sec = timeframe_to_seconds(self.timeframe)
            interval_ms = interval_sec * 1000
            if (live_start_ms - self.history_end_ms) > 2 * interval_ms:
                self.gap_detected = True
                self.candles.clear()
                if timestamps_list is not None:
                    timestamps_list.clear()
                self.bar_count = 0
                return True
        return False

    def load_historical_candle(self, candle_data: Any) -> None:
        self.candles.append(tuple(candle_data))
        self.bar_count += 1

    def load_historical_htf_candle(self, candle_data: Any) -> None:
        self.htf_candles.append(tuple(candle_data))

    def add_candle(self, candle_data: Any, timestamp: Optional[str] = None) -> None:
        """Append a closed candle to the rolling buffer."""
        if timestamp is not None:
            try:
                raw_t = int(timestamp)
                self.handle_live_data_start(raw_t)
            except (ValueError, TypeError):
                pass
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
        htf_closes = [float(c[3]) for c in self.htf_candles]
        ema_50 = _calc_ema(htf_closes, 50)
        curr_close = htf_closes[-1]
        if len(htf_closes) >= 200:
            ema_200 = _calc_ema(htf_closes, 200)
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


MarketDataFeed = CandleFeedBuffer


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

DEFAULT_MONITORED_SYMBOLS: List[str] = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "XRPUSDT",
    "BNBUSDT",
    "DOGEUSDT",
    "ADAUSDT",
    "AVAXUSDT",
    "SUIUSDT",
    "LINKUSDT",
]
TOP_10_SYMBOLS: List[str] = DEFAULT_MONITORED_SYMBOLS


def get_combined_stream_url(symbols: Sequence[str], timeframe: str) -> str:
    """Build Binance USD-M Futures multiplexed WebSocket stream URL."""
    stream_params = "/".join(f"{s.strip().lower()}@kline_{timeframe}" for s in symbols)
    return f"wss://fstream.binance.com/market/stream?streams={stream_params}"


async def warmup_symbol(
    session: aiohttp.ClientSession,
    symbol: str,
    timeframe: str,
    trader: Any,
    timestamps_list: Optional[List[int]] = None,
) -> None:
    """Fetch initial 250 klines for a single symbol from Binance USD-M Futures and pre-seed buffers."""
    url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol.upper()}&interval={timeframe}&limit=250"
    backoff = 1.0
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
                        if timestamps_list is not None:
                            raw_t = int(k[0])
                            timestamps_list.append(raw_t // 1000 if raw_t > 10_000_000_000 else raw_t)
                    break
                elif response.status == 429:
                    print(f"[WARMUP RATE LIMIT] 429 for {symbol}. Waiting {backoff * 2:.1f}s before retry...")
                    await asyncio.sleep(backoff * 2)
                    backoff *= 2
                else:
                    print(f"[WARMUP WARNING] HTTP {response.status} for {symbol} (attempt {attempt}/3)")
                    await asyncio.sleep(backoff)
                    backoff *= 2
        except Exception as e:
            if attempt == 3:
                raise RuntimeError(f"Failed to warm up {symbol} from Binance USD-M futures after 3 attempts: {e}")
            await asyncio.sleep(backoff)
            backoff *= 2

    # If buffer/trader has HTF trend filter enabled, also prime 4h macro candles from USD-M futures
    if getattr(trader, "htf_trend_filter", False):
        htf_url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol.upper()}&interval=4h&limit=250"
        htf_backoff = 1.0
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
                        await asyncio.sleep(htf_backoff * 2)
                        htf_backoff *= 2
                    else:
                        await asyncio.sleep(htf_backoff)
                        htf_backoff *= 2
            except Exception:
                if attempt == 3:
                    print(f"[WARMUP WARNING] Failed to prime 4h HTF candles for {symbol} from USD-M futures")
                await asyncio.sleep(htf_backoff)
                htf_backoff *= 2


async def warmup_all_traders(
    traders: Dict[str, Any],
    symbols: List[str],
    timeframe: str,
    timestamps_dict: Optional[Dict[str, List[int]]] = None,
    warmup_fn: Optional[Any] = None,
) -> None:
    """Perform parallel REST warmup across all symbols using connection pooling."""
    print(f"\n[WARMUP] Priming 250 {timeframe} candles across {len(symbols)} coins in parallel...")
    start_t = time.perf_counter()
    _warmup = warmup_fn or warmup_symbol

    connector = aiohttp.TCPConnector(limit=10)
    async with aiohttp.ClientSession(connector=connector) as session:
        if timestamps_dict is not None:
            tasks = [
                _warmup(
                    session,
                    sym,
                    timeframe,
                    traders[sym],
                    timestamps_list=timestamps_dict.get(sym),
                )
                for sym in symbols
            ]
        else:
            tasks = [
                _warmup(
                    session,
                    sym,
                    timeframe,
                    traders[sym],
                )
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


async def poll_htf_candles(
    traders: Dict[str, Any],
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
                    url = f"https://fapi.binance.com/fapi/v1/klines?symbol={sym.upper()}&interval=4h&limit=2"
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
                                    if hasattr(trader, "on_htf_candle"):
                                        trader.on_htf_candle(candle_tuple)
                                    elif hasattr(trader, "load_historical_htf_candle"):
                                        trader.load_historical_htf_candle(candle_tuple)
                    except Exception:
                        pass
        except asyncio.CancelledError:
            break
        except Exception:
            await asyncio.sleep(60)

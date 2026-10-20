import argparse
import asyncio
import json
import os
import signal
import sys
import time
from typing import Dict, List, Optional, Sequence
import urllib.parse

import aiohttp
import websockets

from microstructure import SymbolNormalizer, get_whale_thresholds
from vault import AsyncVaultWriter, MarketDataVault

DEFAULT_COLLECTOR_SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "XRPUSDT",
    "BNBUSDT",
    "SUIUSDT",
]


class MarketDataCollector:
    """Asynchronous Live Market Data Recording Daemon.

    Continuously ingests and persists:
    1. Institutional Whale Orders (@aggTrade)
    2. Forced Margin Liquidations (!forceOrder@arr)
    3. Order Book Top-of-Book BBO Spreads & Imbalance (@bookTicker)
    4. Enriched 1m & 15m Candlestick Bars with Taker Net Delta (@kline)
    5. Open Interest, Funding Rates, & Basis Spreads (fapi REST)
    6. Top Trader Long/Short Sentiment Ratios (futures data API)
    """

    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        db_path: str = "data/market_vault.db",
        flush_interval_sec: float = 1.0,
        isp_friendly: bool = True,
    ):
        self.symbols = [s.strip().upper() for s in (symbols or DEFAULT_COLLECTOR_SYMBOLS) if s.strip()]
        self.futures_symbols = [SymbolNormalizer.to_futures_symbol(s) for s in self.symbols]
        self.futures_to_spot = {SymbolNormalizer.to_futures_symbol(s): s for s in self.symbols}
        self.db_path = db_path
        self.isp_friendly = isp_friendly
        self.vault = MarketDataVault(db_path=self.db_path)
        self.writer = AsyncVaultWriter(self.vault, flush_interval_sec=flush_interval_sec, batch_size=1000)
        self._running = False

    async def start(self) -> None:
        """Start all WebSocket listeners and REST poller tasks."""
        self._running = True
        self.writer.start()

        print("=" * 115)
        mode_str = "ISP-FRIENDLY (< 300 MB/day via REST 10s BBO)" if self.isp_friendly else "UNTHROTTLED (~6 GB/day via WebSocket BBO)"
        print(f" [MARKET DATA COLLECTOR] INITIALIZING LIVE TELEMETRY LAKE")
        print(f" DATABASE VAULT:       {self.db_path}")
        print(f" MONITORED UNIVERSE:   {', '.join(self.symbols)} ({len(self.symbols)} assets)")
        print(f" BANDWIDTH PROFILE:    {mode_str}")
        print(f" CAPTURED TIERS:       Whales, Liquidations, BBO Spread, OI, Funding, Top Trader Sentiment, Net Delta")
        print("=" * 115 + "\n")

        tasks = [
            asyncio.create_task(self._run_futures_market_ws()),
            asyncio.create_task(self._run_klines_and_bbo_ws()),
            asyncio.create_task(self._poll_derivatives_rest()),
            asyncio.create_task(self._poll_sentiment_rest()),
            asyncio.create_task(self._heartbeat_logger()),
        ]
        if self.isp_friendly:
            tasks.append(asyncio.create_task(self._poll_bbo_rest()))

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            print("\n[COLLECTOR] Shutdown signal received. Draining telemetry queues...")
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Stop writer and close connections cleanly."""
        self._running = False
        await self.writer.stop()
        summary = self.vault.get_vault_summary()
        print("\n" + "=" * 115)
        print(" [COLLECTOR SHUTDOWN] Telemetry Lake safely synced to disk.")
        print(f" VAULT SIZE:           {summary['db_size_mb']} MB")
        for table, count in summary["row_counts"].items():
            print(f"  - {table:<28}: {count:,} records stored")
        print("=" * 115 + "\n")

    # ----------------------------------------------------
    # WEBSOCKET STREAM 1: FUTURES AGGTRADE & FORCEORDER
    # ----------------------------------------------------

    async def _run_futures_market_ws(self) -> None:
        """Stream aggTrade and !forceOrder@arr from Binance Futures."""
        stream_names = [f"{sym.lower()}@aggTrade" for sym in self.futures_symbols]
        stream_names.append("!forceOrder@arr")
        ws_url = f"wss://fstream.binance.com/market/stream?streams={'/'.join(stream_names)}"
        backoff = 3

        while self._running:
            try:
                print("[Futures WS] Connecting to aggTrade & forceOrder streams...")
                async with websockets.connect(ws_url, ping_interval=20, ping_timeout=20) as ws:
                    print("[Futures WS] Connected successfully. Capturing institutional order flow.\n")
                    backoff = 3
                    async for message in ws:
                        if not self._running:
                            break
                        payload = json.loads(message)
                        data = payload.get("data", payload)
                        event_type = data.get("e")

                        if event_type == "aggTrade":
                            f_sym = str(data.get("s", "")).upper()
                            spot_sym = self.futures_to_spot.get(f_sym, f_sym)
                            price = float(data.get("p", 0.0))
                            qty = float(data.get("q", 0.0))
                            notional = price * qty

                            # Scaled threshold evaluation
                            dolphin, shark, whale = get_whale_thresholds(spot_sym)
                            if notional >= dolphin:
                                badge = "WHALE" if notional >= whale else ("SHARK" if notional >= shark else "DOLPHIN")
                                side = "SELL" if data.get("m") is True else "BUY"
                                ts_ms = int(data.get("T", time.time() * 1000))
                                trade_id = int(data.get("a", 0))

                                self.writer.record_event(
                                    "whale_trades",
                                    {
                                        "symbol": spot_sym,
                                        "futures_symbol": f_sym,
                                        "timestamp_ms": ts_ms,
                                        "price": price,
                                        "quantity": qty,
                                        "notional": notional,
                                        "side": side,
                                        "badge": badge,
                                        "is_buyer_maker": bool(data.get("m", False)),
                                        "trade_id": trade_id,
                                    },
                                )

                        elif event_type == "forceOrder":
                            o = data.get("o", {})
                            f_sym = str(o.get("s", "")).upper()
                            spot_sym = self.futures_to_spot.get(f_sym, f_sym)
                            pos_side = "LONG" if o.get("S") == "SELL" else "SHORT"
                            price = float(o.get("p", 0.0))
                            qty = float(o.get("q", 0.0))
                            notional = price * qty
                            ts_ms = int(o.get("T", time.time() * 1000))

                            self.writer.record_event(
                                "liquidation_events",
                                {
                                    "symbol": spot_sym,
                                    "futures_symbol": f_sym,
                                    "timestamp_ms": ts_ms,
                                    "liquidated_position": pos_side,
                                    "price": price,
                                    "quantity": qty,
                                    "notional": notional,
                                    "order_type": str(o.get("o", "LIMIT")),
                                    "status": str(o.get("X", "FILLED")),
                                },
                            )

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[Futures WS Error] {e}. Reconnecting in {backoff}s...")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    # ----------------------------------------------------
    # WEBSOCKET STREAM 2: KLINES & BOOK TICKER (BBO)
    # ----------------------------------------------------

    async def _run_klines_and_bbo_ws(self) -> None:
        """Stream real-time 1m/15m klines (and optional BBO bookTicker) from Binance Spot."""
        streams = []
        for s in self.symbols:
            low_s = s.lower()
            if not self.isp_friendly:
                streams.append(f"{low_s}@bookTicker")
            streams.append(f"{low_s}@kline_1m")
            streams.append(f"{low_s}@kline_15m")

        ws_url = f"wss://stream.binance.com:9443/stream?streams={'/'.join(streams)}"
        backoff = 3

        while self._running:
            try:
                stream_desc = "klines & bookTicker" if not self.isp_friendly else "klines (1m/15m)"
                print(f"[Spot WS] Connecting to {stream_desc} streams...")
                async with websockets.connect(ws_url, ping_interval=20, ping_timeout=20) as ws:
                    print(f"[Spot WS] Connected successfully. Capturing {stream_desc}.\n")
                    backoff = 3
                    async for message in ws:
                        if not self._running:
                            break
                        payload = json.loads(message)
                        data = payload.get("data", payload)
                        stream_name = payload.get("stream", "")

                        if "bookTicker" in stream_name:
                            sym = str(data.get("s", "")).upper()
                            bid_p = float(data.get("b", 0.0))
                            bid_q = float(data.get("B", 0.0))
                            ask_p = float(data.get("a", 0.0))
                            ask_q = float(data.get("A", 0.0))

                            spread = ask_p - bid_p
                            mid_p = (ask_p + bid_p) / 2.0 if (ask_p + bid_p) > 0 else 1.0
                            spread_bps = (spread / mid_p) * 10000.0
                            tot_q = bid_q + ask_q
                            imbalance = (bid_q - ask_q) / tot_q if tot_q > 0 else 0.0

                            self.writer.record_event(
                                "order_book_bbo_snapshots",
                                {
                                    "symbol": sym,
                                    "timestamp_ms": int(time.time() * 1000),
                                    "best_bid_price": bid_p,
                                    "best_bid_qty": bid_q,
                                    "best_ask_price": ask_p,
                                    "best_ask_qty": ask_q,
                                    "spread": spread,
                                    "spread_bps": spread_bps,
                                    "book_imbalance": imbalance,
                                },
                            )

                        elif "kline" in stream_name:
                            k = data.get("k", {})
                            if k.get("x"):  # Only record closed bars
                                sym = str(k.get("s", "")).upper()
                                tf = str(k.get("i", "1m"))
                                open_t = int(k.get("t", 0))
                                close_t = int(k.get("T", 0))
                                o = float(k.get("o", 0.0))
                                h = float(k.get("h", 0.0))
                                l = float(k.get("l", 0.0))
                                c = float(k.get("c", 0.0))
                                vol = float(k.get("v", 0.0))
                                turnover = float(k.get("q", 0.0))
                                trades = int(k.get("n", 0))
                                taker_buy = float(k.get("V", 0.0))
                                taker_sell = max(0.0, vol - taker_buy)
                                net_delta = taker_buy - taker_sell
                                delta_ratio = (net_delta / vol) if vol > 0 else 0.0

                                self.writer.record_event(
                                    "candle_microstructure_bars",
                                    {
                                        "symbol": sym,
                                        "timeframe": tf,
                                        "open_time": open_t,
                                        "close_time": close_t,
                                        "open": o,
                                        "high": h,
                                        "low": l,
                                        "close": c,
                                        "volume": vol,
                                        "turnover": turnover,
                                        "trade_count": trades,
                                        "taker_buy_volume": taker_buy,
                                        "taker_sell_volume": taker_sell,
                                        "net_volume_delta": net_delta,
                                        "delta_ratio": delta_ratio,
                                    },
                                )

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[Spot WS Error] {e}. Reconnecting in {backoff}s...")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    # ----------------------------------------------------
    # REST POLLER: BBO ORDER BOOK SPREADS (ISP-FRIENDLY)
    # ----------------------------------------------------

    async def _poll_bbo_rest(self) -> None:
        """Poll Order Book BBO spreads every 10 seconds via REST to conserve bandwidth."""
        sym_json = json.dumps(self.symbols)
        encoded_symbols = urllib.parse.quote(sym_json)
        url = f"https://api.binance.com/api/v3/ticker/bookTicker?symbols={encoded_symbols}"
        connector = aiohttp.TCPConnector(limit=2)
        timeout = aiohttp.ClientTimeout(total=5)

        while self._running:
            try:
                now_ms = int(time.time() * 1000)
                async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
                    async with session.get(url) as resp:
                        if resp.status == 200:
                            items = await resp.json()
                            for item in items:
                                sym = str(item.get("symbol", "")).upper()
                                bid_p = float(item.get("bidPrice", 0.0))
                                bid_q = float(item.get("bidQty", 0.0))
                                ask_p = float(item.get("askPrice", 0.0))
                                ask_q = float(item.get("askQty", 0.0))

                                spread = ask_p - bid_p
                                mid_p = (ask_p + bid_p) / 2.0 if (ask_p + bid_p) > 0 else 1.0
                                spread_bps = (spread / mid_p) * 10000.0
                                tot_q = bid_q + ask_q
                                imbalance = (bid_q - ask_q) / tot_q if tot_q > 0 else 0.0

                                self.writer.record_event(
                                    "order_book_bbo_snapshots",
                                    {
                                        "symbol": sym,
                                        "timestamp_ms": now_ms,
                                        "best_bid_price": bid_p,
                                        "best_bid_qty": bid_q,
                                        "best_ask_price": ask_p,
                                        "best_ask_qty": ask_q,
                                        "spread": spread,
                                        "spread_bps": spread_bps,
                                        "book_imbalance": imbalance,
                                    },
                                )
            except asyncio.CancelledError:
                break
            except Exception:
                pass

            await asyncio.sleep(10)

    # ----------------------------------------------------
    # REST POLLER 1: OPEN INTEREST, PREMIUM, & BASIS
    # ----------------------------------------------------

    async def _poll_derivatives_rest(self) -> None:
        """Poll Open Interest and Premium Index every 15 seconds."""
        connector = aiohttp.TCPConnector(limit=5)
        timeout = aiohttp.ClientTimeout(total=5)

        while self._running:
            try:
                now_ms = int(time.time() * 1000)
                async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
                    for f_sym in self.futures_symbols:
                        spot_sym = self.futures_to_spot.get(f_sym, f_sym)
                        try:
                            # 1. Open Interest
                            oi_url = f"https://fapi.binance.com/fapi/v1/openInterest?symbol={f_sym}"
                            async with session.get(oi_url) as resp:
                                if resp.status == 200:
                                    oi_data = await resp.json()
                                    oi_val = float(oi_data.get("openInterest", 0.0))

                                    # 2. Premium Index & Funding
                                    p_url = f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={f_sym}"
                                    async with session.get(p_url) as p_resp:
                                        if p_resp.status == 200:
                                            p_data = await p_resp.json()
                                            mark_p = float(p_data.get("markPrice", 0.0))
                                            index_p = float(p_data.get("indexPrice", 0.0))
                                            funding_r = float(p_data.get("lastFundingRate", 0.0))
                                            next_t = int(p_data.get("nextFundingTime", 0))

                                            basis_bps = ((mark_p - index_p) / index_p) * 10000.0 if index_p > 0 else 0.0
                                            funding_apr = funding_r * 3.0 * 365.0 * 100.0
                                            oi_val_usd = oi_val * mark_p

                                            self.writer.record_event(
                                                "derivatives_snapshots",
                                                {
                                                    "symbol": spot_sym,
                                                    "timestamp_ms": now_ms,
                                                    "mark_price": mark_p,
                                                    "index_price": index_p,
                                                    "basis_spread_bps": basis_bps,
                                                    "open_interest": oi_val,
                                                    "open_interest_value": oi_val_usd,
                                                    "last_funding_rate": funding_r,
                                                    "funding_apr_pct": funding_apr,
                                                    "next_funding_time_ms": next_t,
                                                },
                                            )
                        except Exception:
                            pass
            except asyncio.CancelledError:
                break
            except Exception:
                pass

            await asyncio.sleep(15)

    # ----------------------------------------------------
    # REST POLLER 2: TOP TRADER SENTIMENT RATIOS
    # ----------------------------------------------------

    async def _poll_sentiment_rest(self) -> None:
        """Poll top trader long/short account and position ratios every 5 minutes."""
        connector = aiohttp.TCPConnector(limit=5)
        timeout = aiohttp.ClientTimeout(total=10)

        while self._running:
            try:
                now_ms = int(time.time() * 1000)
                async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
                    for f_sym in self.futures_symbols:
                        spot_sym = self.futures_to_spot.get(f_sym, f_sym)
                        try:
                            # Top Trader Long/Short Account Ratio
                            url_acc = f"https://fapi.binance.com/futures/data/topLongShortAccountRatio?symbol={f_sym}&period=5m&limit=1"
                            url_pos = f"https://fapi.binance.com/futures/data/topLongShortPositionRatio?symbol={f_sym}&period=5m&limit=1"
                            url_glob = f"https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol={f_sym}&period=5m&limit=1"
                            url_taker = f"https://fapi.binance.com/futures/data/takerlongshortRatio?symbol={f_sym}&period=5m&limit=1"

                            acc_r = 1.0
                            pos_r = 1.0
                            glob_r = 1.0
                            taker_r = 1.0

                            async with session.get(url_acc) as r1:
                                if r1.status == 200:
                                    d1 = await r1.json()
                                    if d1:
                                        acc_r = float(d1[0].get("longShortRatio", 1.0))

                            async with session.get(url_pos) as r2:
                                if r2.status == 200:
                                    d2 = await r2.json()
                                    if d2:
                                        pos_r = float(d2[0].get("longShortRatio", 1.0))

                            async with session.get(url_glob) as r3:
                                if r3.status == 200:
                                    d3 = await r3.json()
                                    if d3:
                                        glob_r = float(d3[0].get("longShortRatio", 1.0))

                            async with session.get(url_taker) as r4:
                                if r4.status == 200:
                                    d4 = await r4.json()
                                    if d4:
                                        taker_r = float(d4[0].get("buySellRatio", 1.0))

                            self.writer.record_event(
                                "sentiment_metrics",
                                {
                                    "symbol": spot_sym,
                                    "timestamp_ms": now_ms,
                                    "top_trader_long_short_account_ratio": acc_r,
                                    "top_trader_long_short_position_ratio": pos_r,
                                    "global_long_short_account_ratio": glob_r,
                                    "taker_buy_sell_volume_ratio": taker_r,
                                },
                            )
                        except Exception:
                            pass
            except asyncio.CancelledError:
                break
            except Exception:
                pass

            await asyncio.sleep(300)

    # ----------------------------------------------------
    # HEARTBEAT & LOGGING
    # ----------------------------------------------------

    async def _heartbeat_logger(self) -> None:
        """Log periodic ingestion heartbeat every 60 seconds."""
        while self._running:
            await asyncio.sleep(60)
            summary = self.vault.get_vault_summary()
            total_events = sum(summary["row_counts"].values())
            print(
                f"[VAULT HEARTBEAT] Total Telemetry Events: {total_events:,} | "
                f"Vault Size: {summary['db_size_mb']} MB | "
                f"Whale Trades: {summary['row_counts']['whale_trades']:,} | "
                f"Liquidations: {summary['row_counts']['liquidation_events']:,} | "
                f"BBO: {summary['row_counts']['order_book_bbo_snapshots']:,}"
            )


def main():
    parser = argparse.ArgumentParser(description="Institutional Binance Live Telemetry Collector")
    parser.add_argument("--symbols", type=str, default=",".join(DEFAULT_COLLECTOR_SYMBOLS), help="Comma-separated symbols to record")
    parser.add_argument("--vault", type=str, default="data/market_vault.db", help="Path to SQLite market vault file")
    parser.add_argument("--unthrottled", action="store_true", help="Enable raw tick-level WebSocket BBO streaming (~6 GB/day). Defaults to False (ISP-Friendly REST mode < 300 MB/day).")
    args = parser.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    collector = MarketDataCollector(symbols=symbols, db_path=args.vault, isp_friendly=not args.unthrottled)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def handle_signal():
        print("\n[COLLECTOR] Caught interrupt. Exiting safely...")
        for task in asyncio.all_tasks(loop):
            task.cancel()

    try:
        loop.run_until_complete(collector.start())
    except KeyboardInterrupt:
        handle_signal()
    finally:
        loop.close()


if __name__ == "__main__":
    main()

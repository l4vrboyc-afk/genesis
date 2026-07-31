"""
Data Fetcher — Retrieves and processes market data from MT5.
Handles candle data, tick data, and indicator calculations.
"""

import MetaTrader5 as mt5
import pandas as pd
import pandas_ta_classic as ta
import numpy as np
from datetime import datetime
from typing import Optional
from loguru import logger
from bot.config.settings import settings, TIMEFRAME_MAP


class DataFetcher:
    """Fetches and processes market data from MetaTrader 5."""

    # Number of recent bars used for the rolling linear-regression slope of the
    # trend EMA. Larger windows stabilize the slope at the cost of lag; 20 is a
    # good trade-off for H4 trend confirmation.
    EMA_SLOPE_WINDOW = 20

    def __init__(self, connector):
        """
        Args:
            connector: MT5Connector instance for ensuring connectivity.
        """
        self.connector = connector

        # Serialised MT5 executor -- injected by ``TradingOrchestrator.start``
        # immediately after the worker thread is alive. Public ``async``
        # methods on this class route every native ``mt5.copy_rates_*`` and
        # ``mt5.symbol_info_tick`` call through ``self._executor.submit(...)``
        # so the broker IPC is serialised on a single worker thread.
        self._executor = None

    def attach_executor(self, executor) -> None:
        """Inject the serialised MT5 executor.

        Called once from ``TradingOrchestrator.start`` AFTER
        ``await executor.start()``.
        """
        self._executor = executor

    # ── Indicator Internals ─────────────────────────────────────────

    @staticmethod
    def _rolling_slope(series: pd.Series, window: int = 20) -> np.ndarray:
        """
        Pure-numpy rolling linear-regression slope of `series` over `window` bars.

        For each bar i, fits y = m*x + b to the most recent `window` points
        (x = 0..window-1, y = series[i-window+1 : i+1]) and returns m.

        Why this beats `series.diff(n)/n`:
            A two-point diff conflates the magnitude of price with curvature and
            noise. A fitted slope averages over `window` bars, so a steady trend
            registers a slope proportional to its true per-bar rate and random
            noise largely cancels.

        Why a Python loop + numpy instead of `.rolling().apply()`:
            `pd.Series.rolling(...).apply(...)` falls back to a Python-row-at-a-time
            apply and is ~50-100x slower on frames of a few thousand rows. The
            numpy-vectorized inner product keeps it fast even at 10k bars.

        Slope units: price-units per bar. Callers that need a scale-invariant
        value must divide by ATR or another volatility scalar (see
        SmartTrendStrategy._calculate_confidence).

        Args:
            series: Numeric series (e.g. EMA values, price).
            window: Lookback window length.

        Returns:
            np.ndarray of slope per bar; entries where the window is
            incomplete are NaN.
        """
        arr = np.asarray(series, dtype=float)
        n = len(arr)
        out = np.full(n, np.nan)

        # Pre-compute the centered x and its self-dot once; for a fixed window
        # this is a constant independent of window position.
        x = np.arange(window, dtype=float)
        xm = x - x.mean()
        xm_sq_sum = (xm ** 2).sum()

        for i in range(window - 1, n):
            y = arr[i - window + 1: i + 1]
            # slope = ((x - x.mean()) · (y - y.mean())) / ((x - x.mean()) ** 2).sum()
            ym = y - y.mean()
            out[i] = (xm * ym).sum() / xm_sq_sum

        return out


    # ── Candle Data ─────────────────────────────────────────────────

    async def get_candles(
        self,
        symbol: str,
        timeframe: str,
        count: int = 500,
        from_date: Optional[datetime] = None,
    ) -> Optional[pd.DataFrame]:
        """
        Fetch OHLCV candle data from MT5. Async — runs ``mt5.copy_rates_*``
        on the executor's worker thread.

        Args:
            symbol: Trading pair (e.g., "EURUSD")
            timeframe: Timeframe string (e.g., "M15", "H4")
            count: Number of candles to fetch
            from_date: Start date (if None, fetches most recent candles)

        Returns:
            DataFrame with columns: time, open, high, low, close, volume, spread
        """
        if not await self.connector.ensure_connected():
            return None

        return await self._executor.submit(
            self._get_candles_sync, symbol, timeframe, count, from_date
        )

    def _get_candles_sync(
        self,
        symbol: str,
        timeframe: str,
        count: int = 500,
        from_date: Optional[datetime] = None,
    ) -> Optional[pd.DataFrame]:
        """Sync body for ``get_candles`` — runs on the executor's worker thread."""
        tf = TIMEFRAME_MAP.get(timeframe)
        if tf is None:
            logger.error(f"❌ Invalid timeframe: {timeframe}")
            return None

        try:
            if from_date:
                rates = mt5.copy_rates_from(symbol, tf, from_date, count)
            else:
                rates = mt5.copy_rates_from_pos(symbol, tf, 0, count)

            if rates is None or len(rates) == 0:
                logger.warning(f"⚠️ No candle data returned for {symbol} {timeframe}")
                return None

            df = pd.DataFrame(rates)
            df["time"] = pd.to_datetime(df["time"], unit="s")
            df.set_index("time", inplace=True)
            df.rename(columns={"tick_volume": "volume"}, inplace=True)

            return df

        except Exception as e:
            logger.error(f"❌ Error fetching candles for {symbol}: {e}")
            return None

    async def get_current_price(self, symbol: str) -> Optional[dict]:
        """
        Get the current bid/ask price for a symbol. Async — runs
        ``mt5.symbol_info_tick`` on the executor's worker thread.

        Returns:
            dict with 'bid', 'ask', 'spread', 'time'
        """
        if not await self.connector.ensure_connected():
            return None

        tick = await self._executor.submit(self._get_price_sync, symbol)
        if tick is None:
            logger.warning(f"⚠️ Failed to get tick for {symbol}")
            return None

        return {
            "bid": tick.bid,
            "ask": tick.ask,
            "spread": round(tick.ask - tick.bid, 6),
            "time": datetime.fromtimestamp(tick.time),
            "last": tick.last,
            "volume": tick.volume,
        }

    def _get_price_sync(self, symbol: str):
        """Sync body for ``get_current_price`` — runs on the executor's worker thread."""
        return mt5.symbol_info_tick(symbol)

    # ── Indicator Calculations ──────────────────────────────────────

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Calculate all required technical indicators on a candle DataFrame.

        Adds columns:
            ema_{period}      — EMA used as trend filter
            rsi_{period}      — RSI momentum
            adx, plus_di, minus_di — ADX trend strength and DI components
            atr_{period}      — ATR for stop-loss / position sizing
            atr_avg           — 50-bar mean of ATR (spike baseline)
            atr_ratio         — ATR / atr_avg (volatility-spike detector)
            ema_slope         — Linear-regression slope of ema_{period} over
                                `EMA_SLOPE_WINDOW` bars (price-units per bar;
                                positive = rising EMA, negative = falling).
            volume_avg_20     — 20-bar mean of bar tick-volume (the scalper
                                uses this for its 1.5× surge confirmation).

        Args:
            df: DataFrame with OHLCV data

        Returns:
            DataFrame with indicator columns added
        """
        if df is None or df.empty:
            return df

        df = df.copy()

        # EMA (Trend Filter)
        ema_vals = ta.ema(df["close"], length=settings.ema_period)
        df[f"ema_{settings.ema_period}"] = ema_vals if ema_vals is not None else pd.Series(np.nan, index=df.index)

        # RSI (Momentum)
        rsi_vals = ta.rsi(df["close"], length=settings.rsi_period)
        df[f"rsi_{settings.rsi_period}"] = rsi_vals if rsi_vals is not None else pd.Series(np.nan, index=df.index)

        # ADX (Trend Strength) — returns DataFrame with ADX, DMP, DMN
        adx_df = ta.adx(df["high"], df["low"], df["close"], length=settings.adx_period)
        if adx_df is not None:
            df["adx"] = adx_df[f"ADX_{settings.adx_period}"]
            df["plus_di"] = adx_df[f"DMP_{settings.adx_period}"]
            df["minus_di"] = adx_df[f"DMN_{settings.adx_period}"]
        else:
            df["adx"] = np.nan
            df["plus_di"] = np.nan
            df["minus_di"] = np.nan

        # ATR (Volatility / Stop Loss sizing)
        atr_vals = ta.atr(df["high"], df["low"], df["close"], length=settings.atr_period)
        df[f"atr_{settings.atr_period}"] = atr_vals if atr_vals is not None else pd.Series(np.nan, index=df.index)

        # Average ATR for volatility spike detection
        atr_col = f"atr_{settings.atr_period}"
        df["atr_avg"] = df[atr_col].rolling(window=50).mean()
        df["atr_ratio"] = df[atr_col] / df["atr_avg"]

        # EMA slope (for trend direction confirmation).
        # Linear-regression slope of the trend EMA over `EMA_SLOPE_WINDOW` bars;
        # units = price-units per bar. See `DataFetcher._rolling_slope` docstring
        # for why we use a fitted slope instead of a two-point diff.
        ema_col = f"ema_{settings.ema_period}"
        df["ema_slope"] = self._rolling_slope(df[ema_col], window=self.EMA_SLOPE_WINDOW)

        # Volume baseline for the scalper's tick-volume confirmation gate.
        # The scalper compares the latest bar's tick count against this
        # 20-bar mean and only fires when volume surges past ``1.5×``
        # baseline — the "no-trade-in-thin-book" filter the strategy
        # description originally promised but the v1 RSI-only
        # implementation skipped. ``min_periods=1`` lets the first 20
        # bars still produce a value; rolling NaNs in the trailing window
        # are treated by the scalper as "no signal".
        df["volume_avg_20"] = df["volume"].rolling(window=20, min_periods=1).mean()

        return df

    # ── Smart Money Concepts ────────────────────────────────────────

    def detect_order_blocks(
        self, df: pd.DataFrame, lookback: int = 20
    ) -> pd.DataFrame:
        """
        Detect Order Blocks (OB) — institutional supply/demand zones.

        An Order Block is the last bearish candle before a strong bullish move (bullish OB)
        or the last bullish candle before a strong bearish move (bearish OB).

        Args:
            df: DataFrame with OHLCV data
            lookback: Number of candles to look back for OB detection

        Returns:
            DataFrame with 'ob_bullish' and 'ob_bearish' columns
        """
        df = df.copy()
        df["ob_bullish"] = False
        df["ob_bearish"] = False
        df["ob_bull_high"] = np.nan
        df["ob_bull_low"] = np.nan
        df["ob_bear_high"] = np.nan
        df["ob_bear_low"] = np.nan

        closes = df["close"].values
        opens = df["open"].values
        highs = df["high"].values
        lows = df["low"].values

        atr_col = f"atr_{settings.atr_period}"
        if atr_col not in df.columns:
            return df
        atrs = df[atr_col].values

        for i in range(3, min(len(df), lookback + 3)):
            # Skip if ATR is NaN
            if np.isnan(atrs[i]):
                continue

            # Bullish OB: bearish candle followed by strong bullish move
            if (
                closes[i - 2] < opens[i - 2]  # Bearish candle (the OB)
                and closes[i - 1] > opens[i - 1]  # Next candle is bullish
                and (closes[i - 1] - opens[i - 1]) > atrs[i] * 1.0  # Strong move
            ):
                idx = df.index[i - 2]
                df.loc[idx, "ob_bullish"] = True
                df.loc[idx, "ob_bull_high"] = highs[i - 2]
                df.loc[idx, "ob_bull_low"] = lows[i - 2]

            # Bearish OB: bullish candle followed by strong bearish move
            if (
                closes[i - 2] > opens[i - 2]  # Bullish candle (the OB)
                and closes[i - 1] < opens[i - 1]  # Next candle is bearish
                and (opens[i - 1] - closes[i - 1]) > atrs[i] * 1.0  # Strong move
            ):
                idx = df.index[i - 2]
                df.loc[idx, "ob_bearish"] = True
                df.loc[idx, "ob_bear_high"] = highs[i - 2]
                df.loc[idx, "ob_bear_low"] = lows[i - 2]

        return df

    def detect_fair_value_gaps(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Detect Fair Value Gaps (FVG) — imbalances in price action.

        A bullish FVG: candle[i-2].high < candle[i].low (gap up)
        A bearish FVG: candle[i-2].low > candle[i].high (gap down)

        Args:
            df: DataFrame with OHLCV data

        Returns:
            DataFrame with FVG columns added
        """
        df = df.copy()
        df["fvg_bullish"] = False
        df["fvg_bearish"] = False
        df["fvg_bull_top"] = np.nan
        df["fvg_bull_bottom"] = np.nan
        df["fvg_bear_top"] = np.nan
        df["fvg_bear_bottom"] = np.nan

        highs = df["high"].values
        lows = df["low"].values

        for i in range(2, len(df)):
            # Bullish FVG — gap between candle[i-2].high and candle[i].low
            if highs[i - 2] < lows[i]:
                idx = df.index[i - 1]  # Mark the middle candle
                df.loc[idx, "fvg_bullish"] = True
                df.loc[idx, "fvg_bull_top"] = lows[i]
                df.loc[idx, "fvg_bull_bottom"] = highs[i - 2]

            # Bearish FVG — gap between candle[i-2].low and candle[i].high
            if lows[i - 2] > highs[i]:
                idx = df.index[i - 1]
                df.loc[idx, "fvg_bearish"] = True
                df.loc[idx, "fvg_bear_top"] = lows[i - 2]
                df.loc[idx, "fvg_bear_bottom"] = highs[i]

        return df

    # ── Market Structure ────────────────────────────────────────────

    def get_support_resistance(
        self, df: pd.DataFrame, window: int = 20
    ) -> dict:
        """
        Calculate dynamic support and resistance levels using swing highs/lows.

        Args:
            df: DataFrame with OHLCV data
            window: Lookback window for swing detection

        Returns:
            dict with 'support' and 'resistance' levels (list of floats)
        """
        highs = df["high"].values
        lows = df["low"].values
        supports = []
        resistances = []

        for i in range(window, len(df) - window):
            # Swing high
            if highs[i] == max(highs[i - window: i + window + 1]):
                resistances.append(highs[i])

            # Swing low
            if lows[i] == min(lows[i - window: i + window + 1]):
                supports.append(lows[i])

        # Keep only most recent levels
        return {
            "support": sorted(supports[-5:]) if supports else [],
            "resistance": sorted(resistances[-5:], reverse=True) if resistances else [],
        }

    def detect_sweeps(
        self,
        df: pd.DataFrame,
        atr_multiplier: float = 1.0,
        lookback: int = 60,
    ) -> list[dict]:
        """Detect liquidity sweeps (SSL / BSL) on OHLC data.

        Scans for swing highs/lows (neighbour window=2, same pattern as
        the trailing engine's ``_detect_swings``), then checks whether the
        *next* candle took liquidity beyond the swing by ≥ ``atr_multiplier``
        × ATR and rejected back inside:

        - **Sellside sweep (SSL):** wick above swing high, close below it
          → liquidity taken above, bullish rejection.
        - **Buyside sweep (BSL):** wick below swing low, close above it
          → liquidity taken below, bearish rejection.

        Args:
            df: OHLCV DataFrame (preferably with an ``atr_*`` column from
                ``calculate_indicators``; ATR is computed inline otherwise).
            atr_multiplier: Minimum wick penetration beyond the swing,
                expressed in ATR units (default 1.0).
            lookback: Only consider the last *lookback* bars for sweeps
                (swing pivots may sit just outside this window).

        Returns:
            List of dicts with keys ``type`` (``"sellside"`` / ``"buyside"``),
            ``level``, ``time`` (unix epoch int), ``price``, ``label``.
        """
        if df is None or df.empty or len(df) < 5:
            return []

        work = df.copy()
        atr_col = f"atr_{settings.atr_period}"
        if atr_col not in work.columns:
            atr_vals = ta.atr(
                work["high"], work["low"], work["close"], length=settings.atr_period
            )
            work[atr_col] = (
                atr_vals if atr_vals is not None
                else pd.Series(np.nan, index=work.index)
            )

        highs = work["high"].values
        lows = work["low"].values
        closes = work["close"].values
        atrs = work[atr_col].values
        n = len(work)
        window = 2

        # Collect swing pivots with their bar indices (window=2)
        swing_highs: list[tuple[int, float]] = []
        swing_lows: list[tuple[int, float]] = []
        if n >= window * 2 + 1:
            for i in range(window, n - window):
                is_high = all(
                    highs[i] > highs[i - j] for j in range(1, window + 1)
                ) and all(
                    highs[i] > highs[i + j] for j in range(1, window + 1)
                )
                if is_high:
                    swing_highs.append((i, float(highs[i])))

                is_low = all(
                    lows[i] < lows[i - j] for j in range(1, window + 1)
                ) and all(
                    lows[i] < lows[i + j] for j in range(1, window + 1)
                )
                if is_low:
                    swing_lows.append((i, float(lows[i])))

        # Restrict confirmation candles to the lookback window
        start_i = max(0, n - lookback)
        sweeps: list[dict] = []

        def _bar_time(idx: int) -> int:
            ts = work.index[idx]
            try:
                if hasattr(ts, "timestamp"):
                    return int(ts.timestamp())
                return int(pd.Timestamp(ts).timestamp())
            except Exception:
                return int(datetime.now().timestamp())

        # Sellside (SSL): next candle wicks ≥1×ATR above swing high, closes below
        for swing_i, level in swing_highs:
            conf_i = swing_i + 1
            if conf_i < start_i or conf_i >= n:
                continue
            atr = atrs[conf_i]
            if atr is None or (isinstance(atr, float) and np.isnan(atr)) or atr <= 0:
                continue
            if highs[conf_i] >= level + atr_multiplier * atr and closes[conf_i] < level:
                sweeps.append({
                    "type": "sellside",
                    "level": level,
                    "time": _bar_time(conf_i),
                    "price": float(highs[conf_i]),
                    "label": "SSL Sweep",
                })

        # Buyside (BSL): next candle wicks ≥1×ATR below swing low, closes above
        for swing_i, level in swing_lows:
            conf_i = swing_i + 1
            if conf_i < start_i or conf_i >= n:
                continue
            atr = atrs[conf_i]
            if atr is None or (isinstance(atr, float) and np.isnan(atr)) or atr <= 0:
                continue
            if lows[conf_i] <= level - atr_multiplier * atr and closes[conf_i] > level:
                sweeps.append({
                    "type": "buyside",
                    "level": level,
                    "time": _bar_time(conf_i),
                    "price": float(lows[conf_i]),
                    "label": "BSL Sweep",
                })

        return sweeps

    # ── Gatekeeper Indicators (Rule 1 & 2) ──────────────────────────
    # Lightweight dict return — no full-DataFrame or SMC overhead.

    async def get_gatekeeper_indicators(self, symbol: str) -> Optional[dict]:
        """Fetch ADX, 50-EMA, ATR, and close price on the gatekeeper
        timeframe for the Gatekeeper Trend Guard (Rule 1) and Minimum
        SL Floor (Rule 2).

        The timeframe is profile-aware (configured via
        ``settings.gatekeeper_timeframe``, default H1):

        - **Scalper profile:** M15 — filters short-term pullbacks fast
          without waiting 60 minutes for an H1 candle to close.
        - **Default / Breakout / Day Trader:** H1 — intermediate trend
          direction check.

        Only computes the three needed indicators (ADX, EMA50, ATR) —
        no EMA200, RSI, volume avg, SMC, or ema_slope.

        Returns:
            Dict with keys ``adx``, ``ema_50``, ``atr``, ``close``,
            or None on failure / insufficient data.
        """
        if not await self.connector.ensure_connected():
            return None
        return await self._executor.submit(
            self._get_gatekeeper_indicators_sync, symbol
        )

    def _get_gatekeeper_indicators_sync(self, symbol: str) -> Optional[dict]:
        """Sync body — fetches candles on the profile-aware gatekeeper
        timeframe (H1 for default/breakout/daytrader; M15 for scalper)
        and computes only ADX + EMA50 + ATR."""
        # Need enough bars to warm up ADX (14) + some margin.
        count = max(settings.adx_period + 10, 60)
        df = self._get_candles_sync(
            symbol, settings.gatekeeper_timeframe, count
        )
        if df is None or df.empty:
            return None

        # 1. ADX
        adx_df = ta.adx(
            df["high"], df["low"], df["close"], length=settings.adx_period
        )
        adx_val = None
        if adx_df is not None:
            col = f"ADX_{settings.adx_period}"
            if col in adx_df.columns:
                last = adx_df[col].iloc[-1]
                adx_val = None if pd.isna(last) else float(last)

        # 2. EMA(50)
        ema_vals = ta.ema(df["close"], length=settings.gatekeeper_ema_period)
        ema_50_val = None
        if ema_vals is not None:
            last = ema_vals.iloc[-1]
            ema_50_val = None if pd.isna(last) else float(last)

        # 3. ATR
        atr_vals = ta.atr(
            df["high"], df["low"], df["close"], length=settings.atr_period
        )
        atr_val = None
        if atr_vals is not None:
            last = atr_vals.iloc[-1]
            atr_val = None if (pd.isna(last) or last <= 0) else float(last)

        # 4. Close price
        close_val = float(df["close"].iloc[-1])

        logger.debug(
            f"Gatekeeper indicators for {symbol}: ADX={adx_val}, "
            f"EMA50={ema_50_val}, ATR={atr_val}, close={close_val}"
        )

        if adx_val is None and ema_50_val is None:
            # Both trend indicators missing → unusable
            logger.warning(
                f"Gatekeeper indicators unusable for {symbol}: "
                f"ADX={adx_val}, EMA50={ema_50_val}"
            )
            return None

        return {
            "adx": adx_val,
            "ema_50": ema_50_val,
            "atr": atr_val,
            "close": close_val,
        }

    # ── Lightweight ATR-only Helper ────────────────────────────────

    async def get_atr(
        self, symbol: str, timeframe: str, count: int = 50
    ) -> Optional[float]:
        """Fetch the latest ATR value for *symbol* on *timeframe* — no EMA,
        RSI, ADX, or SMC overhead.

        Used by the breakeven and trailing-stop logic in the orchestrator
        where only volatility magnitude is needed.  Avoids the full indicator
        pipeline (5× fewer pandas operations per call).

        Args:
            symbol: Trading pair.
            timeframe: Timeframe string (e.g. "M15", "H1").
            count: Number of candles to fetch (must be > ATR period + 1).

        Returns:
            Latest ATR value, or None on failure / insufficient data.
        """
        if not await self.connector.ensure_connected():
            return None
        return await self._executor.submit(
            self._get_atr_sync, symbol, timeframe, count
        )

    def _get_atr_sync(
        self, symbol: str, timeframe: str, count: int = 50
    ) -> Optional[float]:
        """Sync body — fetches candles and calculates a single ATR value."""
        df = self._get_candles_sync(symbol, timeframe, count)
        if df is None or df.empty:
            return None

        atr_vals = ta.atr(
            df["high"], df["low"], df["close"], length=settings.atr_period
        )
        if atr_vals is None or atr_vals.empty:
            return None

        latest = atr_vals.iloc[-1]
        if pd.isna(latest) or latest <= 0:
            return None

        return float(latest)

    # ── Full Data Pipeline ──────────────────────────────────────────

    async def get_analyzed_data(
        self, symbol: str, timeframe: str, count: int = 500
    ) -> Optional[pd.DataFrame]:
        """
        Full pipeline: fetch candles + calculate indicators + detect SMC patterns.

        Async — runs the entire pipeline on the executor's worker thread so
        the broker IPC + heavy pandas/numpy work do not touch the event
        loop. Strategies call this directly; the orchestrator no longer
        needs to wrap it in ``asyncio.to_thread``.
        """
        if not await self.connector.ensure_connected():
            return None
        return await self._executor.submit(
            self._get_analyzed_data_inner, symbol, timeframe, count
        )

    def _get_analyzed_data_inner(
        self, symbol: str, timeframe: str, count: int = 500
    ) -> Optional[pd.DataFrame]:
        """Sync body for ``get_analyzed_data`` — runs on the executor's worker thread.

        Heavy lifting (pandas / numpy) happens here too. The executor's
        single worker thread is what serialises MT5 calls; that thread
        doesn't run multiple jobs concurrently, so the indicator math
        effectively runs sequentially. If throughput ever matters more
        than lock discipline, swap the executor for a thread pool.
        """
        df = self._get_candles_sync(symbol, timeframe, count)
        if df is None:
            return None
        df = self.calculate_indicators(df)
        df = self.detect_order_blocks(df)
        df = self.detect_fair_value_gaps(df)
        return df

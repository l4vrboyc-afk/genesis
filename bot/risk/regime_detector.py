"""
Regime Detector — 5-Regime Market Health Evaluator.

Standalone module that classifies incoming H4 market data into one of the
5 core regimes.  Pulled out of ``StrategySelector.detect_regime()`` so that
any module (news filter, dashboard, orchestrator) can evaluate the current
market state without importing the entire strategy engine.

Usage:
    from bot.risk.regime_detector import detect_regime

    regime = detect_regime(df_h4, is_news_forced=False)

Flow:
    1. Top Override: ``is_news_forced=True`` → NEWS_EVENT
    2. ATR Ratio < 0.5x → DEAD (liquidity trap)
    3. ATR Ratio > 2.0x → VOLATILE (explosive noise)
    4. ADX > 25 → TRENDING (directional flow)
    5. Otherwise → RANGING (consolidation)
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from bot.config.enums import MarketRegime


def detect_regime(
    htf_data: pd.DataFrame,
    atr_volatility_spike: float = 2.0,
    adx_trend_threshold: float = 25.0,
    adx_range_threshold: float = 20.0,
    dead_atr_ratio: float = 0.5,
    is_news_forced: bool = False,
) -> MarketRegime:
    """Classify the current H4 market state into one of 5 regimes.

    Parameters
    ----------
    htf_data : pd.DataFrame
        Higher-timeframe (H4) OHLCV data with pre-computed indicators.
        Must contain ``adx`` and ``atr_ratio`` columns (from
        ``DataFetcher.calculate_indicators``).

        ``atr_ratio`` = latest ATR ÷ 50-bar ATR mean. Values > 2.0x
        indicate a volatility spike; values < 0.5x indicate a dead market.

    atr_volatility_spike : float
        Threshold above which ATR ratio triggers VOLATILE. Default 2.0.

    adx_trend_threshold : float
        ADX value above which market is TRENDING. Default 25.0.

    adx_range_threshold : float
        ADX below which market is RANGING. Default 20.0.

    dead_atr_ratio : float
        ATR ratio below which market is DEAD. Default 0.5.

    is_news_forced : bool
        If True, returns NEWS_EVENT immediately (bypasses all market
        analysis). The orchestrator sets this when a Tier-1 news event
        is within the buffer window.

    Returns
    -------
    MarketRegime
        One of TRENDING, RANGING, VOLATILE, DEAD, or NEWS_EVENT.
    """
    # ── 0. Top Override: News Guardrail ────────────────────────────
    if is_news_forced:
        return MarketRegime.NEWS_EVENT

    # ── 1. Data sufficiency ────────────────────────────────────────
    if htf_data is None or htf_data.empty:
        return MarketRegime.DEAD

    latest = htf_data.iloc[-1]

    adx = latest.get("adx", 0)
    atr_ratio = latest.get("atr_ratio", 1.0)

    if np.isnan(adx):
        adx = 0
    if np.isnan(atr_ratio):
        atr_ratio = 1.0

    # ── 2. Volume / Volatility Anomalies ───────────────────────────
    if atr_ratio < dead_atr_ratio:
        return MarketRegime.DEAD

    if atr_ratio > atr_volatility_spike:
        return MarketRegime.VOLATILE

    # ── 3. Directional vs Oscillating Market Structure ─────────────
    if adx > adx_trend_threshold:
        return MarketRegime.TRENDING

    if adx < adx_range_threshold:
        return MarketRegime.RANGING

    # ── 4. In-between — default to ranging (safest assumption) ─────
    return MarketRegime.RANGING


def gatekeeper_bias(htf_data: pd.DataFrame, ema_period: int = 200) -> Optional[str]:
    """Determine the gatekeeper's directional bias from H4 trend EMA.

    Returns ``"BULLISH"`` when close > EMA, ``"BEARISH"`` when close < EMA,
    or ``None`` if data is insufficient.

    This is the same H4 200-EMA direction check that ``StrategySelector``
    uses inside ``SmartTrendStrategy._check_trend()``, exposed here so
    ``strategy_engine`` and the dashboard can read the macro bias without
    instantiating a strategy object.
    """
    if htf_data is None or htf_data.empty:
        return None

    ema_col = f"ema_{ema_period}"
    if ema_col not in htf_data.columns:
        return None

    latest = htf_data.iloc[-1]
    close = latest.get("close", None)
    ema = latest.get(ema_col, None)

    if close is None or ema is None or np.isnan(close) or np.isnan(ema):
        return None

    return "BULLISH" if close > ema else "BEARISH"

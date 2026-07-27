"""ATR based stop‑loss calculation.

Provides a helper that returns a stop‑loss price based on the Average True Range (ATR) of the
specified symbol. The function fetches recent bars using the MT5 API, computes the ATR over the
period defined in ``settings.atr_period`` and applies the multiplier ``multiplier``.
"""

import MetaTrader5 as mt5
from bot.config.settings import settings, TradeDirection
from loguru import logger


def calculate_atr_stop(symbol: str, direction: TradeDirection, multiplier: float) -> float:
    """Return a stop‑loss price derived from ATR.

    Args:
        symbol: Symbol name (e.g., "EURUSD").
        direction: Trade direction – ``TradeDirection.BUY`` or ``SELL``.
        multiplier: Multiplier applied to the ATR value (e.g., ``settings.atr_sl_multiplier``).

    Returns:
        A price level suitable for use as ``sl`` in an order request.
    """
    # Fetch recent bars – we need ``atr_period + 1`` candles to compute ``atr_period`` true ranges.
    timeframe = mt5.TIMEFRAME_M15  # default; can be adjusted later.
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, settings.atr_period + 1)
    if not rates or len(rates) < 2:
        logger.warning(f"[ATR] Not enough bar data to compute ATR for {symbol}. Using price as fallback.")
        tick = mt5.symbol_info_tick(symbol)
        if not tick:
            return 0.0
        return tick.ask if direction == TradeDirection.BUY else tick.bid

    # Compute true range for each consecutive candle.
    tr_vals = []
    for i in range(1, len(rates)):
        high = rates[i]["high"]
        low = rates[i]["low"]
        prev_close = rates[i - 1]["close"]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        tr_vals.append(tr)
    atr = sum(tr_vals) / settings.atr_period

    # Get the latest tick to anchor the stop price.
    tick = mt5.symbol_info_tick(symbol)
    if not tick:
        logger.error(f"[ATR] Unable to retrieve tick for {symbol} while calculating stop.")
        return 0.0

    if direction == TradeDirection.BUY:
        stop_price = tick.ask - atr * multiplier
    else:
        stop_price = tick.bid + atr * multiplier
    return round(stop_price, 5)

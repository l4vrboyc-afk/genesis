"""Order Manager — Handles all trade execution with MT5.
Place, modify, close orders with full error handling and logging.
"""

import MetaTrader5 as mt5
from datetime import datetime
from typing import Optional, List
from loguru import logger
from bot.config.settings import settings, TradeDirection
from bot.core.mt5_connector import _explain_mt5_error


# ── Spread Safety Guards ───────────────────────────────────────────────
# Prevents SL/TP placement inside the Bid/Ask spread gap, which would
# cause immediate stop-outs on MT5 — especially critical for SELL trades
# where the Stop Loss triggers on the higher Ask price.
# The buffer multiplier is applied to the current spread so that valid
# stops are never rejected by the broker while dangerous ones are caught.
SPREAD_GUARD_MULTIPLIER = 2.0  # Buffer = spread × this multiplier


class OrderManager:
    """Manages trade execution, modification, and closing through MT5."""

    def __init__(self, connector):
        """
        Args:
            connector: MT5Connector instance for ensuring connectivity.
        """
        self.connector = connector

        # Serialised MT5 executor -- injected by ``TradingOrchestrator.start``
        # immediately after the worker thread is alive. Every native
        # ``mt5.*`` call in this module routes through
        # ``await self._executor.submit(mt5.<fn>, ...)`` so the broker IPC
        # is serialised on a single worker thread.
        self._executor = None

    def attach_executor(self, executor) -> None:
        """Inject the serialised MT5 executor.

        Called once from ``TradingOrchestrator.start`` AFTER
        ``await executor.start()``.
        """
        self._executor = executor

    # ── Execution helpers ────────────────────────────────────────────

    @staticmethod
    def _verify_sl_spread_guard(
        side_buy: bool,
        entry_price: float,
        sl: float,
        tick,
        symbol_info,
    ) -> tuple[bool, float]:
        """Ensure the requested Stop Loss is safely outside the Bid/Ask spread.

        MT5 triggers SL orders on the market price: SELL SL triggers on Ask,
        BUY SL triggers on Bid.  If the SL is placed *inside* the current
        spread gap, the broker may execute an instant stop-out at a loss —
        the exact bug that consumed real balance on heavy-volume positions.

        This guard validates and, if necessary, widens the SL so it sits
        safely outside the spread:

            SELL:  SL must be ≥ entry_price + spread × SPREAD_GUARD_MULTIPLIER
            BUY:   SL must be ≤ entry_price − spread × SPREAD_GUARD_MULTIPLIER

        For SL modifications on already-open positions (trailing stop), the
        same check uses the open price as the reference.

        Args:
            side_buy: True for BUY, False for SELL.
            entry_price: The position's entry price (or open price for mods).
            sl: The requested stop-loss price.
            tick: MT5 tick object with .bid and .ask attributes.
            symbol_info: MT5 symbol_info for the symbol.

        Returns:
            Tuple of (valid, adjusted_sl).  ``valid`` is False when the SL
            cannot be safely placed (reject), ``adjusted_sl`` is the widened
            SL when a correction was applied (``valid`` stays True).
        """
        spread = (tick.ask - tick.bid) if tick else 0.0
        if spread <= 0 or sl is None or sl == 0:
            return True, sl  # No spread → no guard needed; or SL not set

        spread_buffer = spread * SPREAD_GUARD_MULTIPLIER
        digits = getattr(symbol_info, "digits", 5)

        if side_buy:
            # BUY: SL triggers on Bid.  SL must be below entry by at least
            # the spread buffer, otherwise a sudden bid-ask widening could
            # brush the SL.
            max_allowed_sl = round(entry_price - spread_buffer, digits)
            if sl > max_allowed_sl:
                adjusted = max_allowed_sl
                logger.warning(
                    f"🔒 SPREAD GUARD: BUY SL {sl:.{digits}f} inside spread gap "
                    f"(buffer={spread_buffer:.{digits}f}, entry={entry_price:.{digits}f}). "
                    f"Widened to {adjusted:.{digits}f}."
                )
                return True, adjusted
        else:
            # SELL: SL triggers on Ask.  SL must be above entry by at least
            # the spread buffer, or the Ask gap could trigger it immediately.
            min_allowed_sl = round(entry_price + spread_buffer, digits)
            if sl < min_allowed_sl:
                adjusted = min_allowed_sl
                logger.warning(
                    f"🔒 SPREAD GUARD: SELL SL {sl:.{digits}f} inside spread gap "
                    f"(buffer={spread_buffer:.{digits}f}, entry={entry_price:.{digits}f}). "
                    f"Widened to {adjusted:.{digits}f}."
                )
                return True, adjusted

        return True, sl

    @staticmethod
    def _choose_filling(symbol_info) -> int:
        """Pick a ``type_filling`` the broker advertises for this symbol.

        ``mt5.order_send`` returns ``None`` — before the request ever
        reaches the trade server — for a filling mode the symbol doesn't
        support. That was the scalper's 0-fill failure: every order
        hardcoded ``ORDER_FILLING_IOC``, but the MetaQuotes-Demo symbols
        don't advertise IOC, so all 83 attempts returned ``None``.

        ``symbol_info.filling_mode`` is a bitmask (MT5 ``SYMBOL_FILLING_*``):
        bit 0 (1) = FOK supported, bit 1 (2) = IOC supported. We prefer
        IOC (the legacy choice) then FOK, and fall back to ``RETURN`` for
        market-execution accounts that advertise neither.
        """
        mask = getattr(symbol_info, "filling_mode", 0) or 0
        if mask & 2:   # IOC
            return mt5.ORDER_FILLING_IOC
        if mask & 1:   # FOK
            return mt5.ORDER_FILLING_FOK
        return mt5.ORDER_FILLING_RETURN

    @staticmethod
    def _clamp_stops(
        side_buy: bool, price: float, sl: float, tp: float, symbol_info
    ) -> tuple[float, float]:
        """Widen SL/TP out to the broker's ``trade_stops_level``.

        MT5 rejects orders whose SL/TP sit closer to price than the
        symbol's ``trade_stops_level`` (points × ``point``). The scalper's
        M1 0.8×ATR stops can fall *inside* that band — the sizing log
        showed SL distances as small as ``0.00002`` — so without this
        clamp every fill we finally grant would die on "invalid stops".

        The clamp only ever WIDENS (SL/TP further from price), so it can't
        invalidate the risk validator's R:R or a favorable trail: a wider
        SL is the only direction that stays broker-legal here.
        """
        stops = (getattr(symbol_info, "trade_stops_level", 0) or 0) * (
            getattr(symbol_info, "point", 0.0) or 0.0
        )
        if stops <= 0:
            return sl, tp
        if side_buy:
            return min(sl, price - stops), max(tp, price + stops)
        return max(sl, price + stops), min(tp, price - stops)

    # ── Place Orders ────────────────────────────────────────────────

    async def place_market_order(
        self,
        symbol: str,
        direction: TradeDirection,
        volume: float,
        tp: float,
        sl: Optional[float] = None,
        comment: str = "Genesis Bot",
        magic: Optional[int] = None,
    ) -> Optional[dict]:
        if not await self.connector.ensure_connected():
            return None

        if magic is None:
            magic = settings.magic_number

        tick = await self._executor.submit(mt5.symbol_info_tick, symbol)
        # If SL not provided, compute dynamic stop based on ATR volatility
        if sl is None:
            try:
                from bot.core.risk.atr_stop import calculate_atr_stop
                sl = calculate_atr_stop(symbol, direction, settings.atr_sl_multiplier)
                logger.info(f"[ATR] Dynamic SL for {symbol} calculated: {sl:.5f}")
            except Exception as e:
                logger.error(f"Failed to calculate ATR stop for {symbol}: {e}")
                sl = tick.ask if direction == TradeDirection.BUY else tick.bid
        if tick is None:
            logger.error(f"❌ Failed to get price for {symbol}")
            return None

        if direction == TradeDirection.BUY:
            order_type = mt5.ORDER_TYPE_BUY
            price = tick.ask
        elif direction == TradeDirection.SELL:
            order_type = mt5.ORDER_TYPE_SELL
            price = tick.bid
        else:
            logger.warning("⚠️ Direction is HOLD — no order placed")
            return None

        symbol_info = await self._executor.submit(mt5.symbol_info, symbol)
        if symbol_info is None:
            logger.error(f"❌ Symbol info not found for {symbol}")
            return None

        # Adaptive filling mode + broker-legal stops distance. See
        # ``_choose_filling`` / ``_clamp_stops`` — these fix the class of
        # 0-fill returns (unsupported filling) and "invalid stops"
        # rejections that the scalper hit on MetaQuotes-Demo.
        filling = self._choose_filling(symbol_info)
        sl, tp = self._clamp_stops(
            order_type == mt5.ORDER_TYPE_BUY, price, sl, tp, symbol_info
        )

        # ── Spread guard: ensure SL sits safely outside the Bid/Ask gap ─
        # Without this, a SL placed inside the spread causes an instant
        # market stop-out — especially deadly for SELL trades where SL
        # triggers on the higher Ask price.
        if sl is not None and sl > 0:
            valid, sl = self._verify_sl_spread_guard(
                side_buy=(order_type == mt5.ORDER_TYPE_BUY),
                entry_price=price,
                sl=sl,
                tick=tick,
                symbol_info=symbol_info,
            )
            if not valid:
                logger.error(
                    f"❌ SPREAD GUARD REJECTED {symbol} {direction.value}: "
                    f"SL {sl:.{symbol_info.digits}f} cannot be safely placed "
                    f"outside the spread gap."
                )
                return None

        volume = self._normalize_volume(volume, symbol_info)

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": str(symbol),
            "volume": float(volume),
            "type": int(order_type),
            "price": float(price),
            "sl": float(round(sl, symbol_info.digits)),
            "tp": float(round(tp, symbol_info.digits)),
            "deviation": 20,
            "magic": int(magic),
            "comment": str(comment),
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": int(filling),
        }
        def _do_send():
            return mt5.order_send(request)

        result = await self._executor.submit(_do_send)
        if result is None:
            # order_send returns None pre-server (unsupported filling,
            # not-connected, malformed request). Surface mt5.last_error()
            # so the real reason is logged instead of a bare "None" — the
            # silence here is what let the 0-fill bug run for 8 hours.
            logger.error(
                f"[ORDER_FAILED] Order send returned None for {symbol} "
                f"(filling={filling}); {_explain_mt5_error(mt5.last_error())}"
            )
            return None

        if result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error(
                f"[ORDER_FAILED] Order failed for {symbol}: {result.retcode} — {result.comment}; "
                f"{_explain_mt5_error(mt5.last_error())}"
            )
            return None

        order_info = {
            "ticket": result.order,
            "deal": result.deal,
            "symbol": symbol,
            "direction": direction.value,
            "volume": volume,
            "price": result.price,
            "sl": sl,
            "tp": tp,
            "comment": comment,
            "time": datetime.now(),
        }

        logger.success(
            f"[ORDER_SUCCESS] {direction.value.upper()} {symbol} | "
            f"Vol: {volume} | Price: {result.price:.5f} | "
            f"SL: {sl:.5f} | TP: {tp:.5f} | "
            f"Ticket: {result.order}"
        )
        # Log the trade to persistent storage
        try:
            from bot.core.data_logger import log_trade
            await log_trade(order_info)
        except Exception as e:
            logger.error(f"Failed to persist trade log: {e}")

        return order_info

    # ── Modify Orders ───────────────────────────────────────────────

    async def modify_position(
        self,
        ticket: int,
        sl: Optional[float] = None,
        tp: Optional[float] = None,
    ) -> bool:
        if not await self.connector.ensure_connected():
            return False

        position = await self._executor.submit(mt5.positions_get, ticket=ticket)
        if position is None or len(position) == 0:
            logger.error(f"❌ Position {ticket} not found")
            return False

        pos = position[0]
        symbol_info = await self._executor.submit(mt5.symbol_info, pos.symbol)
        if symbol_info is None:
            logger.error(f"❌ Symbol info not found for {pos.symbol}")
            return False

        # ── Spread guard: ensure modified SL is safely outside the Bid/Ask gap ─
        new_sl = sl if sl is not None else pos.sl
        if new_sl > 0:
            tick = await self._executor.submit(mt5.symbol_info_tick, pos.symbol)
            if tick:
                valid, new_sl = self._verify_sl_spread_guard(
                    side_buy=(pos.type == mt5.ORDER_TYPE_BUY),
                    entry_price=pos.price_open,
                    sl=new_sl,
                    tick=tick,
                    symbol_info=symbol_info,
                )
                if not valid:
                    logger.error(
                        f"❌ SPREAD GUARD REJECTED modify T{ticket}: "
                        f"SL {new_sl:.{symbol_info.digits}f} cannot be safely placed."
                    )
                    return False

        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": int(ticket),
            "symbol": str(pos.symbol),
            "sl": float(round(new_sl, symbol_info.digits)),
            "tp": float(round(tp if tp is not None else pos.tp, symbol_info.digits)),
        }

        def _do_send():
            return mt5.order_send(request)

        result = await self._executor.submit(_do_send)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            error = result.comment if result else "Unknown error"
            logger.error(f"❌ Failed to modify position {ticket}: {error}")
            return False

        logger.info(
            f"[MODIFY] Modified position {ticket} | "
            f"SL: {request['sl']:.5f} | TP: {request['tp']:.5f}"
        )
        return True

    async def trailing_stop(
        self, ticket: int, trail_points: float
    ) -> bool:
        """
        Compute new SL from trail_points and apply it via MT5.
        Fetch-and-compute runs on the thread pool (no lock held on caller side);
        the actual order_send happens through ``_apply_trailing_stop``.
        """
        if not await self.connector.ensure_connected():
            return False

        position = await self._executor.submit(mt5.positions_get, ticket=ticket)
        if position is None or len(position) == 0:
            return False

        pos = position[0]
        tick = await self._executor.submit(mt5.symbol_info_tick, pos.symbol)
        if tick is None:
            return False

        new_sl = None

        if pos.type == mt5.ORDER_TYPE_BUY:
            potential_sl = tick.bid - trail_points
            if potential_sl > pos.sl and potential_sl > pos.price_open:
                new_sl = potential_sl

        elif pos.type == mt5.ORDER_TYPE_SELL:
            potential_sl = tick.ask + trail_points
            if pos.sl == 0 or (potential_sl < pos.sl and potential_sl < pos.price_open):
                new_sl = potential_sl

        if new_sl is not None:
            # Pass side_buy and entry_price from the already-fetched position
            # to avoid an extra MT5 fetch in _apply_trailing_stop's spread guard.
            side_buy = (pos.type == mt5.ORDER_TYPE_BUY)
            return await self._apply_trailing_stop(
                ticket, new_sl, pos.symbol, pos.tp,
                side_buy=side_buy, entry_price=pos.price_open,
            )
        return False

    async def _apply_trailing_stop(
        self, ticket: int, new_sl: float, symbol: str, existing_tp: float = 0.0,
        side_buy: Optional[bool] = None,
        entry_price: Optional[float] = None,
    ) -> bool:
        """
        Low-level MT5 write for a computed trailing-stop SL.
        This method holds NO locks; the caller is responsible for lock discipline.
        It exists so callers (e.g. the orchestrator) can acquire their own
        asyncio.Lock, compute new_sl outside it, then call this inside the lock
        for the ~1ms mt5.order_send without holding the lock during compute.

        Args:
            ticket: MT5 position ticket.
            new_sl: The computed stop-loss price.
            symbol: Trading symbol.
            existing_tp: Current take-profit price (0 = leave unchanged).
            side_buy: True for BUY, False for SELL. Pass this from the
                caller to avoid an extra MT5 ``positions_get`` call for the
                spread guard. If omitted, the method fetches the position
                itself (slightly slower).
            entry_price: The original entry/open price. Pass from the
                caller to avoid an extra MT5 fetch. Required if
                ``side_buy`` is provided.
        """
        symbol_info = await self._executor.submit(mt5.symbol_info, symbol)
        if symbol_info is None:
            return False

        # ── Spread guard: ensure trailing SL is safely outside the Bid/Ask gap ─
        if new_sl > 0:
            # Fetch position info if caller didn't provide it
            if side_buy is None or entry_price is None:
                pos_raw = await self._executor.submit(mt5.positions_get, ticket=ticket)
                if pos_raw is not None and len(pos_raw) > 0:
                    side_buy = (pos_raw[0].type == mt5.ORDER_TYPE_BUY)
                    entry_price = pos_raw[0].price_open
                else:
                    logger.warning(f"⚠️ SPREAD GUARD: Cannot fetch position {ticket} for trail")
                    side_buy = None

            if side_buy is not None and entry_price is not None and entry_price > 0:
                tick = await self._executor.submit(mt5.symbol_info_tick, symbol)
                if tick:
                    valid, adj_sl = self._verify_sl_spread_guard(
                        side_buy=side_buy,
                        entry_price=entry_price,
                        sl=new_sl,
                        tick=tick,
                        symbol_info=symbol_info,
                    )
                    if not valid:
                        logger.error(
                            f"❌ SPREAD GUARD REJECTED trail T{ticket} ({symbol}): "
                            f"SL {new_sl:.{symbol_info.digits}f} cannot be safely placed."
                        )
                        return False
                    new_sl = adj_sl

        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": int(ticket),
            "symbol": str(symbol),
            "sl": float(round(new_sl, symbol_info.digits)),
            "tp": float(round(existing_tp, symbol_info.digits)) if existing_tp > 0 else 0.0,
        }

        def _do_send():
            return mt5.order_send(request)

        result = await self._executor.submit(_do_send)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            error = result.comment if result else "Unknown error"
            logger.error(f"❌ Failed to apply trailing stop {ticket}: {error}")
            return False

        logger.debug(f"[MODIFY] Trailing stop applied: Ticket {ticket} SL → {request['sl']:.5f}")
        return True

    # ── Close Orders ────────────────────────────────────────────────

    async def close_position(
        self, ticket: int, comment: str = "Genesis Close"
    ) -> Optional[dict]:
        if not await self.connector.ensure_connected():
            return None

        position = await self._executor.submit(mt5.positions_get, ticket=ticket)
        if position is None or len(position) == 0:
            logger.error(f"❌ Position {ticket} not found for closing")
            return None

        pos = position[0]
        tick = await self._executor.submit(mt5.symbol_info_tick, pos.symbol)
        if tick is None:
            return None

        symbol_info = await self._executor.submit(mt5.symbol_info, pos.symbol)
        if symbol_info is None:
            logger.error(f"❌ Symbol info not found for {pos.symbol} (close)")
            return None
        filling = self._choose_filling(symbol_info)

        if pos.type == mt5.ORDER_TYPE_BUY:
            close_type = mt5.ORDER_TYPE_SELL
            price = tick.bid
        else:
            close_type = mt5.ORDER_TYPE_BUY
            price = tick.ask

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "position": int(ticket),
            "symbol": str(pos.symbol),
            "volume": float(pos.volume),
            "type": int(close_type),
            "price": float(price),
            "deviation": 20,
            "magic": int(settings.magic_number),
            "comment": str(comment),
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": int(filling),
        }

        def _do_send():
            return mt5.order_send(request)

        result = await self._executor.submit(_do_send)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            error = result.comment if result else "Unknown error"
            logger.error(
                f"❌ Failed to close position {ticket}: {error} "
                f"(filling={filling}); {_explain_mt5_error(mt5.last_error())}"
            )
            return None

        close_info = {
            "ticket": ticket,
            "symbol": pos.symbol,
            "direction": "buy" if pos.type == mt5.ORDER_TYPE_BUY else "sell",
            "volume": pos.volume,
            "open_price": pos.price_open,
            "close_price": getattr(result, "price", price),
            "profit": getattr(result, "profit", pos.profit),
            "comment": comment,
            "time": datetime.now(),
        }

        # Use broker-confirmed fill data
        actual_price = getattr(result, "price", price)
        actual_profit = getattr(result, "profit", pos.profit)
        emoji = "🟢" if actual_profit >= 0 else "🔴"
        logger.info(
            f"{emoji} Closed {pos.symbol} | "
            f"P&L: ${actual_profit:.2f} | "
            f"Open: {pos.price_open:.5f} → Close: {actual_price:.5f}"
        )

        return close_info

    async def close_all_positions(self, comment: str = "Genesis Close All") -> List[dict]:
        """Close all open positions."""
        results = []
        positions = await self.get_open_positions()

        for pos in positions:
            result = await self.close_position(pos["ticket"], comment)
            if result:
                results.append(result)

        if results:
            logger.info(f"[CLEANUP] Closed {len(results)} position(s)")

        return results

    # ── Position Queries ────────────────────────────────────────────

    async def get_open_positions(self, symbol: Optional[str] = None) -> List[dict]:
        if not await self.connector.ensure_connected():
            return []

        if symbol:
            positions = await self._executor.submit(mt5.positions_get, symbol=symbol)
        else:
            positions = await self._executor.submit(mt5.positions_get)

        if positions is None:
            return []

        return [
            {
                "ticket": pos.ticket,
                "symbol": pos.symbol,
                "direction": "buy" if pos.type == mt5.ORDER_TYPE_BUY else "sell",
                "volume": pos.volume,
                "open_price": pos.price_open,
                "current_price": pos.price_current,
                "sl": pos.sl,
                "tp": pos.tp,
                "profit": pos.profit,
                "swap": pos.swap,
                "magic": pos.magic,
                "comment": pos.comment,
                "time": datetime.fromtimestamp(pos.time),
            }
            for pos in positions
        ]

    async def get_position_count(self) -> int:
        """Get the number of currently open positions."""
        if not await self.connector.ensure_connected():
            return 0
        positions = await self._executor.submit(mt5.positions_get)
        return len(positions) if positions else 0

    async def has_position(self, symbol: str) -> bool:
        """Check if there's already an open position for a symbol."""
        if not await self.connector.ensure_connected():
            return False
        positions = await self._executor.submit(mt5.positions_get, symbol=symbol)
        return positions is not None and len(positions) > 0

    # ── Helpers ─────────────────────────────────────────────────────

    def _normalize_volume(self, volume: float, symbol_info) -> float:
        """Normalize volume to valid lot size for the symbol."""
        min_vol = symbol_info.volume_min
        max_vol = symbol_info.volume_max
        step = symbol_info.volume_step

        # Clamp to min/max
        volume = max(min_vol, min(volume, max_vol))

        # Round to nearest step
        volume = round(volume / step) * step
        volume = round(volume, 2)

        return volume

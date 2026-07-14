"""Order Manager — Handles all trade execution with MT5.
Place, modify, close orders with full error handling and logging.
"""

import asyncio
import MetaTrader5 as mt5
from datetime import datetime
from typing import Optional, List
from loguru import logger
from bot.config.settings import settings, TradeDirection


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

    # ── Place Orders ────────────────────────────────────────────────

    async def place_market_order(
        self,
        symbol: str,
        direction: TradeDirection,
        volume: float,
        sl: float,
        tp: float,
        comment: str = "Genesis Bot",
        magic: Optional[int] = None,
    ) -> Optional[dict]:
        if not await self.connector.ensure_connected():
            return None

        if magic is None:
            magic = settings.magic_number

        tick = await self._executor.submit(mt5.symbol_info_tick, symbol)
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

        volume = self._normalize_volume(volume, symbol_info)

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": volume,
            "type": order_type,
            "price": price,
            "sl": round(sl, symbol_info.digits),
            "tp": round(tp, symbol_info.digits),
            "deviation": 20,
            "magic": magic,
            "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result = await self._executor.submit(mt5.order_send, request)
        if result is None:
            logger.error(f"❌ Order send returned None for {symbol}")
            return None

        if result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error(
                f"❌ Order failed for {symbol}: {result.retcode} — {result.comment}"
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
            f"✅ {direction.value.upper()} {symbol} | "
            f"Vol: {volume} | Price: {result.price:.5f} | "
            f"SL: {sl:.5f} | TP: {tp:.5f} | "
            f"Ticket: {result.order}"
        )

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

        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": ticket,
            "symbol": pos.symbol,
            "sl": round(sl if sl is not None else pos.sl, symbol_info.digits),
            "tp": round(tp if tp is not None else pos.tp, symbol_info.digits),
        }

        result = await self._executor.submit(mt5.order_send, request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            error = result.comment if result else "Unknown error"
            logger.error(f"❌ Failed to modify position {ticket}: {error}")
            return False

        logger.info(
            f"📝 Modified position {ticket} | "
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
            return await self._apply_trailing_stop(ticket, new_sl, pos.symbol, pos.tp)
        return False

    async def _apply_trailing_stop(
        self, ticket: int, new_sl: float, symbol: str, existing_tp: float = 0.0
    ) -> bool:
        """
        Low-level MT5 write for a computed trailing-stop SL.
        This method holds NO locks; the caller is responsible for lock discipline.
        It exists so callers (e.g. the orchestrator) can acquire their own
        asyncio.Lock, compute new_sl outside it, then call this inside the lock
        for the ~1ms mt5.order_send without holding the lock during compute.
        """
        symbol_info = await self._executor.submit(mt5.symbol_info, symbol)
        if symbol_info is None:
            return False

        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": ticket,
            "symbol": symbol,
            "sl": round(new_sl, symbol_info.digits),
            "tp": round(existing_tp, symbol_info.digits) if existing_tp > 0 else 0,
        }

        result = await self._executor.submit(mt5.order_send, request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            error = result.comment if result else "Unknown error"
            logger.error(f"❌ Failed to apply trailing stop {ticket}: {error}")
            return False

        logger.debug(f"📝 Trailing stop applied: Ticket {ticket} SL → {request['sl']:.5f}")
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

        if pos.type == mt5.ORDER_TYPE_BUY:
            close_type = mt5.ORDER_TYPE_SELL
            price = tick.bid
        else:
            close_type = mt5.ORDER_TYPE_BUY
            price = tick.ask

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "position": ticket,
            "symbol": pos.symbol,
            "volume": pos.volume,
            "type": close_type,
            "price": price,
            "deviation": 20,
            "magic": settings.magic_number,
            "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result = await self._executor.submit(mt5.order_send, request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            error = result.comment if result else "Unknown error"
            logger.error(f"❌ Failed to close position {ticket}: {error}")
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
            logger.info(f"🧹 Closed {len(results)} position(s)")

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

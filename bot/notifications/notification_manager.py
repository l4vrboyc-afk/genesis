"""
Notification Manager — Coordinates sending alerts to Discord or other integrations.
Uses an asyncio.Queue to decouple trade execution from API requests.
"""

import asyncio
from datetime import datetime
from loguru import logger


class NotificationManager:
    """Orchestrates outgoing notifications via an async queue."""

    QUEUE_MAXSIZE = 1000

    def __init__(self):
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=self.QUEUE_MAXSIZE)
        logger.info(f"📢 Notification Manager initialized (maxsize={self.QUEUE_MAXSIZE})")

    async def _enqueue(self, payload: dict):
        """
        Put payload on queue with bounded-size + drop-oldest overflow policy.
        If the queue is full, drain the oldest entries to make room and log the overflow.

        Uses ``put_nowait`` so ``QueueFull`` is actually raised — ``Queue.put``
        blocks indefinitely when full and would deadlock the producers
        (orchestrator's ``notify_*`` awaiters) on any Discord rate-limit stall.
        """
        try:
            self.queue.put_nowait(payload)
            return
        except asyncio.QueueFull:
            logger.warning(
                "⚠️ Notification queue overflow — dropping oldest entries to make room"
            )
        # Drain oldest until there's room for the new payload
        while True:
            try:
                self.queue.get_nowait()
                self.queue.task_done()
            except asyncio.QueueEmpty:
                break
            try:
                self.queue.put_nowait(payload)
                return
            except asyncio.QueueFull:
                continue

    async def send_custom_notification(self, title: str, message: str, color: int = 0x3498db):
        """Send a basic custom notification."""
        payload = {
            "type": "custom",
            "title": title,
            "message": message,
            "color": color,
            "timestamp": datetime.now(),
        }
        await self._enqueue(payload)

    async def notify_trade_open(self, trade_info: dict):
        """
        Send trade open alert.
        trade_info fields: ticket, symbol, direction, volume, price, sl, tp, strategy, regime
        """
        payload = {
            "type": "trade_open",
            "data": trade_info,
            "timestamp": datetime.now(),
        }
        await self._enqueue(payload)
        logger.debug(f"📢 Queued notification for trade open: ticket={trade_info.get('ticket')}")

    async def notify_trade_close(self, close_info: dict):
        """
        Send trade close alert.
        close_info fields: ticket, symbol, direction, volume, open_price, close_price, profit, comment
        """
        payload = {
            "type": "trade_close",
            "data": close_info,
            "timestamp": datetime.now(),
        }
        await self._enqueue(payload)
        logger.debug(f"📢 Queued notification for trade close: ticket={close_info.get('ticket')}")

    async def notify_alert(self, message: str, alert_type: str = "warning"):
        """Send system alert (e.g. drawdown hit, pause, error)."""
        payload = {
            "type": "alert",
            "alert_type": alert_type,
            "message": message,
            "timestamp": datetime.now(),
        }
        await self._enqueue(payload)
        logger.debug(f"📢 Queued alert notification: {message}")

    async def notify_daily_summary(self, summary_info: dict):
        """Send daily performance summary embed."""
        payload = {
            "type": "daily_summary",
            "data": summary_info,
            "timestamp": datetime.now(),
        }
        await self._enqueue(payload)
        logger.debug("📢 Queued daily summary notification")

    async def notify_regime_change(
        self, old_regime: str, new_regime: str, adx: float, atr_ratio: float
    ):
        """Send regime-change alert when the strategy selector flips."""
        payload = {
            "type": "regime_change",
            "data": {
                "old_regime": old_regime,
                "new_regime": new_regime,
                "adx": round(adx, 1),
                "atr_ratio": round(atr_ratio, 2),
            },
            "timestamp": datetime.now(),
        }
        await self._enqueue(payload)
        logger.info(f"🔄 Queued regime change notification: {old_regime} → {new_regime}")


# Singleton instance
notification_manager = NotificationManager()

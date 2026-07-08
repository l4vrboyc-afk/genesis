"""
Database Models for Genesis Trading Bot.
Uses SQLAlchemy for ORM mapping.
"""

from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for all models."""
    pass


class TradeLog(Base):
    """Logs of all trades (open and closed)."""
    
    __tablename__ = "trade_logs"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    ticket = Column(Integer, unique=True, index=True, nullable=False)
    symbol = Column(String(20), nullable=False)
    direction = Column(String(10), nullable=False)  # "buy" or "sell"
    volume = Column(Float, nullable=False)
    entry_price = Column(Float, nullable=False)
    exit_price = Column(Float, nullable=True)
    sl = Column(Float, nullable=False)
    tp = Column(Float, nullable=False)
    profit = Column(Float, default=0.0, nullable=False)
    swap = Column(Float, default=0.0, nullable=False)
    entry_comment = Column("entry_comment", String(200), nullable=True)
    close_comment = Column("close_comment", String(200), nullable=True)
    # Legacy column retained for backwards-compat with already-created DBs.
    comment = Column("comment", String(100), nullable=True)
    open_time = Column(DateTime, default=datetime.utcnow, nullable=False)
    close_time = Column(DateTime, nullable=True)
    strategy = Column(String(50), nullable=True)
    market_regime = Column(String(30), nullable=True)
    status = Column(String(20), default="open", nullable=False)  # "open", "closed"

    def to_dict(self) -> dict:
        """Convert model instance to dictionary."""
        return {
            "id": self.id,
            "ticket": self.ticket,
            "symbol": self.symbol,
            "direction": self.direction,
            "volume": self.volume,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "sl": self.sl,
            "tp": self.tp,
            "profit": self.profit,
            "swap": self.swap,
            "entry_comment": self.entry_comment,
            "close_comment": self.close_comment,
            "comment": self.close_comment or self.entry_comment,
            "open_time": self.open_time.isoformat() if self.open_time else None,
            "close_time": self.close_time.isoformat() if self.close_time else None,
            "strategy": self.strategy,
            "market_regime": self.market_regime,
            "status": self.status,
        }


class DailyPerformance(Base):
    """Daily summary metrics of the bot's performance."""
    
    __tablename__ = "daily_performance"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(String(10), unique=True, index=True, nullable=False)  # "YYYY-MM-DD"
    balance = Column(Float, nullable=False)
    equity = Column(Float, nullable=False)
    pnl = Column(Float, default=0.0, nullable=False)
    drawdown = Column(Float, default=0.0, nullable=False)
    win_rate = Column(Float, default=0.0, nullable=False)
    trade_count = Column(Integer, default=0, nullable=False)

    def to_dict(self) -> dict:
        """Convert model instance to dictionary."""
        return {
            "id": self.id,
            "date": self.date,
            "balance": self.balance,
            "equity": self.equity,
            "pnl": self.pnl,
            "drawdown": self.drawdown,
            "win_rate": self.win_rate,
            "trade_count": self.trade_count,
        }


class BotState(Base):
    """Persistent storage for bot settings/states."""
    
    __tablename__ = "bot_state"
    
    key = Column(String(50), primary_key=True)
    value = Column(String(500), nullable=False)

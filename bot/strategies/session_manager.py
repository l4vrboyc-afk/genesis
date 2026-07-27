"""
Session Manager — Market session awareness for day trading strategies.

Provides session timing logic for the Day Trader profile's 3 strategies:
- Trend Engine: Active during London/NY sessions when ADX > 25
- Mean Reversion: Targets quiet Asian session hours (00:00-06:00 UTC)
- Breakout: Trades session open windows (London 07:00 UTC, NY 12:00 UTC)

All times are in UTC to match MT5's timezone handling.
"""

from datetime import datetime, time
from enum import Enum
from typing import Optional, NamedTuple
from loguru import logger


class MarketSession(Enum):
    """Trading session classification."""
    ASIAN = "asian"           # 00:00 - 07:00 UTC
    LONDON = "london"        # 07:00 - 16:00 UTC
    NEW_YORK = "new_york"    # 12:00 - 20:00 UTC
    OVERLAP = "overlap"      # London-NY overlap (12:00-16:00 UTC)
    OUTSIDE = "outside"      # Outside all sessions


class SessionInfo(NamedTuple):
    """Session state information."""
    session: MarketSession
    minutes_since_open: int
    is_opening_window: bool
    is_closing_window: bool


class SessionManager:
    """
    Manages market session awareness for time-based strategy decisions.

    Session Times (UTC):
    - Asian Session: 00:00 - 07:00
    - London Session: 07:00 - 16:00
    - New York Session: 12:00 - 20:00

    The London-NY overlap (12:00-16:00 UTC) is classified as OVERLAP
    for special handling by strategies.
    """

    def __init__(self):
        # Session hours in UTC
        self._session_hours = {
            MarketSession.ASIAN: (time(0, 0), time(7, 0)),
            MarketSession.LONDON: (time(7, 0), time(16, 0)),
            MarketSession.NEW_YORK: (time(12, 0), time(20, 0)),
        }

        # Opening windows (first 30 minutes of session = high volatility)
        self._opening_window_minutes = 30

        # Closing windows (last 30 minutes = potential reversals)
        self._closing_window_minutes = 30

        self._last_session: Optional[MarketSession] = None
        self._last_checked: Optional[datetime] = None

    def _get_utc_now(self) -> datetime:
        """Get current UTC time."""
        return datetime.utcnow()

    def get_current_session(self) -> SessionInfo:
        """
        Get current market session information.

        Returns:
            SessionInfo with session details
        """
        now = self._get_utc_now()
        current_time = now.time()

        # Determine which session we're in
        session = self._classify_session(current_time)

        # Calculate minutes since session open
        minutes_since_open = self._minutes_since_session_open(current_time, session)

        # Check if in opening window
        session_duration = self._get_session_duration(session)
        is_opening_window = minutes_since_open <= self._opening_window_minutes
        is_closing_window = (session_duration - minutes_since_open) <= self._closing_window_minutes

        # Track session changes for logging
        if self._last_session != session:
            logger.debug(f"Session changed: {self._last_session} → {session.value}")
            self._last_session = session
        self._last_checked = now

        return SessionInfo(
            session=session,
            minutes_since_open=minutes_since_open,
            is_opening_window=is_opening_window,
            is_closing_window=is_closing_window
        )

    def _classify_session(self, current_time: time) -> MarketSession:
        """Classify the current time into a market session."""
        # Check NY-London overlap first (12:00-16:00 UTC)
        if time(12, 0) <= current_time < time(16, 0):
            return MarketSession.OVERLAP

        # Asian: 00:00 - 07:00
        if time(0, 0) <= current_time < time(7, 0):
            return MarketSession.ASIAN

        # London: 07:00 - 12:00
        if time(7, 0) <= current_time < time(12, 0):
            return MarketSession.LONDON

        # New York: 16:00 - 20:00
        if time(16, 0) <= current_time < time(20, 0):
            return MarketSession.NEW_YORK

        # Outside all sessions: 20:00 - 00:00
        return MarketSession.OUTSIDE

    def _minutes_since_session_open(self, current_time: time, session: MarketSession) -> int:
        """Calculate minutes since the current session opened."""
        if session == MarketSession.OUTSIDE:
            return 0

        start_time, _ = self._session_hours.get(session, (time(0, 0), time(0, 0)))

        # Handle day rollover for Asian session
        if session == MarketSession.ASIAN:
            # Asian is 00:00-07:00 UTC, which is today's session
            hours = current_time.hour
            minutes = current_time.minute
        else:
            hours = start_time.hour
            minutes = start_time.minute

        session_start_minutes = hours * 60 + minutes
        current_minutes = current_time.hour * 60 + current_time.minute

        return max(0, current_minutes - session_start_minutes)

    def _get_session_duration(self, session: MarketSession) -> int:
        """Get total session duration in minutes."""
        if session == MarketSession.OUTSIDE:
            return 0

        start, end = self._session_hours.get(session, (time(0, 0), time(0, 0)))

        start_minutes = start.hour * 60 + start.minute
        end_minutes = end.hour * 60 + end.minute

        duration = end_minutes - start_minutes
        return duration

    def get_session_for_strategy(self, strategy_type: str) -> Optional[MarketSession]:
        """
        Get the appropriate session for a given strategy type.

        Strategy Mapping:
        - "trend": London + NY (active sessions with ADX > 25)
        - "mean_rev": Asian only (quiet hours)
        - "breakout": Session opens (London 07:00, NY 12:00)

        Args:
            strategy_type: One of "trend", "mean_rev", "breakout"

        Returns:
            Current session if it matches the strategy's preferred session, None otherwise
        """
        current = self.get_current_session()

        if strategy_type == "trend":
            # Trend works during active sessions
            if current.session in (MarketSession.LONDON, MarketSession.NEW_YORK, MarketSession.OVERLAP):
                return current.session
            return None

        elif strategy_type == "mean_rev":
            # Mean reversion for quiet hours
            if current.session == MarketSession.ASIAN:
                return current.session
            return None

        elif strategy_type == "breakout":
            # Breakout only in opening windows
            if current.is_opening_window:
                return current.session
            return None

        return None

    def is_trading_allowed(self) -> bool:
        """
        Check if we're in an active trading session.

        Returns True if currently in Asian, London, or NY session,
        False if outside all sessions.
        """
        current = self.get_current_session()
        return current.session != MarketSession.OUTSIDE

    def get_active_sessions(self) -> list:
        """Get list of currently active sessions (handles overlaps)."""
        current = self.get_current_session()

        if current.session == MarketSession.OVERLAP:
            return [MarketSession.LONDON, MarketSession.NEW_YORK]
        elif current.session in (MarketSession.ASIAN, MarketSession.LONDON, MarketSession.NEW_YORK):
            return [current.session]
        return []


# Global instance for convenience
_session_manager: Optional[SessionManager] = None


def get_session_manager() -> SessionManager:
    """Get or create the global SessionManager instance."""
    global _session_manager
    if _session_manager is None:
        _session_manager = SessionManager()
    return _session_manager


def reset_session_manager() -> None:
    """Reset the global session manager (useful for testing)."""
    global _session_manager
    _session_manager = None
"""Shanghai A-share session calendar with an explicitly bounded history."""

from __future__ import annotations

from datetime import date
from importlib.metadata import version

import exchange_calendars as xcals
import pandas as pd

CALENDAR_NAME = "XSHG"
CALENDAR_SOURCE = "exchange_calendars XSHG precomputed exchange-holiday calendar"
CALENDAR_VERSION = version("exchange-calendars")


class AShareTradingCalendar:
    """Use the XSHG calendar for SSE/SZSE sessions within supported bounds."""

    def __init__(self, start: date | str = "2018-01-01", end: date | str = "2025-12-31") -> None:
        self.start = pd.Timestamp(start).normalize()
        self.end = pd.Timestamp(end).normalize()
        if self.start > self.end:
            raise ValueError("Calendar start must be on or before end.")
        self.calendar = xcals.get_calendar(CALENDAR_NAME, start=self.start, end=self.end)
        self.sessions = self._sessions_between(self.start, self.end)
        if self.sessions.empty:
            raise ValueError("No sessions are available in the requested calendar range.")

    def _sessions_between(self, start: pd.Timestamp, end: pd.Timestamp) -> pd.DatetimeIndex:
        first = pd.Timestamp(self.calendar.first_session).tz_localize(None).normalize()
        last = pd.Timestamp(self.calendar.last_session).tz_localize(None).normalize()
        left, right = max(start, first), min(end, last)
        if left > right:
            return pd.DatetimeIndex([])
        sessions = self.calendar.sessions_in_range(left, right)
        if sessions.tz is not None:
            sessions = sessions.tz_localize(None)
        return pd.DatetimeIndex(sessions).normalize()

    def sessions_between(self, start: date | str | pd.Timestamp, end: date | str | pd.Timestamp) -> pd.DatetimeIndex:
        left, right = pd.Timestamp(start).normalize(), pd.Timestamp(end).normalize()
        if left < self.start or right > self.end:
            raise ValueError(
                f"Requested dates {left.date()}..{right.date()} exceed calendar bounds "
                f"{self.start.date()}..{self.end.date()}."
            )
        return self.sessions[(self.sessions >= left) & (self.sessions <= right)]

    def is_session(self, value: date | str | pd.Timestamp) -> bool:
        day = pd.Timestamp(value).normalize()
        if day < self.start or day > self.end:
            raise ValueError("Date is outside the configured calendar bounds.")
        return day in self.sessions

    def label_cutoff(self, boundary: date | str | pd.Timestamp, embargo_sessions: int = 0) -> pd.Timestamp:
        """Latest permissible label date before a boundary and an optional embargo."""
        if embargo_sessions < 0:
            raise ValueError("embargo_sessions cannot be negative.")
        boundary = pd.Timestamp(boundary).normalize()
        index = int(self.sessions.searchsorted(boundary, side="left")) - 1 - embargo_sessions
        if index < 0:
            return self.sessions[0] - pd.Timedelta(days=1)
        return self.sessions[index]

    @property
    def metadata(self) -> dict[str, object]:
        return {
            "calendar": CALENDAR_NAME,
            "source": CALENDAR_SOURCE,
            "version": CALENDAR_VERSION,
            "start": self.sessions[0].date().isoformat(),
            "end": self.sessions[-1].date().isoformat(),
            "session_count": len(self.sessions),
        }

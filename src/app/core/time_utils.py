from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")

# NSE session times (IST)
NSE_OPEN = time(9, 15)
NSE_CLOSE = time(15, 30)
INTRADAY_SIGNAL_START = time(9, 25)
INTRADAY_NO_NEW_ENTRY = time(14, 30)
INTRADAY_SQUAREOFF = time(15, 15)

# NSE holidays – extend as needed; an external source is preferable
_NSE_HOLIDAYS_2024: set[date] = {
    date(2024, 1, 26), date(2024, 3, 25), date(2024, 3, 29), date(2024, 4, 14),
    date(2024, 4, 17), date(2024, 5, 23), date(2024, 6, 17), date(2024, 7, 17),
    date(2024, 8, 15), date(2024, 10, 2), date(2024, 10, 24), date(2024, 11, 1),
    date(2024, 11, 15), date(2024, 12, 25),
}
_NSE_HOLIDAYS_2025: set[date] = {
    date(2025, 2, 26), date(2025, 3, 14), date(2025, 3, 31), date(2025, 4, 10),
    date(2025, 4, 14), date(2025, 4, 18), date(2025, 5, 1), date(2025, 8, 15),
    date(2025, 8, 27), date(2025, 10, 2), date(2025, 10, 20), date(2025, 10, 21),
    date(2025, 11, 5), date(2025, 12, 25),
}
# NSE publishes the definitive holiday list by November each year.
# 2026 dates below are best-effort estimates; update from the official NSE circular
# once published: https://www.nseindia.com/products-services/equity-market-holidays
_NSE_HOLIDAYS_2026: set[date] = {
    date(2026, 1, 26),   # Republic Day
    date(2026, 2, 17),   # Maha Shivratri (approx)
    date(2026, 3, 20),   # Holi (approx)
    date(2026, 4, 3),    # Good Friday
    date(2026, 4, 14),   # Dr. B.R. Ambedkar Jayanti
    date(2026, 5, 1),    # Maharashtra Day / Labour Day
    date(2026, 8, 15),   # Independence Day
    date(2026, 9, 1),    # Ganesh Chaturthi (approx)
    date(2026, 10, 2),   # Gandhi Jayanti
    date(2026, 10, 8),   # Dussehra (approx)
    date(2026, 11, 5),   # Diwali / Guru Nanak Jayanti (approx)
    date(2026, 12, 25),  # Christmas
}
_NSE_HOLIDAYS: set[date] = _NSE_HOLIDAYS_2024 | _NSE_HOLIDAYS_2025 | _NSE_HOLIDAYS_2026


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in _NSE_HOLIDAYS


def next_trading_day(d: date) -> date:
    candidate = d + timedelta(days=1)
    while not is_trading_day(candidate):
        candidate += timedelta(days=1)
    return candidate


def prev_trading_day(d: date) -> date:
    candidate = d - timedelta(days=1)
    while not is_trading_day(candidate):
        candidate -= timedelta(days=1)
    return candidate


def trading_days_between(start: date, end: date) -> list[date]:
    result = []
    current = start
    while current <= end:
        if is_trading_day(current):
            result.append(current)
        current += timedelta(days=1)
    return result


def nth_trading_day_after(d: date, n: int) -> date:
    result = d
    for _ in range(n):
        result = next_trading_day(result)
    return result


def now_ist() -> datetime:
    return datetime.now(tz=IST)


def to_ist(dt: datetime) -> datetime:
    return dt.astimezone(IST)


def to_utc(dt: datetime) -> datetime:
    return dt.astimezone(UTC)


def session_open_dt(d: date) -> datetime:
    return datetime.combine(d, NSE_OPEN, tzinfo=IST)


def session_close_dt(d: date) -> datetime:
    return datetime.combine(d, NSE_CLOSE, tzinfo=IST)


def squareoff_dt(d: date) -> datetime:
    return datetime.combine(d, INTRADAY_SQUAREOFF, tzinfo=IST)

"""Daily local charging window. No scheduling or device commands occur here."""
from datetime import datetime, time
import re


def parse_time(value):
    if not isinstance(value, str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', value):
        raise ValueError('Time must use the 24-hour HH:MM format.')
    return time(int(value[:2]), int(value[3:]))


def in_window(now, start, end):
    """Start inclusive, end exclusive; equal endpoints are rejected, not 24 hours.

    `now` must have the tablet's local timezone. During a daylight-saving fold,
    both instances of the local wall-clock minute follow the same window.
    """
    if not isinstance(now, datetime) or now.utcoffset() is None:
        raise ValueError('A timezone-aware local datetime is required.')
    begin, finish = parse_time(start), parse_time(end)
    if begin == finish:
        raise ValueError('Start and end must differ.')
    current = now.time().replace(tzinfo=None)
    return begin <= current < finish if begin < finish else current >= begin or current < finish

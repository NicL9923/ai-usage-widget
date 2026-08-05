"""Parsers turning raw CLI output into normalized usage windows.

Ported from pingdotgg/t3code PR #4326 (`apps/server/src/provider/providerUsageLimits.ts`).

Both parsers fail closed: unrecognized, malformed, or changed output yields no windows
rather than a wrong number. A missing bar is recoverable; a lying bar is not.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .models import (
    SESSION_WINDOW_MINS,
    WEEKLY_WINDOW_MINS,
    ProviderUsage,
    UsageWindow,
    valid_duration_mins,
    valid_percent,
)

MONTHS = (
    "jan",
    "feb",
    "mar",
    "apr",
    "may",
    "jun",
    "jul",
    "aug",
    "sep",
    "oct",
    "nov",
    "dec",
)

# Matches e.g. "Current session: 3% used · resets Aug 5, 1am (America/Chicago)"
# and         "Current week (Fable): 12.5% used - resets Aug 11, 1:30pm (America/Chicago)"
CLAUDE_WINDOW_RE = re.compile(
    r"^Current (session|week(?: \([^)]+\))?):\s*(\d{1,3}(?:\.\d+)?)% used\s*[\u00b7-]\s*"
    r"resets ([A-Za-z]{3,9}) (\d{1,2}), (\d{1,2})(?::(\d{2}))?(am|pm) \(([^)]+)\)$",
    re.IGNORECASE | re.MULTILINE,
)


def _parse_claude_reset(
    *,
    month: str,
    day: str,
    hour: str,
    minute: str | None,
    meridiem: str,
    time_zone: str,
    checked_at: datetime,
) -> str | None:
    """Resolve Claude's yearless reset stamp into an ISO-8601 instant.

    Claude reports resets without a year ("resets Aug 5, 1am"), and a reset is always
    upcoming. Pick the earliest candidate year that still lands at or after the probe
    time, which handles both directions of a year boundary (a January reset probed in
    December, and a December reset probed just after New Year in UTC). A minute of slack
    absorbs rounding in the reported time.
    """
    try:
        month_index = MONTHS.index(month.lower()[:3]) + 1
    except ValueError:
        return None

    try:
        zone = ZoneInfo(time_zone)
    except (ZoneInfoNotFoundError, ValueError):
        return None

    checked_local = checked_at.astimezone(zone)

    day_number = int(day)
    hour_number = int(hour)
    if not 1 <= day_number <= 31 or not 1 <= hour_number <= 12:
        return None
    if hour_number == 12:
        hour_number = 0
    if meridiem.lower() == "pm":
        hour_number += 12
    minute_number = int(minute) if minute else 0
    if not 0 <= minute_number <= 59:
        return None

    floor = checked_local - timedelta(minutes=1)
    best: datetime | None = None
    for year in (checked_local.year - 1, checked_local.year, checked_local.year + 1):
        try:
            candidate = datetime(
                year, month_index, day_number, hour_number, minute_number, tzinfo=zone
            )
        except ValueError:
            continue  # e.g. Feb 30, or Feb 29 in a non-leap year
        if candidate < floor:
            continue
        if best is None or candidate < best:
            best = candidate
    return best.isoformat() if best else None


def parse_claude_usage(output: str, checked_at: datetime) -> tuple[UsageWindow, ...]:
    """Parse the JSON envelope emitted by `claude --print /usage --output-format json`.

    Claude has no structured usage output, so the percentages live as prose inside the
    envelope's `result` string.
    """
    try:
        decoded = json.loads(output)
    except (json.JSONDecodeError, TypeError):
        return ()
    if not isinstance(decoded, dict) or not isinstance(decoded.get("result"), str):
        return ()

    result = decoded["result"].replace("\r\n", "\n")
    windows: list[UsageWindow] = []
    for match in CLAUDE_WINDOW_RE.finditer(result):
        raw_label, percent, month, day, hour, minute, meridiem, time_zone = match.groups()
        try:
            used_percent = valid_percent(float(percent))
        except ValueError:
            continue
        if used_percent is None:
            continue
        is_session = raw_label.lower() == "session"
        suffix_match = re.search(r"\(([^)]+)\)", raw_label)
        suffix = suffix_match.group(1) if suffix_match else None
        resets_at = _parse_claude_reset(
            month=month,
            day=day,
            hour=hour,
            minute=minute,
            meridiem=meridiem,
            time_zone=time_zone,
            checked_at=checked_at,
        )
        if is_session:
            label = "Session"
        elif suffix and suffix.lower() != "all models":
            label = f"Weekly ({suffix})"
        else:
            label = "Weekly"
        windows.append(
            UsageWindow(
                label=label,
                used_percent=used_percent,
                window_duration_mins=SESSION_WINDOW_MINS if is_session else WEEKLY_WINDOW_MINS,
                resets_at=resets_at,
                is_primary=is_session,
            )
        )
    return tuple(windows)


def _codex_window_label(bucket_name: str | None, duration_mins: int | None) -> str:
    base = (
        "Weekly" if duration_mins is not None and duration_mins >= WEEKLY_WINDOW_MINS else "Session"
    )
    return f"{base} ({bucket_name})" if bucket_name else base


def _map_codex_window(window: Any, bucket_name: str | None, is_primary: bool) -> UsageWindow | None:
    if not isinstance(window, dict):
        return None
    used_percent = valid_percent(window.get("usedPercent"))
    if used_percent is None:
        return None
    duration = valid_duration_mins(window.get("windowDurationMins"))
    resets_at_epoch = window.get("resetsAt")
    resets_at: str | None = None
    if isinstance(resets_at_epoch, (int, float)) and not isinstance(resets_at_epoch, bool):
        try:
            resets_at = datetime.fromtimestamp(resets_at_epoch).astimezone().isoformat()
        except (OverflowError, OSError, ValueError):
            resets_at = None
    return UsageWindow(
        label=_codex_window_label(bucket_name, duration),
        used_percent=used_percent,
        window_duration_mins=duration,
        resets_at=resets_at,
        is_primary=is_primary,
    )


def parse_codex_rate_limits(response: Any) -> tuple[UsageWindow, ...]:
    """Parse the `account/rateLimits/read` result from the Codex app-server.

    Unlike the t3code implementation, this reads every bucket in `rateLimitsByLimitId`,
    not just the default one. Plans such as `prolite` carry model-specific limits (e.g.
    GPT-5.3-Codex-Spark) that only appear there, and hiding them would understate how
    close an account is to being cut off.
    """
    if not isinstance(response, dict):
        return ()

    default_limits = response.get("rateLimits")
    default_id = default_limits.get("limitId") if isinstance(default_limits, dict) else None

    buckets: list[tuple[str | None, dict[str, Any], bool]] = []
    by_id = response.get("rateLimitsByLimitId")
    if isinstance(by_id, dict) and by_id:
        for limit_id, bucket in by_id.items():
            if not isinstance(bucket, dict):
                continue
            is_default = limit_id == default_id
            # The default bucket is the account-wide limit; leave it unlabeled so it
            # reads as "Weekly" rather than "Weekly (codex)".
            name = None if is_default else (bucket.get("limitName") or limit_id)
            buckets.append((name, bucket, is_default))
    elif isinstance(default_limits, dict):
        buckets.append((None, default_limits, True))

    # Default bucket first, then remaining buckets in a stable order.
    buckets.sort(key=lambda item: (not item[2], item[0] or ""))

    windows: list[UsageWindow] = []
    for name, bucket, is_default in buckets:
        for key in ("primary", "secondary"):
            window = _map_codex_window(
                bucket.get(key),
                name,
                is_primary=is_default and key == "primary" and not windows,
            )
            if window is not None:
                windows.append(window)
    return tuple(windows)


def build_provider(
    provider_id: str,
    display_name: str,
    source: str,
    checked_at: datetime,
    windows: tuple[UsageWindow, ...],
) -> ProviderUsage:
    return ProviderUsage(
        id=provider_id,
        display_name=display_name,
        source=source,
        checked_at=checked_at.isoformat(),
        windows=windows,
    )

"""Parsers turning raw CLI output into normalized usage windows.

The Claude and Codex parsers began as Python ports of Nicolas Layne's unmerged
pingdotgg/t3code PR #4326 (`apps/server/src/provider/providerUsageLimits.ts`).

Every parser fails closed: unrecognized, malformed, or changed output yields no windows
rather than a wrong number. A missing bar is recoverable; a lying bar is not.
"""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .models import (
    SESSION_WINDOW_MINS,
    WEEKLY_WINDOW_MINS,
    BankedResets,
    ProviderUsage,
    ResetCredit,
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


def _codex_timestamp(value: Any) -> str | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    try:
        return datetime.fromtimestamp(value).astimezone().isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _map_codex_window(window: Any, bucket_name: str | None, is_primary: bool) -> UsageWindow | None:
    if not isinstance(window, dict):
        return None
    used_percent = valid_percent(window.get("usedPercent"))
    if used_percent is None:
        return None
    duration = valid_duration_mins(window.get("windowDurationMins"))
    return UsageWindow(
        label=_codex_window_label(bucket_name, duration),
        used_percent=used_percent,
        window_duration_mins=duration,
        resets_at=_codex_timestamp(window.get("resetsAt")),
        is_primary=is_primary,
    )


def parse_codex_rate_limits(response: Any) -> tuple[UsageWindow, ...]:
    """Parse the `account/rateLimits/read` result from the Codex app-server.

    Unlike the original PR, this reads every bucket in `rateLimitsByLimitId`,
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


def parse_codex_banked_resets(response: Any) -> BankedResets | None:
    """Parse reset credits returned alongside Codex rate limits."""
    if not isinstance(response, dict):
        return None
    raw = response.get("rateLimitResetCredits")
    if not isinstance(raw, dict):
        return None
    count = raw.get("availableCount")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        return None

    credits: list[ResetCredit] = []
    raw_credits = raw.get("credits")
    if isinstance(raw_credits, list):
        for credit in raw_credits:
            if not isinstance(credit, dict) or credit.get("status") != "available":
                continue
            title = credit.get("title")
            credits.append(
                ResetCredit(
                    title=title.strip() if isinstance(title, str) and title.strip() else "Reset",
                    expires_at=_codex_timestamp(credit.get("expiresAt")),
                )
            )
    return BankedResets(available_count=count, credits=tuple(credits))


GROK_PERIOD_LABELS = {
    "USAGE_PERIOD_TYPE_WEEKLY": "Weekly",
    "USAGE_PERIOD_TYPE_MONTHLY": "Monthly",
}


def _grok_instant(value: Any) -> datetime | None:
    """Read one of Grok's ISO-8601 timestamps, assuming local time if it carries no zone."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.astimezone()


def _grok_amount(value: Any) -> float | None:
    """Read one of Grok's money fields, which arrive as `{"val": n}` or a bare number."""
    if isinstance(value, dict):
        value = value.get("val")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def parse_grok_billing(response: Any) -> tuple[UsageWindow, ...]:
    """Parse the `_x.ai/billing` result from `grok agent stdio`.

    Grok reports the share of the plan's weekly pool already spent in
    `creditUsagePercent`, a 0-100 number cross-checked against the figure its own TUI
    prints on the `/usage` screen.

    That field is omitted entirely until some of the period has been consumed, and
    Grok's TUI renders the omission as a confident "0%". We refuse to: absent is not the
    same as zero, and inventing a reassuring number for output we did not actually get
    is the one failure this project will not ship. A period with no reading contributes
    no window and the provider reads as unavailable until Grok reports something.
    """
    if not isinstance(response, dict):
        return ()
    config = response.get("config")
    if not isinstance(config, dict):
        return ()

    raw_period = config.get("currentPeriod")
    period = raw_period if isinstance(raw_period, dict) else {}
    starts_at = _grok_instant(period.get("start") or config.get("billingPeriodStart"))
    ends_at = _grok_instant(period.get("end") or config.get("billingPeriodEnd"))

    duration_mins: int | None = None
    if starts_at is not None and ends_at is not None and ends_at > starts_at:
        duration_mins = valid_duration_mins((ends_at - starts_at).total_seconds() / 60)
    resets_at = ends_at.astimezone().isoformat() if ends_at is not None else None

    raw_type = period.get("type")
    label = GROK_PERIOD_LABELS.get(raw_type, "Plan") if isinstance(raw_type, str) else "Plan"

    windows: list[UsageWindow] = []

    included_percent = valid_percent(config.get("creditUsagePercent"))
    if included_percent is not None:
        windows.append(
            UsageWindow(
                label=label,
                used_percent=included_percent,
                window_duration_mins=duration_mins,
                resets_at=resets_at,
                is_primary=True,
            )
        )

    cap = _grok_amount(config.get("onDemandCap"))
    used = _grok_amount(config.get("onDemandUsed"))
    if cap is not None and used is not None and cap > 0 and used >= 0:
        # Both sides are the same field pair in the same unit, so a ratio above 1 means
        # the cap was overrun, not that we misread it. Report that as fully consumed
        # rather than discarding the window the way an out-of-range reading would be.
        on_demand_percent = valid_percent(min(100.0, used / cap * 100.0))
        if on_demand_percent is not None:
            windows.append(
                UsageWindow(
                    label="On-demand credits",
                    # On-demand spend is a cap, not a pool tied to the plan period, and
                    # Grok reports no schedule for it. Borrowing the weekly reset would
                    # assert a refill we never observed.
                    window_duration_mins=None,
                    used_percent=on_demand_percent,
                    resets_at=None,
                    # Only the ring's fallback when the included pool reported nothing.
                    is_primary=not windows,
                )
            )

    return tuple(windows)


def build_provider(
    provider_id: str,
    display_name: str,
    source: str,
    checked_at: datetime,
    windows: tuple[UsageWindow, ...],
    banked_resets: BankedResets | None = None,
) -> ProviderUsage:
    return ProviderUsage(
        id=provider_id,
        display_name=display_name,
        source=source,
        checked_at=checked_at.isoformat(),
        windows=windows,
        banked_resets=banked_resets,
    )

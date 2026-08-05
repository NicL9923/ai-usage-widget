"""Normalized usage shapes shared by probes, cache, and the JSON contract."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any

CONTRACT_VERSION = 1

SESSION_WINDOW_MINS = 5 * 60
WEEKLY_WINDOW_MINS = 7 * 24 * 60

PERCENT_TOLERANCE = 0.5


def valid_percent(value: Any) -> float | None:
    """Return `value` as a percentage, or None if it is not a credible one.

    An out-of-range reading means we misread the output — a different field, a fraction
    where a percentage was expected, a changed unit. Clamping would turn that mistake
    into a plausible-looking number, so reject it and show nothing for that window
    instead. The small tolerance absorbs a provider reporting a hair over 100.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0.0 or number > 100.0 + PERCENT_TOLERANCE:
        return None
    return min(100.0, number)


def valid_duration_mins(value: Any) -> int | None:
    """Return `value` as a non-negative whole number of minutes, or None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        return None
    return int(number)


@dataclass(frozen=True, slots=True)
class UsageWindow:
    """A single rate-limit window, e.g. "Session" or "Weekly (Fable)"."""

    label: str
    used_percent: float
    window_duration_mins: int | None = None
    resets_at: str | None = None
    is_primary: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "usedPercent": self.used_percent,
            "windowDurationMins": self.window_duration_mins,
            "resetsAt": self.resets_at,
            "isPrimary": self.is_primary,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> UsageWindow:
        return cls(
            label=str(raw["label"]),
            used_percent=float(raw["usedPercent"]),
            window_duration_mins=raw.get("windowDurationMins"),
            resets_at=raw.get("resetsAt"),
            is_primary=bool(raw.get("isPrimary", False)),
        )


@dataclass(frozen=True, slots=True)
class ProviderUsage:
    """Everything the widget needs to render one provider."""

    id: str
    display_name: str
    source: str
    checked_at: str
    windows: tuple[UsageWindow, ...]
    error: str | None = None
    stale: bool = False

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.windows)

    @property
    def primary_window(self) -> UsageWindow | None:
        """The window the panel ring represents.

        Prefers the window explicitly flagged by the provider, then the shortest
        window (the one that bites first), then whatever is most consumed.
        """
        if not self.windows:
            return None
        for window in self.windows:
            if window.is_primary:
                return window
        with_duration = [w for w in self.windows if w.window_duration_mins]
        if with_duration:
            return min(with_duration, key=lambda w: (w.window_duration_mins, -w.used_percent))
        return max(self.windows, key=lambda w: w.used_percent)

    @property
    def max_used_percent(self) -> float:
        return max((w.used_percent for w in self.windows), default=0.0)

    def as_stale(self) -> ProviderUsage:
        return replace(self, stale=True)

    def to_dict(self) -> dict[str, Any]:
        primary = self.primary_window
        return {
            "id": self.id,
            "displayName": self.display_name,
            "ok": self.ok,
            "error": self.error,
            "stale": self.stale,
            "source": self.source,
            "checkedAt": self.checked_at,
            "primaryUsedPercent": primary.used_percent if primary else None,
            "primaryLabel": primary.label if primary else None,
            "maxUsedPercent": self.max_used_percent if self.windows else None,
            "windows": [w.to_dict() for w in self.windows],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ProviderUsage:
        return cls(
            id=str(raw["id"]),
            display_name=str(raw["displayName"]),
            source=str(raw.get("source", "")),
            checked_at=str(raw.get("checkedAt", "")),
            windows=tuple(UsageWindow.from_dict(w) for w in raw.get("windows", [])),
            error=raw.get("error"),
            stale=bool(raw.get("stale", False)),
        )


def failed(provider_id: str, display_name: str, checked_at: str, error: str) -> ProviderUsage:
    return ProviderUsage(
        id=provider_id,
        display_name=display_name,
        source="",
        checked_at=checked_at,
        windows=(),
        error=error,
    )

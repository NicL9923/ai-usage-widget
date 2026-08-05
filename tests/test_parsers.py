"""Parser tests.

The parsers are the only place this project can be silently *wrong* rather than merely
unavailable, so the cases below focus on the two failure modes that matter: Claude's
yearless reset stamps, and output the parsers must refuse to guess at.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from ai_usage.parsers import parse_claude_usage, parse_codex_rate_limits

FIXTURES = Path(__file__).parent / "fixtures"
CHICAGO = ZoneInfo("America/Chicago")


def envelope(result: str) -> str:
    return json.dumps({"type": "result", "subtype": "success", "result": result})


def claude_line(label: str, percent: str, when: str) -> str:
    return f"Current {label}: {percent}% used \u00b7 resets {when} (America/Chicago)"


@pytest.fixture
def checked_at() -> datetime:
    return datetime(2026, 8, 4, 20, 46, tzinfo=CHICAGO)


class TestClaudeParsing:
    def test_parses_real_output(self, checked_at: datetime) -> None:
        raw = (FIXTURES / "claude_usage.json").read_text(encoding="utf-8")
        windows = parse_claude_usage(raw, checked_at)

        assert [w.label for w in windows] == ["Session", "Weekly", "Weekly (Fable)"]
        assert [w.used_percent for w in windows] == [3.0, 12.0, 47.5]
        assert [w.window_duration_mins for w in windows] == [300, 10080, 10080]
        assert windows[0].is_primary is True
        assert windows[1].is_primary is False

    def test_resolves_reset_year_and_timezone(self, checked_at: datetime) -> None:
        windows = parse_claude_usage(
            envelope(claude_line("session", "3", "Aug 5, 1am")), checked_at
        )
        assert windows[0].resets_at == "2026-08-05T01:00:00-05:00"

    def test_reset_across_new_year_picks_next_year(self) -> None:
        """A January reset probed in December belongs to the following year."""
        checked = datetime(2026, 12, 31, 22, 0, tzinfo=CHICAGO)
        windows = parse_claude_usage(envelope(claude_line("session", "50", "Jan 1, 3am")), checked)
        assert windows[0].resets_at == "2027-01-01T03:00:00-06:00"

    def test_reset_just_after_new_year_stays_in_current_year(self) -> None:
        checked = datetime(2027, 1, 1, 1, 0, tzinfo=CHICAGO)
        windows = parse_claude_usage(envelope(claude_line("session", "50", "Jan 1, 3am")), checked)
        assert windows[0].resets_at == "2027-01-01T03:00:00-06:00"

    def test_parses_minutes_and_meridiem(self, checked_at: datetime) -> None:
        windows = parse_claude_usage(
            envelope(claude_line("session", "1", "Aug 5, 12:30pm")), checked_at
        )
        assert windows[0].resets_at == "2026-08-05T12:30:00-05:00"

    def test_midnight_is_hour_zero(self, checked_at: datetime) -> None:
        windows = parse_claude_usage(
            envelope(claude_line("session", "1", "Aug 5, 12am")), checked_at
        )
        assert windows[0].resets_at == "2026-08-05T00:00:00-05:00"

    def test_all_models_week_is_not_suffixed(self, checked_at: datetime) -> None:
        windows = parse_claude_usage(
            envelope(claude_line("week (all models)", "12", "Aug 11, 1am")), checked_at
        )
        assert windows[0].label == "Weekly"

    def test_out_of_range_percent_is_rejected(self, checked_at: datetime) -> None:
        """Better to show nothing than to clamp a misread number into something plausible."""
        windows = parse_claude_usage(
            envelope(claude_line("session", "137", "Aug 5, 1am")), checked_at
        )
        assert windows == ()

    def test_hundred_percent_is_kept(self, checked_at: datetime) -> None:
        windows = parse_claude_usage(
            envelope(claude_line("session", "100", "Aug 5, 1am")), checked_at
        )
        assert windows[0].used_percent == 100.0

    def test_window_kept_when_reset_is_unparseable(self, checked_at: datetime) -> None:
        """A bad timezone loses the timestamp, not the percentage."""
        line = "Current session: 3% used \u00b7 resets Aug 5, 1am (Mars/Olympus)"
        windows = parse_claude_usage(envelope(line), checked_at)
        assert len(windows) == 1
        assert windows[0].used_percent == 3.0
        assert windows[0].resets_at is None

    @pytest.mark.parametrize(
        "raw",
        [
            pytest.param("not json at all", id="malformed-json"),
            pytest.param("", id="empty"),
            pytest.param(json.dumps({"type": "result"}), id="missing-result"),
            pytest.param(json.dumps({"result": {"nested": True}}), id="result-not-a-string"),
            pytest.param(json.dumps([1, 2, 3]), id="not-an-object"),
            pytest.param(envelope("Session usage is now 40 percent of the limit"), id="reworded"),
            pytest.param(envelope("You are currently using your subscription"), id="no-windows"),
        ],
    )
    def test_fails_closed(self, raw: str, checked_at: datetime) -> None:
        assert parse_claude_usage(raw, checked_at) == ()


class TestCodexParsing:
    def test_parses_real_response_including_extra_buckets(self) -> None:
        """The default bucket alone would hide the model-specific Spark limit."""
        response = json.loads((FIXTURES / "codex_rate_limits.json").read_text(encoding="utf-8"))
        windows = parse_codex_rate_limits(response)

        assert [w.label for w in windows] == ["Weekly", "Weekly (GPT-5.3-Codex-Spark)"]
        assert [w.used_percent for w in windows] == [27.0, 0.0]
        assert windows[0].is_primary is True
        assert windows[0].resets_at is not None

    def test_labels_short_windows_as_session(self) -> None:
        windows = parse_codex_rate_limits(
            {
                "rateLimits": {
                    "limitId": "codex",
                    "primary": {"usedPercent": 42, "windowDurationMins": 300},
                    "secondary": {"usedPercent": 8, "windowDurationMins": 10080},
                }
            }
        )
        assert [w.label for w in windows] == ["Session", "Weekly"]
        assert windows[0].is_primary is True

    def test_falls_back_when_bucket_map_is_absent(self) -> None:
        windows = parse_codex_rate_limits(
            {"rateLimits": {"limitId": "codex", "primary": {"usedPercent": 5}}}
        )
        assert len(windows) == 1
        assert windows[0].label == "Session"

    def test_named_bucket_falls_back_to_limit_id(self) -> None:
        windows = parse_codex_rate_limits(
            {
                "rateLimits": {"limitId": "codex", "primary": {"usedPercent": 1}},
                "rateLimitsByLimitId": {
                    "codex": {"limitId": "codex", "primary": {"usedPercent": 1}},
                    "codex_mystery": {
                        "limitId": "codex_mystery",
                        "limitName": None,
                        "primary": {"usedPercent": 2},
                    },
                },
            }
        )
        assert [w.label for w in windows] == ["Session", "Session (codex_mystery)"]

    def test_bucket_order_is_stable(self) -> None:
        response = {
            "rateLimits": {"limitId": "codex", "primary": {"usedPercent": 1}},
            "rateLimitsByLimitId": {
                "zzz": {"limitId": "zzz", "limitName": "Zeta", "primary": {"usedPercent": 3}},
                "aaa": {"limitId": "aaa", "limitName": "Alpha", "primary": {"usedPercent": 2}},
                "codex": {"limitId": "codex", "primary": {"usedPercent": 1}},
            },
        }
        assert [w.label for w in parse_codex_rate_limits(response)] == [
            "Session",
            "Session (Alpha)",
            "Session (Zeta)",
        ]

    def test_absurd_reset_timestamp_is_dropped(self) -> None:
        windows = parse_codex_rate_limits(
            {"rateLimits": {"limitId": "codex", "primary": {"usedPercent": 5, "resetsAt": 1e30}}}
        )
        assert len(windows) == 1
        assert windows[0].resets_at is None

    @pytest.mark.parametrize(
        "response",
        [
            pytest.param(None, id="none"),
            pytest.param({}, id="empty"),
            pytest.param("nope", id="not-an-object"),
            pytest.param({"rateLimits": {"primary": None, "secondary": None}}, id="no-windows"),
            pytest.param(
                {"rateLimits": {"primary": {"usedPercent": "lots"}}}, id="percent-not-a-number"
            ),
            pytest.param(
                {"rateLimits": {"primary": {"usedPercent": True}}}, id="percent-is-a-bool"
            ),
            pytest.param(
                {"rateLimits": {"primary": {"usedPercent": 137}}}, id="percent-above-range"
            ),
            pytest.param(
                {"rateLimits": {"primary": {"usedPercent": -4}}}, id="percent-below-range"
            ),
            pytest.param(
                {"rateLimits": {"primary": {"usedPercent": float("nan")}}}, id="percent-not-finite"
            ),
        ],
    )
    def test_fails_closed(self, response: object) -> None:
        assert parse_codex_rate_limits(response) == ()

    def test_nonsense_window_duration_does_not_crash(self) -> None:
        windows = parse_codex_rate_limits(
            {
                "rateLimits": {
                    "limitId": "codex",
                    "primary": {"usedPercent": 5, "windowDurationMins": float("nan")},
                }
            }
        )
        assert len(windows) == 1
        assert windows[0].window_duration_mins is None

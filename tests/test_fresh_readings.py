"""A fresh reading never carries a window whose reset is at or before the probe time.

A vendor can keep reporting a window it has not rolled over yet, right after a weekly
reset. Cached readings were already pruned of such windows; these tests hold the same
line on every path a fresh reading takes: the parser entry point, the cache, and the
`--no-cache` CLI path.
"""

from __future__ import annotations

import json
import stat
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ai_usage import cache, cli, probes
from ai_usage.models import ProviderUsage, UsageWindow
from ai_usage.parsers import build_provider

NOW = datetime(2026, 8, 4, 20, 0, tzinfo=UTC)
FINISHED = (
    "every reported window has already reset; waiting for the provider to start the new period"
)


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))


class Recorder:
    """A probe stub that hands back canned readings for the providers it is asked for."""

    def __init__(self, *results: ProviderUsage) -> None:
        self.results = {result.id: result for result in results}

    def __call__(self, provider_ids: list[str]) -> list[ProviderUsage]:
        return [self.results[pid] for pid in provider_ids if pid in self.results]


def window(label: str, resets_at: datetime | None, primary: bool = False) -> UsageWindow:
    return UsageWindow(
        label=label,
        used_percent=40,
        window_duration_mins=10080,
        resets_at=resets_at.isoformat() if resets_at else None,
        is_primary=primary,
    )


def build(*windows: UsageWindow) -> ProviderUsage:
    return build_provider("codex", "Codex", "test", NOW, windows)


def test_fresh_reading_drops_a_window_that_already_reset() -> None:
    finished = window("Weekly", NOW - timedelta(minutes=1), primary=True)
    upcoming = window("Session", NOW + timedelta(hours=2))

    reading = build(finished, upcoming)

    assert reading.ok
    assert reading.windows == (upcoming,)


def test_window_resetting_exactly_at_probe_time_is_finished() -> None:
    reading = build(window("Weekly", NOW), window("Session", NOW + timedelta(seconds=1)))

    assert [w.label for w in reading.windows] == ["Session"]


def test_window_without_a_reset_time_is_kept() -> None:
    reading = build(window("Plan", None))

    assert reading.ok
    assert [w.label for w in reading.windows] == ["Plan"]


def test_reading_whose_windows_have_all_reset_fails_with_the_reason() -> None:
    reading = build(
        window("Weekly", NOW - timedelta(hours=1), primary=True),
        window("Session", NOW - timedelta(seconds=1)),
    )

    assert not reading.ok
    assert reading.windows == ()
    assert reading.error == FINISHED
    assert reading.checked_at == NOW.isoformat()


def test_failed_fresh_reading_serves_the_previous_one_as_stale_with_the_reason() -> None:
    week = window("Weekly", NOW + timedelta(days=1), primary=True)
    stored = build_provider("codex", "Codex", "test", NOW - timedelta(days=1), (week,))
    cache.resolve(["codex"], NOW - timedelta(days=1), Recorder(stored), ttl=300)

    probe = Recorder(build(window("Weekly", NOW - timedelta(minutes=5), primary=True)))

    result = cache.resolve(["codex"], NOW, probe, ttl=300)

    assert result[0].windows == (week,)
    assert result[0].stale is True
    assert result[0].error == FINISHED


def test_failed_fresh_reading_with_nothing_stored_is_unavailable() -> None:
    probe = Recorder(build(window("Weekly", NOW - timedelta(minutes=5), primary=True)))

    result = cache.resolve(["codex"], NOW, probe, ttl=300)

    assert result[0].has_reading is False
    assert result[0].error == FINISHED


def fake_codex(directory: Path, rate_limits: dict) -> None:
    """A stand-in `codex` executable: the I/O edge, speaking the app-server protocol."""
    script = directory / "codex"
    script.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        f"RESULT = {json.dumps(rate_limits)!r}\n"
        "for line in sys.stdin:\n"
        "    request = json.loads(line)\n"
        "    if request.get('id') == 1:\n"
        "        print(json.dumps({'id': 1, 'result': json.loads(RESULT)}), flush=True)\n"
        "        break\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)


def run_cli(monkeypatch, capsys, directory: Path, *args: str) -> tuple[int, dict]:
    monkeypatch.setenv("PATH", f"{directory}:/usr/bin:/bin")
    monkeypatch.setattr(probes, "_now", lambda: NOW)
    code = cli.main([*args, "--provider", "codex"])
    return code, json.loads(capsys.readouterr().out)["providers"][0]


def codex_response(primary_resets: datetime, secondary_resets: datetime) -> dict:
    return {
        "rateLimits": {
            "limitId": "codex",
            "primary": {
                "usedPercent": 60,
                "windowDurationMins": 300,
                "resetsAt": int(primary_resets.timestamp()),
            },
            "secondary": {
                "usedPercent": 30,
                "windowDurationMins": 10080,
                "resetsAt": int(secondary_resets.timestamp()),
            },
        }
    }


def test_no_cache_cli_prints_only_windows_that_have_not_reset(
    tmp_path, monkeypatch, capsys
) -> None:
    fake_codex(tmp_path, codex_response(NOW - timedelta(minutes=1), NOW + timedelta(days=2)))

    code, provider = run_cli(monkeypatch, capsys, tmp_path, "--no-cache")

    assert code == 0
    assert provider["ok"] is True
    assert [w["usedPercent"] for w in provider["windows"]] == [30]


def test_no_cache_cli_reports_unavailable_when_every_window_already_reset(
    tmp_path, monkeypatch, capsys
) -> None:
    fake_codex(tmp_path, codex_response(NOW - timedelta(minutes=1), NOW - timedelta(days=1)))

    code, provider = run_cli(monkeypatch, capsys, tmp_path, "--no-cache")

    assert code == 1
    assert provider["ok"] is False
    assert provider["windows"] == []
    assert provider["error"] == FINISHED


def test_cached_cli_path_applies_the_same_guard(tmp_path, monkeypatch, capsys) -> None:
    fake_codex(tmp_path, codex_response(NOW - timedelta(minutes=1), NOW + timedelta(days=2)))

    code, provider = run_cli(monkeypatch, capsys, tmp_path, "--refresh")

    assert code == 0
    assert [w["usedPercent"] for w in provider["windows"]] == [30]

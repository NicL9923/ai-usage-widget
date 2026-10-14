"""Cache behaviour tests.

The cache decides what the panel shows when a probe is skipped or fails, so the cases
below pin down exactly when a reading counts as current, stale, or absent.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from ai_usage import cache
from ai_usage.cli import render_plain
from ai_usage.models import BankedResets, ProviderUsage, ResetCredit, UsageWindow, failed

NOW = datetime(2026, 8, 4, 20, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    return tmp_path


def usage(provider_id: str, percent: float, checked_at: datetime) -> ProviderUsage:
    return ProviderUsage(
        id=provider_id,
        display_name=provider_id.title(),
        source="test",
        checked_at=checked_at.isoformat(),
        windows=(UsageWindow(label="Session", used_percent=percent, is_primary=True),),
    )


class Recorder:
    """A probe stub that records which providers it was actually asked to refresh."""

    def __init__(self, *results: ProviderUsage) -> None:
        self.results = {result.id: result for result in results}
        self.calls: list[list[str]] = []

    def __call__(self, provider_ids: list[str]) -> list[ProviderUsage]:
        self.calls.append(list(provider_ids))
        return [self.results[pid] for pid in provider_ids if pid in self.results]


def test_probes_when_cache_is_empty() -> None:
    probe = Recorder(usage("codex", 27, NOW))
    result = cache.resolve(["codex"], NOW, probe, ttl=300)

    assert probe.calls == [["codex"]]
    assert result[0].windows[0].used_percent == 27
    assert result[0].stale is False


def test_cache_hit_within_ttl_skips_the_probe_and_is_not_stale() -> None:
    """A hit inside the TTL is current data; marking it stale would be a lie."""
    cache.resolve(["codex"], NOW, Recorder(usage("codex", 27, NOW)), ttl=300)

    probe = Recorder(usage("codex", 99, NOW))
    later = NOW + timedelta(seconds=120)
    result = cache.resolve(["codex"], later, probe, ttl=300)

    assert probe.calls == []
    assert result[0].windows[0].used_percent == 27
    assert result[0].stale is False


def test_banked_resets_survive_cache_round_trip() -> None:
    codex = usage("codex", 27, NOW)
    codex = ProviderUsage(
        id=codex.id,
        display_name=codex.display_name,
        source=codex.source,
        checked_at=codex.checked_at,
        windows=codex.windows,
        banked_resets=BankedResets(
            available_count=1,
            credits=(ResetCredit(title="Full reset", expires_at="2026-09-20T12:00:00Z"),),
        ),
    )

    cache.resolve(["codex"], NOW, Recorder(codex), ttl=300)
    cached = cache.load()["codex"].banked_resets

    assert cached is not None
    assert cached.available_count == 1
    assert cached.credits[0].title == "Full reset"


def test_expired_cache_triggers_a_probe() -> None:
    cache.resolve(["codex"], NOW, Recorder(usage("codex", 27, NOW)), ttl=300)

    later = NOW + timedelta(seconds=400)
    probe = Recorder(usage("codex", 44, later))
    result = cache.resolve(["codex"], later, probe, ttl=300)

    assert probe.calls == [["codex"]]
    assert result[0].windows[0].used_percent == 44
    assert result[0].stale is False


def test_failed_refresh_serves_last_good_reading_as_stale() -> None:
    cache.resolve(["codex"], NOW, Recorder(usage("codex", 27, NOW)), ttl=300)

    later = NOW + timedelta(seconds=400)
    probe = Recorder(failed("codex", "Codex", later.isoformat(), "offline"))
    result = cache.resolve(["codex"], later, probe, ttl=300)

    assert result[0].windows[0].used_percent == 27
    assert result[0].stale is True
    assert result[0].error == "offline"
    assert result[0].ok is False
    assert result[0].has_reading is True
    serialized = result[0].to_dict()
    assert serialized["ok"] is False
    assert serialized["hasReading"] is True


def windowed(provider_id: str, checked_at: datetime, *windows: UsageWindow) -> ProviderUsage:
    return ProviderUsage(
        id=provider_id,
        display_name=provider_id.title(),
        source="test",
        checked_at=checked_at.isoformat(),
        windows=windows,
    )


def test_reading_from_a_finished_period_is_never_served() -> None:
    """A stale percentage from before the reset describes a period that is over.

    This is the signed-out Grok case: the last good reading said 4% of a week that ended
    weeks ago, and the panel kept drawing it.
    """
    week = UsageWindow("Weekly", 4, 10080, (NOW + timedelta(days=1)).isoformat(), True)
    cache.resolve(["grok"], NOW, Recorder(windowed("grok", NOW, week)), ttl=300)

    later = NOW + timedelta(days=2)
    probe = Recorder(failed("grok", "Grok", later.isoformat(), "signed out"))
    result = cache.resolve(["grok"], later, probe, ttl=300)

    assert result[0].has_reading is False
    assert result[0].error == "signed out"


def test_stale_reading_keeps_only_windows_that_have_not_reset() -> None:
    session = UsageWindow("Session", 80, 300, (NOW + timedelta(hours=1)).isoformat(), True)
    week = UsageWindow("Weekly", 30, 10080, (NOW + timedelta(days=3)).isoformat())
    cache.resolve(["claude"], NOW, Recorder(windowed("claude", NOW, session, week)), ttl=300)

    later = NOW + timedelta(hours=2)
    probe = Recorder(failed("claude", "Claude", later.isoformat(), "offline"))
    result = cache.resolve(["claude"], later, probe, ttl=300)

    assert [window.label for window in result[0].windows] == ["Weekly"]
    assert result[0].stale is True
    assert result[0].primary_window == week


def test_failed_refresh_after_a_reset_is_retried_on_the_next_call() -> None:
    """Serving a pruned reading must not persist it as if the refresh had succeeded."""
    session = UsageWindow("Session", 80, 300, (NOW + timedelta(seconds=60)).isoformat(), True)
    week = UsageWindow("Weekly", 30, 10080, (NOW + timedelta(days=3)).isoformat())
    cache.resolve(["claude"], NOW, Recorder(windowed("claude", NOW, session, week)), ttl=300)

    later = NOW + timedelta(seconds=120)
    offline = Recorder(failed("claude", "Claude", later.isoformat(), "offline"))
    cache.resolve(["claude"], later, offline, ttl=300)

    again = later + timedelta(seconds=30)
    probe = Recorder(failed("claude", "Claude", again.isoformat(), "still offline"))
    result = cache.resolve(["claude"], again, probe, ttl=300)

    assert probe.calls == [["claude"]]
    assert result[0].stale is True
    assert result[0].error == "still offline"


def test_other_provider_call_keeps_a_reset_reading_due() -> None:
    session = UsageWindow("Session", 80, 300, (NOW + timedelta(seconds=60)).isoformat(), True)
    week = UsageWindow("Weekly", 30, 10080, (NOW + timedelta(days=3)).isoformat())
    claude = windowed("claude", NOW, session, week)
    cache.resolve(["claude", "codex"], NOW, Recorder(claude, usage("codex", 5, NOW)), ttl=300)

    later = NOW + timedelta(seconds=120)
    cache.resolve(["codex"], later, Recorder(), ttl=300)

    probe = Recorder(usage("claude", 2, later))
    cache.resolve(["claude"], later, probe, ttl=300)

    assert probe.calls == [["claude"]]


def test_window_reset_inside_ttl_triggers_a_probe() -> None:
    session = UsageWindow("Session", 80, 300, (NOW + timedelta(seconds=60)).isoformat(), True)
    cache.resolve(["claude"], NOW, Recorder(windowed("claude", NOW, session)), ttl=300)

    later = NOW + timedelta(seconds=120)
    probe = Recorder(usage("claude", 2, later))
    result = cache.resolve(["claude"], later, probe, ttl=300)

    assert probe.calls == [["claude"]]
    assert result[0].windows[0].used_percent == 2


def test_stale_reading_serializes_with_its_refresh_error() -> None:
    """Consumers can render the last reading while clearly marking it stale."""
    cache.resolve(["codex"], NOW, Recorder(usage("codex", 27, NOW)), ttl=300)
    later = NOW + timedelta(seconds=400)
    result = cache.resolve(
        ["codex"], later, Recorder(failed("codex", "Codex", later.isoformat(), "offline")), ttl=300
    )

    payload = json.loads(json.dumps(result[0].to_dict()))
    assert payload == {
        "id": "codex",
        "displayName": "Codex",
        "ok": False,
        "hasReading": True,
        "error": "offline",
        "stale": True,
        "source": "test",
        "checkedAt": NOW.isoformat(),
        "primaryUsedPercent": 27,
        "primaryLabel": "Session",
        "maxUsedPercent": 27,
        "windows": [
            {
                "label": "Session",
                "usedPercent": 27,
                "windowDurationMins": None,
                "resetsAt": None,
                "isPrimary": True,
            }
        ],
        "bankedResets": None,
    }


def test_plain_output_keeps_stale_windows_and_refresh_error() -> None:
    stale = ProviderUsage(
        id="codex",
        display_name="Codex",
        source="test",
        checked_at=NOW.isoformat(),
        windows=(UsageWindow(label="Session", used_percent=27, is_primary=True),),
        error="offline",
        stale=True,
    )

    assert render_plain([stale]) == (
        "Codex (stale):\n  Refresh failed: offline\n  Session                       27.0% used"
    )


def test_stale_entry_is_retried_on_the_next_call() -> None:
    """A stale reading keeps its original timestamp, so it stays due for a refresh."""
    cache.resolve(["codex"], NOW, Recorder(usage("codex", 27, NOW)), ttl=300)
    later = NOW + timedelta(seconds=400)
    cache.resolve(
        ["codex"], later, Recorder(failed("codex", "Codex", later.isoformat(), "x")), ttl=300
    )

    even_later = later + timedelta(seconds=10)
    probe = Recorder(usage("codex", 31, even_later))
    result = cache.resolve(["codex"], even_later, probe, ttl=300)

    assert probe.calls == [["codex"]]
    assert result[0].windows[0].used_percent == 31
    assert result[0].stale is False


def test_force_refresh_ignores_a_fresh_cache() -> None:
    cache.resolve(["codex"], NOW, Recorder(usage("codex", 27, NOW)), ttl=300)

    probe = Recorder(usage("codex", 55, NOW))
    result = cache.resolve(["codex"], NOW, probe, ttl=300, force=True)

    assert probe.calls == [["codex"]]
    assert result[0].windows[0].used_percent == 55


def test_one_provider_failing_does_not_affect_the_other() -> None:
    probe = Recorder(usage("codex", 27, NOW), failed("claude", "Claude", NOW.isoformat(), "boom"))
    result = cache.resolve(["codex", "claude"], NOW, probe, ttl=300)

    by_id = {provider.id: provider for provider in result}
    assert by_id["codex"].ok is True
    assert by_id["claude"].ok is False
    assert by_id["claude"].error == "boom"


def test_only_expired_providers_are_probed() -> None:
    cache.resolve(
        ["codex", "claude"],
        NOW,
        Recorder(usage("codex", 27, NOW), usage("claude", 3, NOW)),
        ttl=300,
    )

    # Age out only codex by rewriting its timestamp far enough into the past.
    later = NOW + timedelta(seconds=400)
    entries = cache.load()
    entries["claude"] = usage("claude", 3, later)
    cache.save(entries)

    probe = Recorder(usage("codex", 33, later))
    result = cache.resolve(["codex", "claude"], later, probe, ttl=300)

    assert probe.calls == [["codex"]]
    by_id = {provider.id: provider for provider in result}
    assert by_id["codex"].windows[0].used_percent == 33
    assert by_id["claude"].windows[0].used_percent == 3
    assert by_id["claude"].stale is False


def test_corrupt_cache_file_is_ignored() -> None:
    cache.cache_dir().mkdir(parents=True, exist_ok=True)
    cache.cache_path().write_text("{ not json", encoding="utf-8")

    probe = Recorder(usage("codex", 27, NOW))
    result = cache.resolve(["codex"], NOW, probe, ttl=300)

    assert probe.calls == [["codex"]]
    assert result[0].windows[0].used_percent == 27


def test_failures_are_never_persisted() -> None:
    cache.resolve(
        ["codex"], NOW, Recorder(failed("codex", "Codex", NOW.isoformat(), "nope")), ttl=300
    )
    assert cache.load() == {}


def test_narrow_request_keeps_other_providers_cached() -> None:
    """Asking for one provider must not evict the other's last good reading."""
    cache.resolve(
        ["codex", "claude"],
        NOW,
        Recorder(usage("codex", 27, NOW), usage("claude", 24, NOW)),
        ttl=300,
    )

    later = NOW + timedelta(minutes=10)
    cache.resolve(["codex"], later, Recorder(usage("codex", 31, later)), ttl=300)

    stored = cache.load()
    assert set(stored) == {"codex", "claude"}
    assert stored["claude"].windows[0].used_percent == 24
    assert stored["codex"].windows[0].used_percent == 31

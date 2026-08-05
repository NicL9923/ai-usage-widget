"""Cache behaviour tests.

The cache decides what the panel shows when a probe is skipped or fails, so the cases
below pin down exactly when a reading counts as current, stale, or absent.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ai_usage import cache
from ai_usage.models import ProviderUsage, UsageWindow, failed

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

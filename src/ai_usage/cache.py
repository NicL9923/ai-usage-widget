"""TTL cache so panel polling does not spawn a CLI on every tick.

Successful probes are cached for `ttl` seconds. Failures are never cached, but the last
good reading is retained and served with `stale: true` so a transient hiccup blanks a
timestamp rather than the whole widget.
"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from .models import CONTRACT_VERSION, ProviderUsage

DEFAULT_TTL_SECONDS = 300.0


def cache_dir() -> Path:
    root = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(root) / "ai-usage-widget"


def cache_path() -> Path:
    return cache_dir() / "usage.json"


@contextmanager
def _lock() -> Iterator[None]:
    """Serialize refreshes so a manual run and a panel tick cannot double-probe."""
    directory = cache_dir()
    directory.mkdir(parents=True, exist_ok=True)
    handle = (directory / "refresh.lock").open("w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield
    finally:
        with suppress(OSError):
            fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


def load() -> dict[str, ProviderUsage]:
    path = cache_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict) or raw.get("version") != CONTRACT_VERSION:
        return {}
    entries = raw.get("providers")
    if not isinstance(entries, dict):
        return {}

    cached: dict[str, ProviderUsage] = {}
    for provider_id, entry in entries.items():
        if not isinstance(entry, dict):
            continue
        with suppress(KeyError, TypeError, ValueError):
            cached[provider_id] = ProviderUsage.from_dict(entry)
    return cached


def save(entries: dict[str, ProviderUsage]) -> None:
    directory = cache_dir()
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        return

    payload = {
        "version": CONTRACT_VERSION,
        "providers": {pid: usage.to_dict() for pid, usage in entries.items() if usage.ok},
    }
    # Write atomically so a concurrent reader never sees a truncated file.
    temp_path: Path | None = None
    try:
        handle_fd, raw_temp = tempfile.mkstemp(dir=directory, prefix=".usage-", suffix=".json")
        temp_path = Path(raw_temp)
        with os.fdopen(handle_fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=1)
            handle.flush()
            os.fsync(handle.fileno())
        temp_path.replace(cache_path())
    except OSError:
        if temp_path is not None:
            with suppress(OSError):
                temp_path.unlink()


def age_seconds(usage: ProviderUsage, now: datetime) -> float:
    try:
        checked = datetime.fromisoformat(usage.checked_at)
    except (TypeError, ValueError):
        return float("inf")
    if checked.tzinfo is None:
        checked = checked.astimezone()
    return (now - checked).total_seconds()


def resolve(
    provider_ids: list[str],
    now: datetime,
    probe: Callable[[list[str]], list[ProviderUsage]],
    ttl: float = DEFAULT_TTL_SECONDS,
    force: bool = False,
) -> list[ProviderUsage]:
    """Return usage for each provider, probing only what the cache cannot satisfy."""
    with _lock():
        cached = load()
        needed = [
            pid
            for pid in provider_ids
            if force
            or pid not in cached
            or not cached[pid].ok
            or age_seconds(cached[pid], now) >= ttl
        ]

        fresh = {usage.id: usage for usage in probe(needed)} if needed else {}

        merged: dict[str, ProviderUsage] = {}
        for pid in provider_ids:
            probed = fresh.get(pid)
            previous = cached.get(pid)
            if probed is not None and probed.ok:
                merged[pid] = probed
            elif previous is not None and previous.ok:
                # A cache hit inside the TTL is current, not stale. Only a reading we
                # tried and failed to refresh gets the stale marker, along with why.
                attempted = pid in needed
                merged[pid] = replace(
                    previous,
                    stale=attempted,
                    error=probed.error if attempted and probed is not None else None,
                )
            elif probed is not None:
                merged[pid] = probed

        # Persist last-good readings without the stale/error decoration. They keep their
        # original `checkedAt`, so a stale entry stays expired and is retried next tick.
        # Providers outside this call's scope keep whatever the cache already held.
        persisted = {pid: usage for pid, usage in cached.items() if usage.windows}
        for pid, usage in merged.items():
            if usage.windows:
                persisted[pid] = replace(usage, stale=False, error=None)
            else:
                persisted.pop(pid, None)
        save(persisted)
        return [merged[pid] for pid in provider_ids if pid in merged]

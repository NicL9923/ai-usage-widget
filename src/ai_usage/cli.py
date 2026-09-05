"""Command line entrypoint. Emits the JSON contract the plasmoid renders."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime

from . import cache
from .models import CONTRACT_VERSION, ProviderUsage
from .probes import CLAUDE_ID, CODEX_ID, DEFAULT_TIMEOUT, GROK_ID, probe_all

ALL_PROVIDERS = [CODEX_ID, CLAUDE_ID, GROK_ID]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ai-usage",
        description="Report Claude Code, Codex, and Grok subscription usage limits as JSON.",
    )
    parser.add_argument(
        "--provider",
        action="append",
        choices=ALL_PROVIDERS,
        dest="providers",
        help="Limit to a provider (repeatable). Defaults to all.",
    )
    parser.add_argument(
        "--ttl",
        type=float,
        default=cache.DEFAULT_TTL_SECONDS,
        help=f"Cache lifetime in seconds (default: {cache.DEFAULT_TTL_SECONDS:g}).",
    )
    parser.add_argument("--refresh", action="store_true", help="Ignore the cache and probe now.")
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"Per-probe timeout in seconds (default: {DEFAULT_TIMEOUT:g}).",
    )
    parser.add_argument(
        "--plain", action="store_true", help="Human readable output instead of JSON."
    )
    parser.add_argument("--no-cache", action="store_true", help="Do not read or write the cache.")
    return parser


def render_plain(providers: list[ProviderUsage]) -> str:
    lines: list[str] = []
    for provider in providers:
        if not provider.has_reading:
            lines.append(f"{provider.display_name}: unavailable ({provider.error})")
            continue
        suffix = " (stale)" if provider.stale else ""
        lines.append(f"{provider.display_name}{suffix}:")
        if provider.error:
            lines.append(f"  Refresh failed: {provider.error}")
        if provider.banked_resets is not None:
            lines.append(
                f"  {'Banked resets':<28} {provider.banked_resets.available_count} available"
            )
            for credit in provider.banked_resets.credits:
                expiry = f"  expires {credit.expires_at}" if credit.expires_at else ""
                lines.append(f"    {credit.title}{expiry}")
        for window in provider.windows:
            resets = f"  resets {window.resets_at}" if window.resets_at else ""
            lines.append(f"  {window.label:<28} {window.used_percent:5.1f}% used{resets}")
    return "\n".join(lines) if lines else "No providers available."


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    providers = args.providers or ALL_PROVIDERS
    now = datetime.now(UTC).astimezone()

    def probe(ids: list[str]) -> list[ProviderUsage]:
        return probe_all(ids, timeout=args.timeout)

    if args.no_cache:
        results = probe(providers)
    else:
        results = cache.resolve(providers, now, probe, ttl=args.ttl, force=args.refresh)

    if args.plain:
        print(render_plain(results))
    else:
        json.dump(
            {
                "version": CONTRACT_VERSION,
                "generatedAt": now.isoformat(),
                "providers": [provider.to_dict() for provider in results],
            },
            sys.stdout,
        )
        sys.stdout.write("\n")

    # Exit non-zero only when nothing at all could be reported, so the widget can
    # distinguish "one provider is down" from "the helper is broken".
    return 0 if any(provider.has_reading for provider in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

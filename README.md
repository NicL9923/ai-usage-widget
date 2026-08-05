<div align="center">

# AI Usage

**Live Claude Code and Codex usage limits in your KDE Plasma panel.**

[![CI](https://github.com/NicL9923/ai-usage-widget/actions/workflows/ci.yml/badge.svg)](https://github.com/NicL9923/ai-usage-widget/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Plasma 6](https://img.shields.io/badge/Plasma-6-1d99f3.svg)](https://kde.org/plasma-desktop/)

![The widget in a Plasma panel: a ring and percentage for each provider](docs/panel.png)

</div>

Stop finding out you are rate limited by getting rate limited. Each provider gets a ring in
the panel that fills as you burn through your plan, turning orange at 70% and red at 85%.

## Contents

- [Why](#why)
- [Features](#features)
- [Requirements](#requirements)
- [Install](#install)
- [Configuration](#configuration)
- [The ai-usage helper](#the-ai-usage-helper)
- [How it works](#how-it-works)
- [Troubleshooting](#troubleshooting)
- [Uninstall](#uninstall)
- [Contributing](#contributing)

## Why

Both vendors have a usage endpoint, and both are undocumented and behind OAuth. Talking to
them directly means storing, reading, and refreshing somebody else's access tokens, and
re-fixing it every time they change something.

So this doesn't. It asks the CLIs you already have signed in, and lets them own
authentication. Both calls are free — no tokens, no model calls, nothing on your bill.

| Provider | How it's read | Cost |
| --- | --- | --- |
| Codex | `codex app-server` → JSON-RPC `account/rateLimits/read` | none |
| Claude Code | `claude --print /usage --output-format json` | none |

## Features

- **Every window, not just the headline one.** Session and weekly limits, plus per-model
  buckets like `GPT-5.3-Codex-Spark` and `Weekly (Fable)` that are easy to miss.
- **Color coded at a glance** — normal, orange from 70%, red from 85%, and the panel's
  "needs attention" state from 85%.
- **Reset times**, so you know whether to wait ten minutes or switch models.
- **Cheap to poll.** Readings are cached on disk, so the widget can tick as often as you
  like without re-running the CLIs.
- **Degrades honestly.** If one provider fails the other still updates, the last good
  reading stays on screen marked stale, and output that can't be parsed is dropped rather
  than guessed at.
- **A usable CLI on its own** — `ai-usage` prints JSON or plain text for your own scripts,
  status bars, and prompts.
- Theme aware; works in horizontal and vertical panels and on the desktop.

![The expanded view listing every rate-limit window with a progress bar and reset time](docs/popup.png)

## Requirements

- KDE Plasma 6
- Python 3.11 or newer
- [Codex CLI](https://github.com/openai/codex) and/or
  [Claude Code](https://claude.com/claude-code), signed in to a subscription plan

You only need one of the two. Accounts on API-key, Bedrock, or usage-based billing have no
subscription windows to report, so those providers show as unavailable instead of showing a
number that doesn't mean anything.

## Install

```bash
git clone https://github.com/NicL9923/ai-usage-widget
cd ai-usage-widget
./scripts/install.sh
```

That installs an `ai-usage` shim into `~/.local/bin` and registers the applet with
`kpackagetool6`. Then right-click your panel → **Add widgets** → **AI Usage**.

If the widget doesn't show up in the list, restart the shell:

```bash
systemctl --user restart plasma-plasmashell.service
```

To pull updates: `git pull && ./scripts/install.sh --restart-plasma`.

## Configuration

Right-click the widget → **Configure AI Usage**:

| Setting | Default | What it does |
| --- | --- | --- |
| Providers | both | Which providers to query at all |
| Check every | 5 minutes | How often to refresh |
| Show provider logos | on | Draws the provider mark inside the ring |
| Show provider names | off | Prefixes each percentage with the provider name |
| Helper command | `$HOME/.local/bin/ai-usage` | Path to the helper |

Middle-click the widget to force a refresh.

## The ai-usage helper

The widget is a thin shell over a standalone script that is useful on its own:

```bash
ai-usage                    # JSON, for scripts and status bars
ai-usage --plain            # human readable
ai-usage --refresh          # ignore the cache
ai-usage --provider codex   # just one provider
ai-usage --ttl 600          # accept a cached reading up to 10 minutes old
```

The JSON contract is versioned:

```json
{
  "version": 1,
  "generatedAt": "2026-08-04T21:15:00-05:00",
  "providers": [
    {
      "id": "codex",
      "displayName": "Codex",
      "ok": true,
      "stale": false,
      "primaryUsedPercent": 27.0,
      "maxUsedPercent": 27.0,
      "windows": [
        {
          "label": "Weekly",
          "usedPercent": 27.0,
          "windowDurationMins": 10080,
          "resetsAt": "2026-08-08T08:58:17-05:00",
          "isPrimary": true
        }
      ]
    }
  ]
}
```

It needs nothing beyond the Python standard library, so you can also run it straight out of
a clone: `PYTHONPATH=src python3 -m ai_usage`.

## How it works

```
plasmoid (QML)  ──polls──>  ai-usage  ──>  disk cache (TTL)
                                │
                                ├──>  codex app-server   (JSON-RPC)
                                └──>  claude --print /usage
```

The helper probes both providers concurrently, normalizes what comes back, and caches it
under `$XDG_CACHE_HOME/ai-usage-widget/`. The widget polls the helper on a timer; between
TTL expiries that is a cache read, not a CLI run.

Codex returns structured data. Claude does not — it returns English prose that has to be
scraped, including reset dates printed without a year. That parser is the one place this
project could be quietly *wrong* rather than merely unavailable, so it fails closed:
anything unrecognized or out of range yields no bar instead of a plausible-looking wrong
number.

Every `claude --print` run also leaves a session transcript behind in `~/.claude/projects/`.
The probe runs in a throwaway directory and removes only what it created, so polling won't
slowly fill your disk or skew your local activity stats.

## Troubleshooting

**The widget shows a warning icon.** Hover it — the tooltip carries the helper's error.
Usually the helper path is wrong, or `~/.local/bin` isn't on your `PATH`.

**A provider says "unavailable".** Run the probe by hand to see the real error:

```bash
ai-usage --provider claude --refresh --plain
```

If the CLI itself can't report limits, neither can this. Check `claude /usage` or your Codex
plan directly.

**The numbers look frozen.** They're cached. Middle-click the widget, or run
`ai-usage --refresh`.

**Nothing renders after an update.** Check for QML errors:

```bash
journalctl --user --since "-5 min" | grep -i qml
```

## Uninstall

```bash
kpackagetool6 -t Plasma/Applet -r com.nicl9923.aiusage
rm -f ~/.local/bin/ai-usage
rm -rf "${XDG_CACHE_HOME:-$HOME/.cache}/ai-usage-widget"
```

## Contributing

Issues and PRs are welcome, especially parser fixes when a vendor changes its output.
[AGENTS.md](AGENTS.md) has the repo map, conventions, and verification steps.

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
git config core.hooksPath .githooks
.venv/bin/python -m pytest
```

## Credits

The CLI-based approach is adapted from
[pingdotgg/t3code#4326](https://github.com/pingdotgg/t3code/pull/4326).

Provider marks come from [Simple Icons](https://simpleicons.org/) (CC0-1.0). OpenAI, Codex,
Anthropic, and Claude are trademarks of their respective owners and are used here only to
identify which service each reading belongs to. This project is not affiliated with,
endorsed by, or sponsored by either company.

## License

[MIT](LICENSE)

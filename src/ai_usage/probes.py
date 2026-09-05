"""Subprocess probes that read live usage from the Claude, Codex, and Grok CLIs.

Every probe is free: they consume no tokens and make no model calls. We shell out to
the vendor CLIs on purpose rather than calling the undocumented HTTP usage endpoints
directly, so that OAuth storage, token refresh, and endpoint churn stay the vendors'
problem instead of ours.
"""

from __future__ import annotations

import json
import os
import queue
import re
import shutil
import signal
import subprocess
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import ProviderUsage, failed
from .parsers import (
    build_provider,
    parse_claude_usage,
    parse_codex_banked_resets,
    parse_codex_rate_limits,
    parse_grok_billing,
)

CLAUDE_ID = "claude"
CLAUDE_NAME = "Claude Code"
CODEX_ID = "codex"
CODEX_NAME = "Codex"
GROK_ID = "grok"
GROK_NAME = "Grok"

# Grok namespaces its ACP extensions under a leading underscore, per the protocol's
# convention for unstable methods. Without it the agent answers "Method not found".
GROK_BILLING_METHOD = "_x.ai/billing"

DEFAULT_TIMEOUT = 25.0

# Claude session ids are UUIDs. Anything else is not a name we will act on.
SESSION_ID_RE = re.compile(r"[0-9a-fA-F-]{8,64}")


def _now() -> datetime:
    return datetime.now(UTC).astimezone()


def _claude_config_dir() -> Path:
    override = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(override) if override else Path.home() / ".claude"


def _claude_projects_dir() -> Path:
    return _claude_config_dir() / "projects"


def _project_dirs() -> set[Path]:
    projects = _claude_projects_dir()
    if not projects.is_dir():
        return set()
    try:
        return {entry for entry in projects.iterdir() if entry.is_dir()}
    except OSError:
        return set()


def _slug(value: str) -> str:
    """Normalize to the alphabet Claude's project-dir slug survives.

    Claude maps both `/` and `_` (and possibly more) to `-`, so rather than trying to
    reimplement its slug we collapse every non-alphanumeric run on both sides and
    compare that.
    """
    return re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").lower()


def _purge_claude_session(session_id: str | None, probe_cwd: Path, preexisting: set[Path]) -> None:
    """Delete the transcript Claude writes for the probe.

    Every `claude --print` invocation persists a session JSONL under
    `<config>/projects/<slugified-cwd>/`. At widget polling cadence that would leave
    hundreds of junk transcripts per day and inflate the local activity stats, so we
    clean up after ourselves.

    Only directories that did not exist before this probe are ever removed, so a bug in
    the matching cannot touch a real project's history.
    """
    projects = _claude_projects_dir()
    if not projects.is_dir():
        return

    wanted = _slug(str(probe_cwd))
    for candidate in _project_dirs() - preexisting:
        matches_cwd = _slug(candidate.name) == wanted
        holds_session = bool(session_id) and (candidate / f"{session_id}.jsonl").exists()
        if matches_cwd or holds_session:
            shutil.rmtree(candidate, ignore_errors=True)

    # If the probe's directory already existed (a rerun inside the same tick), the
    # transcript is still in it. Match the directory by the probe's own temp-dir slug and
    # delete the exact file — never glob the session id, which is CLI-supplied and could
    # carry a wildcard that reaches into a real project's history.
    if session_id and SESSION_ID_RE.fullmatch(session_id):
        for candidate in _project_dirs():
            if _slug(candidate.name) == wanted:
                (candidate / f"{session_id}.jsonl").unlink(missing_ok=True)


@contextmanager
def _probe_workspace() -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="ai-usage-probe-") as raw:
        yield Path(raw).resolve()


def probe_claude(executable: str = "claude", timeout: float = DEFAULT_TIMEOUT) -> ProviderUsage:
    checked_at = _now()
    if shutil.which(executable) is None:
        return failed(CLAUDE_ID, CLAUDE_NAME, checked_at.isoformat(), "claude CLI not found")

    environment = {
        **os.environ,
        # Keep the probe hermetic: no MCP servers, no project config, no model call.
        "ENABLE_CLAUDEAI_MCP_SERVERS": "false",
    }
    command = [
        executable,
        "--print",
        "/usage",
        "--output-format",
        "json",
        "--permission-mode",
        "plan",
        "--strict-mcp-config",
        "--mcp-config",
        '{"mcpServers":{}}',
    ]

    session_id: str | None = None
    with _probe_workspace() as workspace:
        preexisting = _project_dirs()
        try:
            completed = subprocess.run(
                command,
                cwd=workspace,
                env=environment,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            _purge_claude_session(None, workspace, preexisting)
            return failed(
                CLAUDE_ID, CLAUDE_NAME, checked_at.isoformat(), f"timed out after {timeout:g}s"
            )
        except OSError as error:
            return failed(CLAUDE_ID, CLAUDE_NAME, checked_at.isoformat(), str(error))

        try:
            envelope = json.loads(completed.stdout)
            if isinstance(envelope, dict) and isinstance(envelope.get("session_id"), str):
                session_id = envelope["session_id"]
        except (json.JSONDecodeError, TypeError):
            session_id = None
        _purge_claude_session(session_id, workspace, preexisting)

    if completed.returncode != 0:
        detail = (completed.stderr or "").strip().splitlines()
        message = detail[-1] if detail else f"exited with code {completed.returncode}"
        return failed(CLAUDE_ID, CLAUDE_NAME, checked_at.isoformat(), message)

    windows = parse_claude_usage(completed.stdout, checked_at)
    if not windows:
        return failed(
            CLAUDE_ID,
            CLAUDE_NAME,
            checked_at.isoformat(),
            "no usage windows in output (subscription required, or output format changed)",
        )
    return build_provider(CLAUDE_ID, CLAUDE_NAME, "claudePrint", checked_at, windows)


def _stdio_jsonrpc_request(
    command: list[str],
    messages: tuple[dict[str, Any], ...],
    timeout: float,
    response_id: int = 1,
) -> tuple[Any | None, str | None]:
    """Run one JSON-RPC exchange against a CLI that speaks JSON-RPC over stdio.

    Both `codex app-server` and `grok agent stdio` exit as soon as stdin closes, so the
    request has to be issued over a live pipe and the response read incrementally rather
    than via `communicate()`. `messages` is written in order; the first response
    carrying `response_id` is returned and everything else (notifications, handshake
    replies) is ignored.
    """
    program = command[0]
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            # The probe owns its session, so cleanup can also stop any helper process
            # that inherited its stdout pipe without touching interactive CLI sessions.
            start_new_session=True,
        )
    except OSError as error:
        return None, str(error)

    stdout, stdin = process.stdout, process.stdin
    if stdout is None or stdin is None:  # pragma: no cover - configured pipes always exist
        _stop_jsonrpc_process(process, stdin, stdout, None)
        return None, f"{program} pipes unavailable"

    responses: queue.Queue[tuple[str, Any]] = queue.Queue()

    def pump() -> None:
        try:
            for line in stdout:
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(message, dict) and message.get("id") == response_id:
                    responses.put(("response", message))
                    return
        finally:
            # EOF is a result: without this signal a CLI that exits before replying
            # makes the panel wait for the full timeout on every refresh.
            responses.put(("eof", None))

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()

    response: Any | None = None
    error: str | None = None
    try:
        for message in messages:
            stdin.write(json.dumps(message) + "\n")
            stdin.flush()
    except (OSError, ValueError) as write_error:
        error = f"{program} closed early ({write_error})"
    else:
        try:
            kind, response = responses.get(timeout=timeout)
            if kind == "eof":
                error = f"{program} exited before responding"
        except queue.Empty:
            error = f"timed out after {timeout:g}s"
    finally:
        _stop_jsonrpc_process(process, stdin, stdout, reader)

    if error is not None:
        return None, error

    if not isinstance(response, dict):  # pragma: no cover - reader only queues dictionaries
        return None, f"{program} returned an invalid response"
    if "error" in response:
        detail = response["error"]
        text = detail.get("message") if isinstance(detail, dict) else str(detail)
        return None, str(text or f"{program} returned an error")
    return response.get("result"), None


def _stop_jsonrpc_process(
    process: subprocess.Popen[str],
    stdin: Any,
    stdout: Any,
    reader: threading.Thread | None,
) -> None:
    """Stop the probe's process group and release its pipes without blocking on I/O."""
    if stdin is not None:
        with suppress(OSError, ValueError):
            stdin.close()

    # A CLI can spawn descendants that inherit stdout. Killing only `process` leaves
    # that pipe open and can make `stdout.close()` wait forever on the reader's I/O
    # lock. `start_new_session=True` gives this short-lived probe an isolated group.
    with suppress(OSError):
        os.killpg(process.pid, signal.SIGKILL)
    with suppress(OSError, subprocess.TimeoutExpired):  # pragma: no cover - kill normally reaps it
        process.wait(timeout=5)
    if reader is not None:
        reader.join(timeout=5)
        if reader.is_alive():  # pragma: no cover - SIGKILL closes every owned pipe
            return
    if stdout is not None:
        with suppress(OSError, ValueError):
            stdout.close()


def probe_codex(executable: str = "codex", timeout: float = DEFAULT_TIMEOUT) -> ProviderUsage:
    checked_at = _now()
    if shutil.which(executable) is None:
        return failed(CODEX_ID, CODEX_NAME, checked_at.isoformat(), "codex CLI not found")

    result, error = _stdio_jsonrpc_request(
        [executable, "app-server"],
        (
            {
                "id": 0,
                "method": "initialize",
                "params": {
                    "clientInfo": {
                        "name": "ai-usage-widget",
                        "title": "AI Usage Widget",
                        "version": "0.1.0",
                    }
                },
            },
            {"method": "initialized", "params": {}},
            {"id": 1, "method": "account/rateLimits/read", "params": None},
        ),
        timeout,
    )
    if error is not None:
        return failed(CODEX_ID, CODEX_NAME, checked_at.isoformat(), error)

    windows = parse_codex_rate_limits(result)
    if not windows:
        return failed(
            CODEX_ID,
            CODEX_NAME,
            checked_at.isoformat(),
            "no rate limit windows (API key or usage-based account?)",
        )
    return build_provider(
        CODEX_ID,
        CODEX_NAME,
        "codexAppServer",
        checked_at,
        windows,
        banked_resets=parse_codex_banked_resets(result),
    )


def _grok_missing_reason(result: Any) -> str:
    """Explain an empty Grok reading.

    Grok omits `creditUsagePercent` until the period has some usage on it, so this is
    the expected state at the start of a week rather than a fault. Say so, and do not
    imply the plan is at zero when what we actually have is nothing.
    """
    if not isinstance(result, dict):
        return "unrecognized billing response from the Grok CLI"
    tier = result.get("subscription_tier")
    plan = f" ({tier})" if isinstance(tier, str) and tier.strip() else ""
    return (
        f"the Grok CLI reported no usage yet for the current plan period{plan}. "
        "A bar appears once Grok starts reporting one."
    )


def probe_grok(executable: str = "grok", timeout: float = DEFAULT_TIMEOUT) -> ProviderUsage:
    checked_at = _now()
    if shutil.which(executable) is None:
        return failed(GROK_ID, GROK_NAME, checked_at.isoformat(), "grok CLI not found")

    # `--no-leader` keeps the probe out of the shared leader process the interactive CLI
    # attaches to, so polling can never disturb a running Grok session. The exchange
    # opens no session and makes no model call, so there is no transcript to clean up.
    result, error = _stdio_jsonrpc_request(
        [executable, "agent", "--no-leader", "stdio"],
        (
            {
                "jsonrpc": "2.0",
                "id": 0,
                "method": "initialize",
                "params": {"protocolVersion": 1, "clientCapabilities": {}},
            },
            {"jsonrpc": "2.0", "id": 1, "method": GROK_BILLING_METHOD, "params": {}},
        ),
        timeout,
    )
    if error is not None:
        return failed(GROK_ID, GROK_NAME, checked_at.isoformat(), error)

    windows = parse_grok_billing(result)
    if not windows:
        return failed(GROK_ID, GROK_NAME, checked_at.isoformat(), _grok_missing_reason(result))
    return build_provider(GROK_ID, GROK_NAME, "grokAgentStdio", checked_at, windows)


PROBES = {CLAUDE_ID: probe_claude, CODEX_ID: probe_codex, GROK_ID: probe_grok}
PROVIDER_NAMES = {CLAUDE_ID: CLAUDE_NAME, CODEX_ID: CODEX_NAME, GROK_ID: GROK_NAME}


def probe_all(provider_ids: list[str], timeout: float = DEFAULT_TIMEOUT) -> list[ProviderUsage]:
    """Probe the requested providers concurrently; one failure never blocks the others."""
    results: dict[str, ProviderUsage] = {}
    threads: list[threading.Thread] = []

    def run(provider_id: str) -> None:
        try:
            results[provider_id] = PROBES[provider_id](timeout=timeout)
        except Exception as error:  # a probe must never crash the widget
            results[provider_id] = failed(
                provider_id,
                PROVIDER_NAMES.get(provider_id, provider_id),
                _now().isoformat(),
                f"{type(error).__name__}: {error}",
            )

    for provider_id in provider_ids:
        if provider_id not in PROBES:
            continue
        thread = threading.Thread(target=run, args=(provider_id,), daemon=True)
        thread.start()
        threads.append(thread)
    for thread in threads:
        thread.join(timeout + 5)

    return [results[pid] for pid in provider_ids if pid in results]

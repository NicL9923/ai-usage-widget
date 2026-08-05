"""Subprocess probes that read live usage from the Claude and Codex CLIs.

Both probes are free: they consume no tokens and make no model calls. We shell out to
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
import subprocess
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import ProviderUsage, failed
from .parsers import build_provider, parse_claude_usage, parse_codex_rate_limits

CLAUDE_ID = "claude"
CLAUDE_NAME = "Claude Code"
CODEX_ID = "codex"
CODEX_NAME = "Codex"

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


def _app_server_request(
    executable: str, method: str, timeout: float
) -> tuple[Any | None, str | None]:
    """Run one JSON-RPC request against `codex app-server`.

    The app-server exits as soon as stdin closes, so the request has to be issued over a
    live pipe and the response read incrementally rather than via `communicate()`.
    """
    try:
        process = subprocess.Popen(
            [executable, "app-server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
    except OSError as error:
        return None, str(error)

    stdout, stdin = process.stdout, process.stdin
    if stdout is None or stdin is None:  # pragma: no cover - configured pipes always exist
        process.kill()
        return None, "app-server pipes unavailable"

    responses: queue.Queue[Any] = queue.Queue()

    def pump() -> None:
        for line in stdout:
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(message, dict) and message.get("id") == 1:
                responses.put(message)
                return

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()

    try:
        for message in (
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
            {"id": 1, "method": method, "params": None},
        ):
            stdin.write(json.dumps(message) + "\n")
            stdin.flush()
    except (OSError, ValueError) as error:
        process.kill()
        return None, f"app-server closed early ({error})"

    try:
        message = responses.get(timeout=timeout)
    except queue.Empty:
        return None, f"timed out after {timeout:g}s"
    finally:
        process.kill()
        process.wait(timeout=5)

    if "error" in message:
        detail = message["error"]
        text = detail.get("message") if isinstance(detail, dict) else str(detail)
        return None, str(text or "app-server returned an error")
    return message.get("result"), None


def probe_codex(executable: str = "codex", timeout: float = DEFAULT_TIMEOUT) -> ProviderUsage:
    checked_at = _now()
    if shutil.which(executable) is None:
        return failed(CODEX_ID, CODEX_NAME, checked_at.isoformat(), "codex CLI not found")

    result, error = _app_server_request(executable, "account/rateLimits/read", timeout)
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
    return build_provider(CODEX_ID, CODEX_NAME, "codexAppServer", checked_at, windows)


PROBES = {CLAUDE_ID: probe_claude, CODEX_ID: probe_codex}
PROVIDER_NAMES = {CLAUDE_ID: CLAUDE_NAME, CODEX_ID: CODEX_NAME}


def probe_all(provider_ids: list[str], timeout: float = DEFAULT_TIMEOUT) -> list[ProviderUsage]:
    """Probe the requested providers concurrently; one failure never blocks the other."""
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

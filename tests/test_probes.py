"""Subprocess-level tests for the JSON-RPC probe lifecycle."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from ai_usage.probes import _stdio_jsonrpc_request


def command(script: str, *arguments: str) -> list[str]:
    return [sys.executable, "-u", "-c", script, *arguments]


def test_stdio_jsonrpc_returns_matching_response() -> None:
    result, error = _stdio_jsonrpc_request(
        command(
            """import json
import sys
for line in sys.stdin:
    request = json.loads(line)
    if request.get('id') == 1:
        print(json.dumps({'id': 1, 'result': {'used': 27}}), flush=True)
        break
"""
        ),
        ({"id": 0, "method": "initialize"}, {"id": 1, "method": "usage"}),
        timeout=2,
    )

    assert error is None
    assert result == {"used": 27}


def test_stdio_jsonrpc_reports_eof_without_waiting_for_timeout() -> None:
    started = time.monotonic()
    result, error = _stdio_jsonrpc_request(
        command("import sys; sys.stdin.readline()"), ({"id": 1, "method": "usage"},), timeout=2
    )

    assert result is None
    assert error is not None
    assert error.endswith("exited before responding")
    assert time.monotonic() - started < 1


def test_stdio_jsonrpc_timeout_kills_and_reaps_child(tmp_path: Path) -> None:
    pid_path = tmp_path / "child.pid"
    result, error = _stdio_jsonrpc_request(
        command(
            """import os
import pathlib
import sys
import time
pathlib.Path(sys.argv[1]).write_text(str(os.getpid()), encoding='utf-8')
time.sleep(60)
""",
            str(pid_path),
        ),
        ({"id": 1, "method": "usage"},),
        timeout=1,
    )

    assert result is None
    assert error == "timed out after 1s"
    child_pid = int(pid_path.read_text(encoding="utf-8"))
    assert not Path(f"/proc/{child_pid}").exists()
    with_timeout = False
    try:
        os.kill(child_pid, 0)
    except ProcessLookupError:
        with_timeout = True
    assert with_timeout


def test_stdio_jsonrpc_timeout_kills_descendant_holding_stdout_open(tmp_path: Path) -> None:
    descendant_pid_path = tmp_path / "descendant.pid"
    result, error = _stdio_jsonrpc_request(
        command(
            """import pathlib
import subprocess
import sys
import time
descendant = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
pathlib.Path(sys.argv[1]).write_text(str(descendant.pid), encoding='utf-8')
time.sleep(60)
""",
            str(descendant_pid_path),
        ),
        ({"id": 1, "method": "usage"},),
        timeout=1,
    )

    assert result is None
    assert error == "timed out after 1s"
    descendant_pid = int(descendant_pid_path.read_text(encoding="utf-8"))
    # An orphan can briefly remain a zombie until init reaps it. It no longer owns
    # the pipe; requiring immediate /proc removal would race the host's reaper.
    try:
        status = Path(f"/proc/{descendant_pid}/stat").read_text()
    except FileNotFoundError:
        return
    assert status.split(") ", 1)[1].startswith("Z ")

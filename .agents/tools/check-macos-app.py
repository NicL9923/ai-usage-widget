#!/usr/bin/env python3
"""Check the built app's live menu bar label and signature in a logged-in macOS session."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> None:
    repo = Path(__file__).resolve().parents[2]
    app = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else repo / "build/AI Usage.app"
    process = subprocess.Popen(
        [str(app / "Contents/MacOS/AIUsage")], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )

    def title() -> str:
        # Scope accessibility reads to our process; never attach to another running copy.
        script = f"""tell application "System Events"
            tell (first application process whose unix id is {process.pid})
                get name of every menu bar item of every menu bar
            end tell
        end tell"""
        return subprocess.run(
            ["/usr/bin/osascript", "-e", script],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()

    def wait_running(seconds: int) -> None:
        try:
            process.wait(timeout=seconds)
        except subprocess.TimeoutExpired:
            return
        raise RuntimeError(f"App exited unexpectedly with status {process.returncode}")

    try:
        wait_running(1)
        print("Initial menu bar:", title(), flush=True)
        wait_running(25)
        updated = title()
        print("Updated menu bar:", updated, flush=True)
        if "AI Usage." not in updated or "…" in updated:
            raise RuntimeError("Menu bar did not finish updating")
        subprocess.run(["/usr/bin/codesign", "--verify", "--strict", str(app)], check=True)
    finally:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=5)


if __name__ == "__main__":
    main()

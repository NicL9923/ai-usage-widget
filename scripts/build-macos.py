#!/usr/bin/env python3
"""Build a local, ad-hoc-signed menu bar app with a copy of the shared helper."""

from __future__ import annotations

import argparse
import json
import os
import platform
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def base_python_path(executable: str) -> str:
    """Use Homebrew's stable formula path instead of a patch-specific Cellar binary."""
    path = Path(executable).absolute()
    parts = path.parts
    if "Cellar" in parts:
        index = parts.index("Cellar")
        formula = parts[index + 1] if len(parts) > index + 1 else ""
        match = re.fullmatch(r"python@(\d+\.\d+)", formula)
        if match:
            return str(Path(*parts[:index]) / "opt" / formula / "bin" / f"python{match[1]}")
    return str(path)


def verify_install(destination: Path, config: dict) -> None:
    """Validate the packaged helper without requiring every provider to be signed in."""
    environment = {**os.environ, **config["environment"]}
    environment.update(
        PATH=config["path"],
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONPATH=str(destination / "Contents/Resources/helper"),
        PYTHONNOUSERSITE="1",
        PYTHONSAFEPATH="1",
    )
    for provider in ("codex", "claude", "grok"):
        found = shutil.which(provider, path=config["path"]) is not None
        print(f"{provider} CLI: {'found' if found else 'not found'}")
    result = subprocess.run(
        [config["python"], "-m", "ai_usage", "--timeout", "20"],
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=65,
        check=False,
    )
    if result.returncode not in (0, 1):
        raise RuntimeError("The installed helper failed. Check Python and rerun the installer.")
    try:
        report = json.loads(result.stdout)
        if report["version"] != 1 or not isinstance(report["providers"], list):
            raise ValueError("Unsupported helper report")
    except (ValueError, KeyError, TypeError) as error:
        raise RuntimeError("The installed helper did not return a usage report.") from error
    for provider in report["providers"]:
        state = "available" if provider.get("hasReading") else "unavailable"
        print(f"{provider['displayName']}: {state}")
    if result.returncode == 1:
        print("No usage available yet. Check the CLI sign-ins.")
    subprocess.run(["/usr/bin/codesign", "--verify", "--strict", str(destination)], check=True)


def write_launcher(launcher: Path, destination: Path, config: dict) -> None:
    launcher.parent.mkdir(parents=True, exist_ok=True)
    helper_path = destination / "Contents/Resources/helper"
    launcher.write_text(
        "#!/bin/sh\nexec env PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 PYTHONSAFEPATH=1 "
        + " ".join(shlex.quote(key + "=" + value) for key, value in config["environment"].items())
        + " "
        + shlex.quote("PYTHONPATH=" + str(helper_path))
        + " "
        + shlex.quote("PATH=" + config["path"])
        + " "
        + shlex.quote(config["python"])
        + ' -m ai_usage "$@"\n',
        encoding="utf-8",
    )
    launcher.chmod(0o755)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--install", action="store_true", help="Install to ~/Applications and open."
    )
    parser.add_argument("--no-open", action="store_true", help="Do not launch after installation.")
    parser.add_argument("--output", type=Path, help="App destination. Default: build/AI Usage.app.")
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("The menu bar app must be built on macOS.")
    if sys.version_info < (3, 11):  # noqa: UP036 - this installer also runs before project setup
        parser.error("Python 3.11 or newer is required.")
    # Local Mac mini policy: native compilation shares one machine-wide lock.
    lock = Path.home() / ".agents/tools/mac-heavy-lock.sh"
    if platform.node().split(".")[0] == "Nicolass-Mini" and not os.environ.get(
        "AI_USAGE_BUILD_LOCKED"
    ):
        subprocess.run(
            [str(lock), "env", "AI_USAGE_BUILD_LOCKED=1", sys.executable, __file__, *sys.argv[1:]],
            check=True,
        )
        return
    repo = Path(__file__).resolve().parent.parent
    destination = (
        (
            args.output
            or (
                Path.home() / "Applications/AI Usage.app"
                if args.install
                else repo / "build/AI Usage.app"
            )
        )
        .expanduser()
        .resolve()
    )
    if destination.suffix != ".app":
        parser.error("--output must end in .app")
    destination.parent.mkdir(parents=True, exist_ok=True)
    build = repo / "build"
    build.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".ai-usage-", dir=destination.parent) as temporary:
        app = Path(temporary) / "AI Usage.app"
        contents = app / "Contents"
        binaries = contents / "MacOS"
        resources = contents / "Resources"
        binaries.mkdir(parents=True)
        resources.mkdir()
        subprocess.run(
            [
                "/usr/bin/xcrun",
                "swiftc",
                "-parse-as-library",
                "-O",
                "-target",
                f"{platform.machine()}-apple-macosx13.0",
                "-module-cache-path",
                str(build / "swift-module-cache"),
                str(repo / "macos/Usage.swift"),
                str(repo / "macos/AIUsageApp.swift"),
                "-o",
                str(binaries / "AIUsage"),
            ],
            check=True,
        )
        shutil.copytree(
            repo / "src/ai_usage",
            resources / "helper/ai_usage",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        # The helper is stdlib-only. An installed app must outlive the build venv.
        python = base_python_path(getattr(sys, "_base_executable", sys.executable))
        config = {
            "python": python,
            "path": os.environ.get("PATH", "/usr/bin:/bin"),
            "environment": {
                key: os.environ[key]
                for key in (
                    "CODEX_HOME",
                    "CLAUDE_CONFIG_DIR",
                    "XDG_CACHE_HOME",
                    "XDG_CONFIG_HOME",
                    "XDG_DATA_HOME",
                )
                if key in os.environ
            },
        }
        (resources / "helper.json").write_text(json.dumps(config), encoding="utf-8")
        with (contents / "Info.plist").open("wb") as handle:
            plistlib.dump(
                {
                    "CFBundleExecutable": "AIUsage",
                    "CFBundleIdentifier": "com.nicl9923.aiusage.macos",
                    "CFBundleName": "AI Usage",
                    "CFBundleDisplayName": "AI Usage",
                    "CFBundlePackageType": "APPL",
                    "CFBundleShortVersionString": "0.1.0",
                    "CFBundleVersion": "1",
                    "LSMinimumSystemVersion": "13.0",
                    "LSUIElement": True,
                    "NSHighResolutionCapable": True,
                },
                handle,
            )
        subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", str(app)], check=True)
        # Preserve an existing install if compilation or signing fails.
        backup = Path(temporary) / "previous.app"
        if destination.exists():
            # Ask only the running copy at this path to quit before moving its resources.
            support = build / "install-support"
            subprocess.run(
                [
                    "/usr/bin/xcrun",
                    "swiftc",
                    "-module-cache-path",
                    str(build / "swift-module-cache"),
                    str(repo / "macos/InstallSupport.swift"),
                    "-o",
                    str(support),
                ],
                check=True,
            )
            subprocess.run([str(support), str(destination)], check=True)
            destination.rename(backup)
        try:
            app.rename(destination)
        except OSError:
            if backup.exists():
                backup.rename(destination)
            raise
    if args.install:
        write_launcher(Path.home() / ".local/bin/ai-usage", destination, config)
        verify_install(destination, config)
        if not args.no_open:
            subprocess.run(["/usr/bin/open", str(destination)], check=True)
    print(f"Built {destination}")


if __name__ == "__main__":
    main()

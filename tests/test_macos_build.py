"""Installer verification must distinguish unavailable usage from a broken Python helper."""

from __future__ import annotations

import json
import runpy
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def installer():
    return runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/build-macos.py"))


@pytest.fixture
def bundle(tmp_path: Path, monkeypatch):
    app = tmp_path / "AI Usage.app"
    package = app / "Contents/Resources/helper/ai_usage"
    package.mkdir(parents=True)
    (package / "__init__.py").touch()
    real_run = subprocess.run

    def run(args, **kwargs):
        if args[0] == "/usr/bin/codesign":
            return subprocess.CompletedProcess(args, 0)
        return real_run(args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    config = {"python": sys.executable, "path": "/usr/bin:/bin", "environment": {}}
    return app, package, config


def test_verify_install_accepts_unavailable_and_keeps_bundle_read_only(installer, bundle):
    app, package, config = bundle
    report = {"version": 1, "providers": [{"displayName": "Grok", "hasReading": False}]}
    (package / "__main__.py").write_text(
        "import sys\n" + f"print({json.dumps(report)!r})\n" + "sys.exit(1)\n", encoding="utf-8"
    )
    installer["verify_install"](app, config)
    assert not list(app.rglob("__pycache__"))


def test_verify_install_rejects_python_error_even_with_exit_one(installer, bundle):
    app, package, config = bundle
    (package / "__main__.py").write_text("raise RuntimeError('broken helper')\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="did not return a usage report"):
        installer["verify_install"](app, config)


def test_installed_launcher_preserves_arguments_paths_and_signature_inputs(installer, bundle):
    app, package, config = bundle
    (package / "__main__.py").write_text(
        "import os, sys\n"
        "assert sys.argv[1:] == ['--provider', 'codex']\n"
        "assert os.environ['CODEX_HOME'] == '/custom path/codex'\n"
        "print('launcher works')\n",
        encoding="utf-8",
    )
    config["environment"] = {"CODEX_HOME": "/custom path/codex"}
    launcher = app.parent / "bin with spaces/ai-usage"
    installer["write_launcher"](launcher, app, config)
    result = subprocess.run(
        [str(launcher), "--provider", "codex"], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "launcher works"
    assert not list(app.rglob("__pycache__"))


@pytest.mark.parametrize(
    ("executable", "expected"),
    [
        (
            "/opt/homebrew/Cellar/python@3.14/3.14.7/Frameworks/Python.framework/Versions/3.14/bin/python3.14",
            "/opt/homebrew/opt/python@3.14/bin/python3.14",
        ),
        (
            "/usr/local/Cellar/python@3.11/3.11.9/bin/python3.11",
            "/usr/local/opt/python@3.11/bin/python3.11",
        ),
        (
            "/opt/homebrew/opt/python@3.14/bin/python3.14",
            "/opt/homebrew/opt/python@3.14/bin/python3.14",
        ),
        ("/usr/local/bin/python3", "/usr/local/bin/python3"),
    ],
)
def test_base_python_path_survives_patch_upgrades(installer, executable, expected):
    assert installer["base_python_path"](executable) == expected


def test_helper_cannot_be_shadowed_by_working_directory(installer, bundle, monkeypatch):
    app, package, config = bundle
    (package / "__main__.py").write_text(
        'print(\'{"version":1,"providers":[]}\')\n', encoding="utf-8"
    )
    shadow = app.parent / "ai_usage"
    shadow.mkdir()
    (shadow / "__init__.py").touch()
    (shadow / "__main__.py").write_text("raise RuntimeError('SHADOWED')\n", encoding="utf-8")
    monkeypatch.chdir(app.parent)
    installer["verify_install"](app, config)
    launcher = app.parent / "bin/ai-usage"
    installer["write_launcher"](launcher, app, config)
    result = subprocess.run([str(launcher)], capture_output=True, text=True, check=True)
    assert json.loads(result.stdout)["version"] == 1

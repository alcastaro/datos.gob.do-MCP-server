"""Version-drift guard: pyproject.toml, server.json, __init__.__version__ and
USER_AGENT must all carry the same version. Runs in CI, so a bump that misses
one file fails the build instead of shipping skewed metadata.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from datosgobdo_mcp import USER_AGENT, __version__

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def repo_files():
    pyproject = ROOT / "pyproject.toml"
    server_json = ROOT / "server.json"
    if not pyproject.exists() or not server_json.exists():
        pytest.skip("repo metadata files not present (installed-package run)")
    return pyproject.read_text(encoding="utf-8"), json.loads(
        server_json.read_text(encoding="utf-8")
    )


def test_pyproject_version_matches_package(repo_files):
    pyproject_text, _ = repo_files
    m = re.search(r'^version = "([^"]+)"', pyproject_text, re.MULTILINE)
    assert m, "no version field in pyproject.toml"
    assert m.group(1) == __version__


def test_server_json_versions_match_package(repo_files):
    _, server_json = repo_files
    assert server_json["version"] == __version__
    for pkg in server_json.get("packages", []):
        assert pkg["version"] == __version__


def test_user_agent_carries_package_version():
    assert __version__ in USER_AGENT

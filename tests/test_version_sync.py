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


def test_serverinfo_reports_package_version_not_sdk_version():
    """serverInfo.version must be the package version.

    FastMCP takes no `version` argument, so the low-level server defaulted to
    the installed mcp SDK version and clients saw e.g. "1.27.1" as our version.
    """
    from datosgobdo_mcp.server import mcp

    opts = mcp._mcp_server.create_initialization_options()
    assert opts.server_version == __version__


def test_console_scripts_include_pypi_named_alias(repo_files):
    """`uvx dominican-open-data-mcp` (the command the Registry entry implies)
    only works if a console script matches the distribution name.
    """
    pyproject_text, _ = repo_files
    scripts = pyproject_text.split("[project.scripts]", 1)[1].split("\n[", 1)[0]
    for name in ("datosgobdo-mcp", "dominican-open-data-mcp"):
        assert re.search(
            rf'^{re.escape(name)} = "datosgobdo_mcp\.server:main"$',
            scripts,
            re.MULTILINE,
        ), f"missing console script: {name}"


def test_mcp_dependency_has_upper_bound(repo_files):
    """mcp 2.x removed `mcp.server.fastmcp`; an unbounded pin breaks installs."""
    pyproject_text, _ = repo_files
    assert re.search(r'"mcp>=[\d.]+,<2"', pyproject_text), (
        "mcp dependency must keep an upper bound until the SDK v2 migration lands"
    )


def test_security_policy_supports_the_shipping_minor():
    """SECURITY.md's table is a claim about which code gets security fixes, and
    nothing was checking it: the 0.14.0 bump touched pyproject, server.json and
    __init__ — all three guarded above — and left the policy saying 0.13.x, which
    reads as "the version you are running is unsupported".
    """
    policy = ROOT / "SECURITY.md"
    if not policy.exists():
        pytest.skip("SECURITY.md not present (installed-package run)")
    text = policy.read_text(encoding="utf-8")
    minor = ".".join(__version__.split(".")[:2])
    assert f"| {minor}.x" in text, f"SECURITY.md does not list {minor}.x as supported"
    assert f"| < {minor}" in text, f"SECURITY.md does not mark < {minor} unsupported"

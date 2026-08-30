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

    The v1 SDK's server class took no `version` argument, so the low-level
    server defaulted to the installed mcp SDK version and clients saw e.g.
    "1.27.1" as our version. v2 takes it in the constructor; this test is what
    says the constructor is actually being given it.
    """
    from datosgobdo_mcp.server import mcp

    opts = mcp._lowlevel_server.create_initialization_options()
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


def test_mcp_dependency_is_pinned_to_the_major_this_code_targets(repo_files):
    """Both ends of the mcp pin are load-bearing, and each has already broken
    an install once.

    The upper bound came first: v2 renamed the server class and deleted the
    old module outright, so an unbounded pin silently upgraded fresh installs
    into an ImportError. This code is now written against v2, which makes the
    lower bound just as sharp in the other direction — on 1.x the import fails
    on the first line of server.py. A pin naming one major is the only honest
    description of a package that works on exactly one.
    """
    pyproject_text, _ = repo_files
    assert re.search(r'"mcp>=2\.\d+(\.\d+)?,<3"', pyproject_text), (
        "mcp dependency must pin to the 2.x major this code is written against"
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


def test_the_container_and_the_worker_agree_on_every_env_var():
    """The Dockerfile and the Worker declare the same environment twice.

    They are deliberately duplicated — the container is what Cloud Run or Fly
    would use, the Worker is what Cloudflare uses, and neither inherits from the
    other. The `index.js` comment already says what goes wrong: a value present
    in only one of them is a difference nobody notices until the numbers
    disagree. This is that comment, enforced.

    It was written after an audit found `DATOSGOBDO_QUERY_TIMEOUT` set in
    neither, which meant the published image ran model-supplied SQL with no wall
    clock at all.
    """
    dockerfile = ROOT / "Dockerfile"
    worker = ROOT / "deploy" / "cloudflare" / "src" / "index.js"
    if not dockerfile.exists() or not worker.exists():
        pytest.skip("deploy files not present (installed-package run)")

    # The first var on an ENV line sits right after "ENV ", the rest are indented.
    docker_env = dict(
        re.findall(r"^(?:ENV\s+)?\s*(DATOSGOBDO_\w+)=(\S+)", dockerfile.read_text(), re.M)
    )
    worker_env = dict(re.findall(r'^\s*(DATOSGOBDO_\w+):\s*"([^"]*)"', worker.read_text(), re.M))

    assert docker_env, "no DATOSGOBDO_* vars parsed from the Dockerfile"
    assert set(docker_env) == set(worker_env), (
        f"only in Dockerfile: {sorted(set(docker_env) - set(worker_env))}; "
        f"only in Worker: {sorted(set(worker_env) - set(docker_env))}"
    )
    for name, value in docker_env.items():
        assert worker_env[name] == value.rstrip("\\"), (
            f"{name}: Dockerfile says {value!r}, Worker says {worker_env[name]!r}"
        )


def test_hosted_deploys_bound_query_time():
    """0 means no timeout. That is the right default locally and the wrong one
    on an instance whose SQL arrives from a model and is shared with strangers."""
    dockerfile = ROOT / "Dockerfile"
    if not dockerfile.exists():
        pytest.skip("Dockerfile not present (installed-package run)")
    m = re.search(r"DATOSGOBDO_QUERY_TIMEOUT=(\d+)", dockerfile.read_text())
    assert m, "the hosted image sets no query timeout"
    assert int(m.group(1)) > 0, "a query timeout of 0 is no timeout at all"

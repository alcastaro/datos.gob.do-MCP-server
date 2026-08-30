"""The hosted transport, over the hosted transport.

`test_hosted.py` calls the tool functions in-process with an environment
variable set. That checks the gating logic and nothing else: it never starts an
HTTP server, never negotiates a session, and would pass just as happily if
`streamable-http` did not work at all. The transport had shipped with resource
limits, a stateless mode and two tools disabled — and zero evidence that a
client could connect to it.

These tests launch the real server as a subprocess and talk to it with the
SDK's streamable-http client, which is what a hosted deployment would do.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

pytestmark = pytest.mark.anyio


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="module")
def hosted_server(tmp_path_factory):
    """Run the packaged server under streamable-http, as a hosted deploy would."""
    port = _free_port()
    env = {
        **os.environ,
        "DATOSGOBDO_TRANSPORT": "streamable-http",
        "DATOSGOBDO_HOST": "127.0.0.1",
        "DATOSGOBDO_PORT": str(port),
        "DATOSGOBDO_CACHE_DIR": str(tmp_path_factory.mktemp("hosted-cache")),
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "datosgobdo_mcp.server"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    url = f"http://127.0.0.1:{port}/mcp"
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            err = (proc.stderr.read() or b"").decode()[-2000:] if proc.stderr else ""
            pytest.fail(f"hosted server exited early:\n{err}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                break
        except OSError:
            time.sleep(0.2)
    else:  # pragma: no cover — only on a machine that cannot bind
        proc.kill()
        pytest.fail("hosted server did not start listening")
    try:
        yield url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover
            proc.kill()


async def test_hosted_transport_completes_a_session(hosted_server):
    """Connect, negotiate, and list tools over HTTP — the thing no test did."""
    async with (
        streamable_http_client(hosted_server) as (read, write),
        ClientSession(read, write) as session,
    ):
        init = await session.initialize()
        assert init.server_info.name == "datosgobdo-mcp"
        tools = await session.list_tools()
        assert len(tools.tools) == 24
        assert all(t.output_schema for t in tools.tools)


async def test_hosted_reports_its_own_transport(hosted_server):
    async with (
        streamable_http_client(hosted_server) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        result = await session.call_tool("get_cache_stats", {})
        info = (result.structured_content or {})["server"]
        assert info["transport"] == "streamable-http"
        # A remote caller must not learn where the server keeps its files. The
        # key survives because the response model declares it; what matters is
        # that it carries no path.
        assert (result.structured_content or {})["cache_dir"] is None


async def test_local_filesystem_tools_are_refused_over_http(hosted_server):
    """save_query_to_csv writes to the server's disk and clear_cache wipes a
    cache other sessions are using. Both are disabled in hosted mode, and this
    is the first test to confirm it through the transport rather than by
    calling the function with an env var set."""
    async with (
        streamable_http_client(hosted_server) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        for name, args in (
            ("save_query_to_csv", {"url": "https://example.test/x.csv", "format": "csv"}),
            ("clear_cache", {}),
        ):
            result = await session.call_tool(name, args)
            body = result.structured_content or {}
            assert body.get("error") == "This tool is disabled in hosted mode", name
            # And it is flagged, so a caller that is not a model can tell.
            assert result.is_error is True, name


async def test_hosted_serves_resources_and_prompts_too(hosted_server):
    async with (
        streamable_http_client(hosted_server) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        assert len((await session.list_prompts()).prompts) == 6
        assert len((await session.list_resources()).resources) == 3
        assert len((await session.list_resource_templates()).resource_templates) == 1


async def test_stateless_mode_serves_independent_sessions(hosted_server):
    """Stateless is what lets a hosted deployment scale horizontally: a second
    session must not depend on anything the first one left behind."""
    for _ in range(2):
        async with (
            streamable_http_client(hosted_server) as (read, write),
            ClientSession(read, write) as session,
        ):
            init = await session.initialize()
            assert init.server_info.version


async def test_unknown_path_is_not_the_mcp_endpoint(hosted_server):
    base = hosted_server.rsplit("/", 1)[0]
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"{base}/not-a-real-path")
    assert r.status_code >= 400

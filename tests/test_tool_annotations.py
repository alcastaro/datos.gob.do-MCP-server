"""MCP tool-annotation compliance.

The Anthropic Directory review criteria require every tool to carry
`title`, `readOnlyHint`, and (where relevant) `destructiveHint`. These tests
introspect the live FastMCP registry via the protocol-level list_tools().
"""

from __future__ import annotations

from datosgobdo_mcp.server import mcp

# Tools that mutate state (only local files/cache; never the portal).
DESTRUCTIVE_TOOLS = {"clear_cache", "save_query_to_csv"}


async def test_every_tool_has_title_and_readonly_hint():
    tools = await mcp.list_tools()
    assert tools, "no tools registered"
    for t in tools:
        ann = t.annotations
        assert ann is not None, f"{t.name}: missing annotations"
        assert ann.title, f"{t.name}: missing title annotation"
        assert ann.readOnlyHint is not None, f"{t.name}: missing readOnlyHint"


async def test_read_tools_marked_readonly():
    tools = {t.name: t for t in await mcp.list_tools()}
    for name, t in tools.items():
        if name in DESTRUCTIVE_TOOLS:
            continue
        assert t.annotations.readOnlyHint is True, f"{name}: should be readOnlyHint=True"


async def test_clear_cache_marked_destructive():
    tools = {t.name: t for t in await mcp.list_tools()}
    cc = tools["clear_cache"]
    assert cc.annotations.destructiveHint is True
    assert cc.annotations.readOnlyHint is False


async def test_network_tools_marked_open_world():
    tools = {t.name: t for t in await mcp.list_tools()}
    for name in (
        "search_datasets",
        "get_dataset",
        "summarize_resource",
        "query_resource",
    ):
        assert tools[name].annotations.openWorldHint is True, (
            f"{name}: hits the network, should be openWorldHint=True"
        )


async def test_local_only_tools_not_open_world():
    tools = {t.name: t for t in await mcp.list_tools()}
    for name in ("get_cache_stats", "clear_cache"):
        assert tools[name].annotations.openWorldHint is False, (
            f"{name}: purely local, should be openWorldHint=False"
        )


DATA_TOOLS_WITH_SCHEMA = {
    "download_resource_preview",
    "get_resource_schema",
    "summarize_resource",
    "filter_resource",
    "aggregate_resource",
    "query_resource",
    "quantiles_resource",
    "find_duplicates_resource",
    "detect_outliers_resource",
    "save_query_to_csv",
    "get_cache_stats",
    "clear_cache",
}


async def test_data_tools_have_output_schema():
    """The data-producing tools must emit a typed outputSchema (Pydantic-backed)."""
    tools = {t.name: t for t in await mcp.list_tools()}
    for name in DATA_TOOLS_WITH_SCHEMA:
        t = tools[name]
        assert t.outputSchema is not None, f"{name}: missing outputSchema"
        assert t.outputSchema.get("type") == "object", f"{name}: schema not an object"

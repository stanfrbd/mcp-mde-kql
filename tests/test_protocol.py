"""Tool contracts exercised through real SDK negotiation and lifespan injection."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock

import pytest
from mcp import Client
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import TextContent

from mcp_mde_kql.service import HuntingService
from mcp_mde_kql.tools.hunting import register_hunting_tools


@pytest.mark.parametrize("mode", ["legacy", "auto"])
async def test_protocol_results_errors_validation_and_lifespan(mode: str) -> None:
    upstream = AsyncMock()
    upstream.query.return_value = {
        "schema": [{"name": "Count", "type": "Int64"}],
        "results": [{"Count": 7}, {"Count": 8}],
    }
    lifecycle = []

    @asynccontextmanager
    async def lifespan(server: MCPServer[dict[str, Any]]) -> AsyncIterator[dict[str, Any]]:
        lifecycle.append("started")
        try:
            yield {"hunting_service": HuntingService(upstream)}
        finally:
            lifecycle.append("stopped")

    server = MCPServer("test", lifespan=lifespan)
    register_hunting_tools(server)
    async with Client(server, mode=mode) as client:
        listing = await client.list_tools()
        tools = {tool.name: tool for tool in listing.tools}
        assert set(tools) == {"run_hunting_query", "get_hunting_schema"}
        assert tools["run_hunting_query"].output_schema is not None
        result = await client.call_tool(
            "run_hunting_query", {"query": "DeviceEvents", "max_rows": 1}
        )
        assert not result.is_error
        assert result.structured_content is not None
        assert result.structured_content["rows"] == [[7]]
        assert result.structured_content["truncated"]
        assert result.structured_content["fetched_rows"] == 2
        assert result.structured_content["elapsed_ms"] >= 0
        invalid = await client.call_tool(
            "run_hunting_query", {"query": "DeviceEvents", "max_rows": 1001}
        )
        assert invalid.is_error
        assert upstream.query.await_count == 1
        invalid_table = await client.call_tool("get_hunting_schema", {"table_name": "x | take 1"})
        assert invalid_table.is_error
        assert upstream.query.await_count == 1
        # Both negotiated protocols retain the exact output schema with compact compatibility text.
        upstream.query.return_value["results"] = [{"Count": "ü🚀"}] * 1000
        bounded = await client.call_tool(
            "run_hunting_query",
            {"query": "DeviceEvents", "max_rows": 1000, "max_output_bytes": 1024},
        )
        assert not bounded.is_error
        assert len(bounded.content) == 1
        text = bounded.content[0]
        assert isinstance(text, TextContent)
        assert len(text.text.encode("utf-8")) <= 1024
        assert json.loads(text.text) == bounded.structured_content
        assert len(text.text) < len(json.dumps(bounded.structured_content, indent=2))
        upstream.query.return_value = {
            "results": [{"ColumnName": "Timestamp", "ColumnType": "datetime"}]
        }
        schema = await client.call_tool("get_hunting_schema", {"table_name": "DeviceEvents"})
        assert not schema.is_error
        assert tools["get_hunting_schema"].output_schema is not None
        assert isinstance(schema.content[0], TextContent)
        assert json.loads(schema.content[0].text) == schema.structured_content
        assert "\n" not in schema.content[0].text
        upstream.query.side_effect = ToolError("Graph throttled; retry later")
        failure = await client.call_tool("run_hunting_query", {"query": "DeviceEvents"})
        assert failure.is_error
        assert "Graph throttled" in str(failure.content)
    assert lifecycle == ["started", "stopped"]

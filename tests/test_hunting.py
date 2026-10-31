from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from mcp_mde_kql.service import HuntingService
from mcp_mde_kql.tools.hunting import register_hunting_tools


@pytest.fixture
def client() -> AsyncMock:
    client = AsyncMock()
    client.query.return_value = {
        "schema": [{"name": "payload", "type": "Dynamic"}],
        "results": [{"payload": {"count": 1, "ok": True, "items": [None, "a|b\nc"]}}],
    }
    return client


async def test_query_preserves_native_json_and_query(client: AsyncMock) -> None:
    result = await HuntingService(client).run_query("DeviceEvents", timespan="P1D")
    assert result.rows == [[{"count": 1, "ok": True, "items": [None, "a|b\nc"]}]]
    assert result.columns[0].type == "Dynamic"
    assert not result.truncated
    assert result.returned_rows == result.fetched_rows == 1
    client.query.assert_awaited_once_with("DeviceEvents", timespan="P1D")


async def test_row_limit_is_explicit(client: AsyncMock) -> None:
    client.query.return_value["results"] *= 3
    result = await HuntingService(client).run_query("DeviceEvents", max_rows=2)
    assert result.returned_rows == 2
    assert result.fetched_rows == 3
    assert result.truncated and result.warnings


@pytest.mark.parametrize("cell", ["ü" * 2000, "x" * 2000, {"nested": ["🚀" * 1000]}])
async def test_output_byte_budget_omits_whole_rows(client: AsyncMock, cell: object) -> None:
    client.query.return_value["results"] = [{"payload": "fits"}, {"payload": cell}]
    result = await HuntingService(client).run_query("DeviceEvents", max_output_bytes=1024)
    assert len(result.model_dump_json().encode("utf-8")) <= 1024
    assert result.rows == [["fits"]]
    assert result.truncated


async def test_many_small_rows_respect_byte_budget(client: AsyncMock) -> None:
    client.query.return_value["results"] = [{"payload": "ü🚀"}] * 1000
    result = await HuntingService(client).run_query(
        "DeviceEvents", max_rows=1000, max_output_bytes=1024
    )
    assert len(result.model_dump_json().encode("utf-8")) <= 1024
    assert 1 < result.returned_rows < 1000
    assert result.truncated


async def test_empty_result_retains_columns(client: AsyncMock) -> None:
    client.query.return_value["results"] = []
    result = await HuntingService(client).run_query("DeviceEvents")
    assert result.columns[0].name == "payload"
    assert result.rows == [] and not result.truncated and not result.warnings


@pytest.mark.parametrize(
    "query,rows,budget", [(" ", 100, 1024), ("x", 0, 1024), ("x", 1001, 1024), ("x", 1, 100)]
)
async def test_invalid_query_parameters_skip_upstream(
    client: AsyncMock, query: str, rows: int, budget: int
) -> None:
    with pytest.raises(ToolError):
        await HuntingService(client).run_query(query, rows, budget)
    client.query.assert_not_awaited()


async def test_invalid_response_becomes_safe_error(client: AsyncMock) -> None:
    client.query.return_value = {"sensitive": "tenant details"}
    with pytest.raises(ToolError, match="Invalid response received"):
        await HuntingService(client).run_query("DeviceEvents")


async def test_huge_schema_fails_explicitly(client: AsyncMock) -> None:
    client.query.return_value["schema"] = [{"name": "x" * 2000}]
    with pytest.raises(ToolError, match="Column schema exceeds"):
        await HuntingService(client).run_query("DeviceEvents", max_output_bytes=1024)


@pytest.mark.parametrize(
    "table", ["DeviceEvents | take 1", "x\n", "", "x; DeviceEvents", "x//comment"]
)
async def test_table_identifier_validation(client: AsyncMock, table: str) -> None:
    with pytest.raises(ToolError, match="single table identifier"):
        await HuntingService(client).get_schema(table)
    client.query.assert_not_awaited()


async def test_metadata_discovery_is_cached_without_event_scan(client: AsyncMock) -> None:
    client.table_names.return_value = ["DeviceEvents"]
    service = HuntingService(client)
    first = await service.get_schema()
    first.tables.clear()
    second = await service.get_schema()
    assert second.tables == ["DeviceEvents"]
    assert second.complete
    assert second.warnings
    client.table_names.assert_awaited_once_with()
    client.query.assert_not_awaited()


async def test_schema_cache_expires_and_is_bounded(client: AsyncMock) -> None:
    client.query.return_value = {"results": [{"ColumnName": "Timestamp", "ColumnType": "datetime"}]}
    service = HuntingService(client, cache_size=1)
    with patch("mcp_mde_kql.service.time.monotonic", return_value=0):
        first = await service.get_schema("DeviceEvents")
        assert first.columns[0].name == "Timestamp"
        await service.get_schema("DeviceEvents")
    assert client.query.await_count == 1
    with patch("mcp_mde_kql.service.time.monotonic", return_value=301):
        await service.get_schema("DeviceEvents")
    assert client.query.await_count == 2
    await service.get_schema("DeviceInfo")
    assert len(service._schema_cache) == 1
    assert "DeviceInfo" in service._schema_cache


async def test_concurrent_schema_requests_share_discovery(client: AsyncMock) -> None:
    client.table_names.return_value = ["DeviceEvents"]
    service = HuntingService(client)
    await asyncio.gather(*(service.get_schema() for _ in range(10)))
    assert client.table_names.await_count == 1
    client.query.assert_not_awaited()


async def test_failures_are_not_cached(client: AsyncMock) -> None:
    client.table_names.side_effect = ToolError("Graph unavailable")
    service = HuntingService(client)
    for _ in range(2):
        with pytest.raises(ToolError, match="Graph unavailable"):
            await service.get_schema()
    assert client.table_names.await_count == 2


async def test_sdk_tool_contract_exposes_constraints_output_and_hints() -> None:
    server = MCPServer("test")
    register_hunting_tools(server)
    tools = {tool.name: tool for tool in await server.list_tools()}
    assert set(tools) == {"run_hunting_query", "get_hunting_schema"}
    query = tools["run_hunting_query"]
    assert query.output_schema is not None
    assert "rows" in query.output_schema["properties"]
    assert query.input_schema["properties"]["max_rows"]["maximum"] == 1000
    assert "ctx" not in query.input_schema["properties"]
    assert query.annotations is not None and query.annotations.read_only_hint
    assert query.annotations.open_world_hint


@pytest.mark.parametrize("table", [None, "DeviceEvents"])
@pytest.mark.parametrize("rows", [["malformed"], None, {"wrong": "shape"}])
async def test_malformed_schema_is_safe_error(
    client: AsyncMock, table: str | None, rows: object
) -> None:
    client.query.return_value = {"results": rows}
    client.table_names.return_value = [123] if isinstance(rows, list) else rows
    with pytest.raises(ToolError, match="Invalid schema response"):
        await HuntingService(client).get_schema(table)


async def test_presence_list_is_filtered_and_reports_pending_separately(client):
    from unittest.mock import MagicMock

    presence = MagicMock()
    presence.snapshot.return_value = {
        "status": "warming",
        "total_tables": 4,
        "checked_tables": 3,
        "with_data": ["CloudAppEvents"],
        "empty": ["EmptyTable"],
        "unknown": ["FailedTable", "PendingTable"],
        "error_count": 1,
        "complete": False,
        "warnings": ["Warmup in progress"],
    }
    service = HuntingService(client, presence=presence)
    result = await service.get_schema()
    assert result.tables == ["CloudAppEvents"]
    assert not result.complete
    assert result.presence.pending_tables == 1
    assert result.presence.unknown_tables == 1
    assert result.presence.empty_tables == 1
    client.query.assert_not_awaited()
    client.table_names.assert_not_awaited()
    client.query.return_value = {"results": [{"ColumnName": "Timestamp", "ColumnType": "datetime"}]}
    assert (await service.get_schema("PendingTable")).columns[0].name == "Timestamp"

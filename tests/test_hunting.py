import httpx
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from mcp_mde_kql.tools.hunting import _get_hunting_schema, _run_hunting_query
from mcp_mde_kql.utils.config import MdeConfig

_CONFIG = MdeConfig(tenant_id="tid", client_id="cid", client_secret="secret")

_SAMPLE_RESPONSE = {
    "schema": [{"name": "Timestamp"}, {"name": "DeviceName"}],
    "results": [
        {"Timestamp": "2024-01-01", "DeviceName": "host1"},
        {"Timestamp": "2024-01-02", "DeviceName": "host2"},
    ],
    "stats": {"executionTime": 0.5},
}

_MOCK_TARGET = "mcp_mde_kql.tools.hunting.run_defender_query"


# --- _run_hunting_query ---


@pytest.mark.asyncio
async def test_run_hunting_query_formats_table():
    with patch(_MOCK_TARGET, new_callable=AsyncMock) as mock:
        mock.return_value = _SAMPLE_RESPONSE
        result = await _run_hunting_query("DeviceEvents | limit 2", _CONFIG)

    assert "Timestamp" in result
    assert "DeviceName" in result
    assert "host1" in result
    assert "host2" in result


@pytest.mark.asyncio
async def test_run_hunting_query_shows_stats():
    with patch(_MOCK_TARGET, new_callable=AsyncMock) as mock:
        mock.return_value = _SAMPLE_RESPONSE
        result = await _run_hunting_query("DeviceEvents | limit 2", _CONFIG)

    assert "2 row(s)" in result
    assert "0.5s" in result


@pytest.mark.asyncio
async def test_run_hunting_query_empty_results():
    with patch(_MOCK_TARGET, new_callable=AsyncMock) as mock:
        mock.return_value = {"schema": [], "results": []}
        result = await _run_hunting_query("DeviceEvents | limit 0", _CONFIG)

    assert result == "Query returned no results"


@pytest.mark.asyncio
async def test_run_hunting_query_http_error():
    with patch(_MOCK_TARGET, new_callable=AsyncMock) as mock:
        response = MagicMock()
        response.text = "Forbidden"
        mock.side_effect = httpx.HTTPStatusError("403", request=MagicMock(), response=response)
        result = await _run_hunting_query("bad query", _CONFIG)

    assert "Query error" in result
    assert "Forbidden" in result


@pytest.mark.asyncio
async def test_run_hunting_query_generic_error():
    with patch(_MOCK_TARGET, new_callable=AsyncMock) as mock:
        mock.side_effect = RuntimeError("timeout")
        result = await _run_hunting_query("DeviceEvents", _CONFIG)

    assert "Query error" in result
    assert "timeout" in result


# --- _get_hunting_schema ---


@pytest.mark.asyncio
async def test_get_hunting_schema_all_tables():
    with patch(_MOCK_TARGET, new_callable=AsyncMock) as mock:
        mock.return_value = {
            "schema": [],
            "results": [{"$table": "DeviceEvents"}, {"$table": "EmailEvents"}],
        }
        result = await _get_hunting_schema(None, _CONFIG)

    assert "DeviceEvents" in result
    assert "EmailEvents" in result
    assert "get_hunting_schema" in result


@pytest.mark.asyncio
async def test_get_hunting_schema_specific_table():
    with patch(_MOCK_TARGET, new_callable=AsyncMock) as mock:
        mock.return_value = {
            "schema": [],
            "results": [
                {"ColumnName": "Timestamp", "ColumnType": "datetime"},
                {"ColumnName": "DeviceName", "ColumnType": "string"},
            ],
        }
        result = await _get_hunting_schema("DeviceEvents", _CONFIG)

    assert "DeviceEvents" in result
    assert "Timestamp" in result
    assert "datetime" in result
    assert "DeviceName" in result
    assert "string" in result


@pytest.mark.asyncio
async def test_get_hunting_schema_table_not_found():
    with patch(_MOCK_TARGET, new_callable=AsyncMock) as mock:
        mock.return_value = {"schema": [], "results": []}
        result = await _get_hunting_schema("NonExistentTable", _CONFIG)

    assert "not found" in result


@pytest.mark.asyncio
async def test_get_hunting_schema_no_tables():
    with patch(_MOCK_TARGET, new_callable=AsyncMock) as mock:
        mock.return_value = {"schema": [], "results": []}
        result = await _get_hunting_schema(None, _CONFIG)

    assert "Could not retrieve schema" in result


@pytest.mark.asyncio
async def test_get_hunting_schema_http_error():
    with patch(_MOCK_TARGET, new_callable=AsyncMock) as mock:
        response = MagicMock()
        response.text = "Unauthorized"
        mock.side_effect = httpx.HTTPStatusError("401", request=MagicMock(), response=response)
        result = await _get_hunting_schema(None, _CONFIG)

    assert "Schema error" in result
    assert "Unauthorized" in result

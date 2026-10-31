from __future__ import annotations

import asyncio
import json
import stat
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from mcp_mde_kql.presence import TablePresenceCache
from mcp_mde_kql.utils.config import MdeConfig


@pytest.fixture
def config(tmp_path: Path) -> MdeConfig:
    return MdeConfig(
        "tenant", "client", "secret", presence_cache_path=tmp_path / "private" / "presence.json"
    )


def make_client() -> AsyncMock:
    client = AsyncMock()
    client.table_names.return_value = ["Empty", "Present", "Unknown"]
    client.query.side_effect = [
        {"results": []},
        {"results": [{"HasData": True}]},
        ToolError("sensitive upstream details"),
    ]
    return client


async def run_pass(cache: TablePresenceCache) -> None:
    await cache.start()
    await asyncio.wait_for(cache._pass_finished.wait(), 1)
    await cache.close()


async def test_presence_uses_only_existence_probes_and_separates_unknown(config: MdeConfig) -> None:
    client = make_client()
    cache = TablePresenceCache(client, config, _probe_interval=0)
    await run_pass(cache)
    state = cache.snapshot()
    assert state["status"] == "ready"
    assert not state["complete"]
    assert state["with_data"] == ["Present"]
    assert state["empty"] == ["Empty"]
    assert state["unknown"] == ["Unknown"]
    assert state["checked_tables"] == 3 and state["error_count"] == 1
    assert "sensitive" not in str(state)
    for call in client.query.await_args_list:
        assert call.args[0].endswith(" | take 1 | project HasData=true")
        assert call.kwargs == {"timespan": "P7D"}
        assert "count" not in call.args[0].lower()
    path = config.presence_cache_path
    assert path is not None
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    saved = path.read_text()
    assert "secret" not in saved and "HasData" not in saved and "tenant" not in saved
    assert not list(path.parent.glob(".*.json.*"))


async def test_fresh_positive_negative_resume_unknown_retried(config: MdeConfig) -> None:
    initial = TablePresenceCache(make_client(), config, _probe_interval=0)
    await run_pass(initial)
    client = AsyncMock()
    client.table_names.return_value = ["Empty", "Present", "Unknown"]
    client.query.return_value = {"results": []}
    resumed = TablePresenceCache(client, config, _probe_interval=0)
    await run_pass(resumed)
    client.query.assert_awaited_once_with("Unknown | take 1 | project HasData=true", timespan="P7D")
    assert resumed.snapshot()["empty"] == ["Empty", "Unknown"]
    assert resumed.snapshot()["complete"]


async def test_cancelled_partial_pass_is_resumable_and_pending_not_empty(config: MdeConfig) -> None:
    client = AsyncMock()
    client.table_names.return_value = ["A", "B"]
    blocked = asyncio.Event()

    async def query(query: str, timespan: str | None = None) -> dict:
        if query.startswith("A "):
            return {"results": [{"HasData": True}]}
        blocked.set()
        await asyncio.Event().wait()
        return {"results": []}

    client.query.side_effect = query
    cache = TablePresenceCache(client, config, _probe_interval=0)
    await cache.start()
    await asyncio.wait_for(blocked.wait(), 1)
    assert cache.snapshot()["unknown"] == ["B"]
    assert cache.snapshot()["empty"] == []
    await asyncio.wait_for(cache.close(), 0.2)
    resumed_client = AsyncMock()
    resumed_client.table_names.return_value = ["A", "B"]
    resumed_client.query.return_value = {"results": []}
    resumed = TablePresenceCache(resumed_client, config, _probe_interval=0)
    await run_pass(resumed)
    resumed_client.query.assert_awaited_once_with(
        "B | take 1 | project HasData=true", timespan="P7D"
    )
    assert resumed.snapshot()["with_data"] == ["A"]


async def test_metadata_failure_retains_unknown_and_is_safe(config: MdeConfig) -> None:
    client = AsyncMock()
    client.table_names.side_effect = ToolError("secret error")
    cache = TablePresenceCache(client, config, _probe_interval=0)
    await run_pass(cache)
    assert cache.snapshot()["status"] == "error"
    assert not cache.snapshot()["complete"]
    assert "secret" not in str(cache.snapshot())
    client.query.assert_not_awaited()


@pytest.mark.parametrize("names", [["A | take 1"], ["A\n"], ["A"] * 4097])
async def test_invalid_catalog_never_runs_queries(config: MdeConfig, names: list[str]) -> None:
    client = AsyncMock()
    client.table_names.return_value = names
    cache = TablePresenceCache(client, config, _probe_interval=0)
    await run_pass(cache)
    assert cache.snapshot()["status"] == "error"
    client.query.assert_not_awaited()


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"results": None},
        {"results": [{}]},
        {"results": [{"HasData": False}]},
        {"results": [{"HasData": 0}]},
        {"results": [{"HasData": "1"}]},
        {"results": [{"HasData": 1.0}]},
        {"results": [{"HasData": True}] * 2},
    ],
)
async def test_malformed_probe_is_unknown_not_empty(config: MdeConfig, response: dict) -> None:
    client = AsyncMock()
    client.table_names.return_value = ["A"]
    client.query.return_value = response
    cache = TablePresenceCache(client, config, _probe_interval=0)
    await run_pass(cache)
    assert cache.snapshot()["unknown"] == ["A"]
    assert cache.snapshot()["empty_count"] == 0


@pytest.mark.parametrize("kind", ["stale", "wrong_identity", "corrupt", "oversized"])
async def test_invalid_disk_cache_is_ignored(config: MdeConfig, kind: str) -> None:
    cache = TablePresenceCache(make_client(), config, _probe_interval=0)
    await run_pass(cache)
    path = config.presence_cache_path
    assert path is not None
    saved = json.loads(path.read_text())
    if kind == "stale":
        for entry in saved["entries"].values():
            entry["checked_at"] = time.time() - 3601
        path.write_text(json.dumps(saved))
    elif kind == "wrong_identity":
        saved["identity"] = "another tenant"
        path.write_text(json.dumps(saved))
    elif kind == "corrupt":
        path.write_text("{bad json")
    else:
        path.write_text("x" * (2 * 1024 * 1024 + 1))
    client = make_client()
    resumed = TablePresenceCache(client, config, _probe_interval=0)
    await run_pass(resumed)
    assert client.query.await_count == 3


async def test_pacing_is_sequential_and_close_cancels_wait(config: MdeConfig) -> None:
    client = AsyncMock()
    client.table_names.return_value = ["A", "B"]
    client.query.return_value = {"results": []}
    cache = TablePresenceCache(client, config)
    assert cache._interval == 2.1
    await cache.start()
    while client.query.await_count == 0:
        await asyncio.sleep(0)
    await asyncio.sleep(0.02)
    assert client.query.await_count == 1
    await asyncio.wait_for(cache.close(), 0.2)
    assert client.query.await_count == 1
    assert cache.snapshot()["unknown"] == ["B"]


async def test_worker_refreshes_after_ttl_without_restart(config: MdeConfig) -> None:
    client = AsyncMock()
    client.table_names.return_value = ["A"]
    client.query.return_value = {"results": []}
    cache = TablePresenceCache(client, config, _probe_interval=0)
    real_sleep = asyncio.sleep
    second_pass = asyncio.Event()
    clock = [1000.0]

    async def fake_sleep(delay: float) -> None:
        clock[0] += delay
        if client.query.await_count >= 2:
            second_pass.set()
            await asyncio.Event().wait()
        await real_sleep(0)

    with (
        patch("mcp_mde_kql.presence.time.time", side_effect=lambda: clock[0]),
        patch("mcp_mde_kql.presence.asyncio.sleep", side_effect=fake_sleep),
    ):
        await cache.start()
        await asyncio.wait_for(second_pass.wait(), 1)
        await cache.close()
    assert client.table_names.await_count == 2
    assert client.query.await_count == 2


async def test_failed_disk_write_keeps_memory_state(config: MdeConfig) -> None:
    client = AsyncMock()
    client.table_names.return_value = ["A"]
    client.query.return_value = {"results": []}
    cache = TablePresenceCache(client, config, _probe_interval=0)
    with patch("mcp_mde_kql.presence.tempfile.mkstemp", side_effect=OSError("denied")):
        await run_pass(cache)
    assert cache.snapshot()["empty"] == ["A"]
    assert cache.snapshot()["warnings"]


async def test_repeated_failures_stop_batch_and_keep_remaining_pending(config: MdeConfig) -> None:
    client = AsyncMock()
    client.table_names.return_value = ["A", "B", "C", "D", "E"]
    client.query.side_effect = TimeoutError()
    cache = TablePresenceCache(client, config, _probe_interval=0)
    real_timeout = asyncio.timeout
    with patch("mcp_mde_kql.presence.asyncio.timeout", wraps=real_timeout) as deadline:
        await run_pass(cache)
    assert client.query.await_count == 3
    assert [call.args[0] for call in deadline.call_args_list] == [30, 15, 15, 15]
    state = cache.snapshot()
    assert state["status"] == "error"
    assert state["checked_tables"] == state["error_count"] == 3
    assert state["total_tables"] == 5
    assert state["empty_count"] == 0
    assert "D" not in cache._entries and "E" not in cache._entries
    assert any("paused" in warning for warning in state["warnings"])


async def test_graph_sbyte_true_is_presence(config: MdeConfig) -> None:
    client = AsyncMock()
    client.table_names.return_value = ["DeviceInfo"]
    client.query.return_value = {
        "schema": [{"name": "HasData", "type": "SByte"}],
        "results": [{"HasData@odata.type": "#SByte", "HasData": 1}],
    }
    cache = TablePresenceCache(client, config, _probe_interval=0)
    await run_pass(cache)
    assert cache.snapshot()["with_data"] == ["DeviceInfo"]
    assert cache.snapshot()["complete"]
    assert cache.snapshot()["unknown_count"] == 0

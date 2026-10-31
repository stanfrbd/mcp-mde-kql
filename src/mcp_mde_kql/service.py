"""Bounded hunting results and tenant-local schema discovery cache."""

from __future__ import annotations

import asyncio
import re
import time
from collections import OrderedDict
from typing import Any, Protocol

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import JsonValue, TypeAdapter, ValidationError

from mcp_mde_kql.models import HuntingColumn, PresenceProgress, QueryResult, SchemaResult
from mcp_mde_kql.presence import TablePresenceCache

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")
_ROW_ADAPTER = TypeAdapter(list[JsonValue])
_TRUNCATED = "Output limited; narrow the time range, project fewer columns, or summarize in KQL."


class QueryClient(Protocol):
    async def table_names(self) -> list[str]: ...

    async def query(self, query: str, timespan: str | None = None) -> dict[str, Any]: ...


class HuntingService:
    def __init__(
        self,
        client: QueryClient,
        *,
        cache_ttl: float = 300,
        cache_size: int = 128,
        presence: TablePresenceCache | None = None,
    ) -> None:
        self.client = client
        self.presence = presence
        self.cache_ttl = cache_ttl
        self.cache_size = cache_size
        self._schema_cache: OrderedDict[str | None, tuple[float, SchemaResult]] = OrderedDict()
        self._schema_lock = asyncio.Lock()

    async def run_query(
        self,
        query: str,
        max_rows: int = 100,
        max_output_bytes: int = 65536,
        timespan: str | None = None,
    ) -> QueryResult:
        if not query.strip() or len(query) > 100000:
            raise ToolError("query must be nonempty and at most 100000 characters")
        if not 1 <= max_rows <= 1000 or not 1024 <= max_output_bytes <= 262144:
            raise ToolError(
                "Invalid output limits: max_rows=1..1000, max_output_bytes=1024..262144"
            )
        started = time.monotonic()
        response = await self.client.query(query, timespan=timespan)
        elapsed_ms = max(0, round((time.monotonic() - started) * 1000))
        try:
            columns = [HuntingColumn.model_validate(c) for c in response["schema"]]
            upstream_rows = response["results"]
            if not isinstance(upstream_rows, list) or any(
                not isinstance(r, dict) for r in upstream_rows
            ):
                raise ValueError("Invalid rows")
            result = QueryResult(
                columns=columns,
                rows=[],
                returned_rows=0,
                fetched_rows=len(upstream_rows),
                elapsed_ms=elapsed_ms,
                truncated=bool(upstream_rows),
                warnings=[_TRUNCATED] if upstream_rows else [],
            )
            # Reserve truncation metadata. Never cut a cell or change its type.
            if len(result.model_dump_json().encode("utf-8")) > max_output_bytes:
                raise ToolError(
                    "Column schema exceeds output budget; project fewer columns or raise max_output_bytes"
                )
            used_bytes = len(result.model_dump_json().encode("utf-8"))
            for row in upstream_rows[:max_rows]:
                values = _ROW_ADAPTER.validate_python([row.get(c.name) for c in columns])
                row_bytes = len(_ROW_ADAPTER.dump_json(values))
                extra = row_bytes + bool(result.rows)
                # Reserve three bytes for returned_rows growing from 0 to at most 1000.
                if used_bytes + extra + 3 > max_output_bytes:
                    break
                result.rows.append(values)
                result.returned_rows += 1
                used_bytes += extra
            result.truncated = result.returned_rows < result.fetched_rows
            result.warnings = [_TRUNCATED] if result.truncated else []
            return result
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            raise ToolError("Invalid response received from Microsoft Graph") from exc

    async def get_schema(self, table_name: str | None = None) -> SchemaResult:
        if table_name is not None and not _IDENTIFIER.fullmatch(table_name):
            raise ToolError(
                "table_name must be a single table identifier (letters, digits, underscores)"
            )
        if table_name is None and self.presence is not None:
            state = self.presence.snapshot()
            return SchemaResult(
                tables=state["with_data"],
                source="seven_day_presence_cache",
                complete=state["complete"],
                presence=PresenceProgress(
                    status=state["status"],
                    total_tables=state["total_tables"],
                    checked_tables=state["checked_tables"],
                    pending_tables=max(0, state["total_tables"] - state["checked_tables"]),
                    empty_tables=len(state["empty"]),
                    unknown_tables=state["error_count"],
                ),
                warnings=state["warnings"],
            )
        # One bounded lock avoids duplicate discovery without accumulating per-table locks.
        async with self._schema_lock:
            cached = self._schema_cache.get(table_name)
            if cached is not None and cached[0] > time.monotonic():
                self._schema_cache.move_to_end(table_name)
                return cached[1].model_copy(deep=True)
            try:
                if table_name:
                    response = await self.client.query(f"{table_name} | getschema")
                    rows = response["results"]
                    if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
                        raise ValueError("Invalid schema rows")
                    if not rows:
                        raise ToolError("No column schema returned for that table")
                    result = SchemaResult(
                        table_name=table_name,
                        columns=[
                            HuntingColumn(name=r["ColumnName"], type=r["ColumnType"]) for r in rows
                        ],
                        source="getschema",
                        complete=True,
                    )
                else:
                    tables = await self.client.table_names()
                    if not isinstance(tables, list) or any(
                        not isinstance(t, str) or not _IDENTIFIER.fullmatch(t) for t in tables
                    ):
                        raise ValueError("Invalid metadata table names")
                    tables = sorted(set(tables))
                    result = SchemaResult(
                        tables=tables,
                        source="graph_schema_metadata_beta",
                        complete=True,
                        warnings=[
                            "Metadata lists accessible tables; data presence was not checked."
                        ],
                    )
            except (KeyError, TypeError, ValueError, ValidationError) as exc:
                raise ToolError("Invalid schema response received from Microsoft Graph") from exc
            if len(result.model_dump_json().encode("utf-8")) > 65536:
                raise ToolError("Schema response exceeds output budget; request a specific table")
            self._schema_cache[table_name] = (time.monotonic() + self.cache_ttl, result)
            self._schema_cache.move_to_end(table_name)
            while len(self._schema_cache) > self.cache_size:
                self._schema_cache.popitem(last=False)
            return result.model_copy(deep=True)

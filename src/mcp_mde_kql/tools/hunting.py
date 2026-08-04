from __future__ import annotations

import httpx
from mcp.server.fastmcp import FastMCP

from mcp_mde_kql.utils.config import MdeConfig
from mcp_mde_kql.utils.http import run_defender_query


def _render_table(headers: list[str], rows: list[list[str]]) -> str:
    sep = "|" + "|".join("---" for _ in headers) + "|"
    fmt_row = lambda cells: "| " + " | ".join(cells) + " |"  # noqa: E731
    lines = [fmt_row(headers), sep]
    lines.extend(fmt_row(row) for row in rows)
    return "\n".join(lines)


async def _run_hunting_query(query: str, config: MdeConfig) -> str:
    try:
        result = await run_defender_query(query, config)
        schema = result.get("schema", [])
        results = result.get("results", [])

        if not schema and not results:
            return "Query returned no results"

        col_names = [col.get("name", "") for col in schema]
        rows = [[str(row.get(name, "")) for name in col_names] for row in results]
        table = _render_table(col_names, rows)

        stats = result.get("stats", {})
        summary = f"\n{len(results)} row(s)"
        if stats.get("executionTime"):
            summary += f" | {stats['executionTime']}s"

        return table + summary

    except httpx.HTTPStatusError as e:
        return f"Query error: {e.response.text if e.response else str(e)}"
    except Exception as e:
        return f"Query error: {e}"


async def _get_hunting_schema(table_name: str | None, config: MdeConfig) -> str:
    try:
        if table_name:
            result = await run_defender_query(f"{table_name} | getschema", config)
            rows = result.get("results", [])
            if not rows:
                return f"Table '{table_name}' not found"
            table = _render_table(
                ["Column", "Type"],
                [[r.get("ColumnName", ""), r.get("ColumnType", "")] for r in rows],
            )
            return f"Schema for {table_name}\n\n{table}"

        result = await run_defender_query("search * | distinct $table | sort by $table asc", config)
        tables = [r.get("$table", "") for r in result.get("results", []) if r.get("$table")]
        if not tables:
            return "Could not retrieve schema"

        table = _render_table(["Table"], [[t] for t in tables])
        return f"Available Advanced Hunting Tables\n\n{table}\n\nUse get_hunting_schema with table_name to see columns."

    except httpx.HTTPStatusError as e:
        return f"Schema error: {e.response.text if e.response else str(e)}"
    except Exception as e:
        return f"Schema error: {e}"


def register_hunting_tools(mcp: FastMCP, config: MdeConfig) -> None:
    @mcp.tool()
    async def run_hunting_query(query: str) -> str:
        """
        Execute a KQL (Kusto Query Language) query against Microsoft Defender Advanced Hunting.
        Use this to investigate security events across endpoints, email, identity, and cloud apps.
        Always call get_hunting_schema first to understand available tables and columns.

        Args:
            query: The KQL query to execute.
        """
        return await _run_hunting_query(query, config)

    @mcp.tool()
    async def get_hunting_schema(table_name: str | None = None) -> str:
        """
        Get the Advanced Hunting schema with available tables and columns.
        Call this before writing queries to understand what data is available.
        Returns table names, column names, and data types.

        Args:
            table_name: Get detailed schema for a specific table. Omit to list all tables.
        """
        return await _get_hunting_schema(table_name, config)

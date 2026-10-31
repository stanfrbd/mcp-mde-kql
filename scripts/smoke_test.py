"""Opt-in live MCP smoke test. Prints metadata only, never returned event values.

Run after installing the package:
python scripts/smoke_test.py --env-file .env --command mcp-mde-kql
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from mcp import Client, StdioServerParameters


async def smoke(command: str, env_file: Path) -> None:
    target = StdioServerParameters(
        command=command,
        args=["--env-file", str(env_file.resolve())],
        env={"MDE_PRESENCE_ENABLED": "false"},
    )
    async with Client(target, read_timeout_seconds=240) as client:
        tools = await client.list_tools()
        assert {t.name for t in tools.tools} == {"run_hunting_query", "get_hunting_schema"}
        print("PASS installed stdio discovery: 2 tools", flush=True)
        schema = await client.call_tool("get_hunting_schema", {"table_name": "DeviceInfo"})
        assert not schema.is_error, "DeviceInfo schema lookup failed"
        print("PASS DeviceInfo schema", flush=True)
        observed = await client.call_tool("get_hunting_schema", {})
        assert not observed.is_error, "Metadata table discovery failed"
        assert observed.structured_content["complete"] is True
        cached = await client.call_tool("get_hunting_schema", {})
        assert cached.structured_content == observed.structured_content
        print("PASS metadata table discovery and cached result", flush=True)
        examples = {
            "constant": "print Value=1, Label='smoke', Flag=true",
            "native values": 'print Payload=dynamic({"nested":[1,true,null]}), Label="é漢字|line\\nnext"',
            "byte budget": 'print Payload=strcat_array(repeat("x", 2000), "")',
            "empty": "DeviceInfo | where Timestamp > ago(1h) | take 0",
            "device aggregate": "DeviceInfo | where Timestamp > ago(1d) | summarize Events=count()",
            "process aggregate": "DeviceProcessEvents | where Timestamp > ago(1h) | summarize Events=count() by ActionType | top 3 by Events",
            "network aggregate": "DeviceNetworkEvents | where Timestamp > ago(1h) | summarize Events=count() by Protocol | top 3 by Events",
            "bounded preview": "print Value=dynamic([1,2,3]) | mv-expand Value",
        }
        for label, query in examples.items():
            result = await client.call_tool(
                "run_hunting_query",
                {
                    "query": query,
                    "timespan": "P1D",
                    "max_rows": 1 if label == "bounded preview" else 5,
                    "max_output_bytes": 1024 if label == "byte budget" else 65536,
                },
            )
            assert not result.is_error, f"{label} failed"
            data = result.structured_content
            assert isinstance(data, dict) and isinstance(data["rows"], list)
            assert data["returned_rows"] == len(data["rows"])
            assert data["returned_rows"] <= 5
            if label == "empty":
                assert data["returned_rows"] == 0
            if label == "bounded preview":
                assert data["returned_rows"] == 1 and data["truncated"]
            if label == "byte budget":
                assert data["returned_rows"] == 0 and data["truncated"]
            if label == "native values":
                assert isinstance(data["rows"][0][0], dict)
            print(
                f"PASS {label}: rows={data['returned_rows']}, truncated={data['truncated']}",
                flush=True,
            )
        error = await client.call_tool(
            "run_hunting_query",
            {
                "query": "DeviceInfo | project ThisColumnDoesNotExistSmokeTest",
            },
        )
        assert error.is_error, "Invalid KQL should return an MCP tool error"
        print("PASS invalid KQL error signaling", flush=True)
        invalid = await client.call_tool(
            "get_hunting_schema", {"table_name": "DeviceInfo | take 1"}
        )
        assert invalid.is_error
        print("PASS invalid schema identifier", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--command", default="mcp-mde-kql")
    args = parser.parse_args()
    asyncio.run(smoke(args.command, args.env_file))


if __name__ == "__main__":
    main()

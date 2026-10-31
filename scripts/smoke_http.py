"""Opt-in authenticated live HTTP/SSE transport checks; prints no event values."""

from __future__ import annotations

import argparse
import asyncio
import os
import secrets
import socket
from pathlib import Path

import httpx2
from mcp import Client
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client


async def check(command: str, env_file: Path, transport: str) -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    path = "/sse" if transport == "sse" else "/mcp"
    url = f"http://127.0.0.1:{port}{path}"
    token = secrets.token_urlsafe(48)
    env = dict(os.environ, MCP_AUTH_TOKEN=token, MCP_PUBLIC_URL=url, MDE_PRESENCE_ENABLED="false")
    proc = await asyncio.create_subprocess_exec(
        command,
        "--env-file",
        str(env_file.resolve()),
        "--transport",
        transport,
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        env=env,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        async with httpx2.AsyncClient(timeout=2) as http:
            for _ in range(100):
                if proc.returncode is not None:
                    raise RuntimeError("Transport server exited during startup")
                try:
                    response = await http.get(url)
                    assert response.status_code == 401
                    break
                except httpx2.ConnectError:
                    await asyncio.sleep(0.1)
            else:
                raise TimeoutError("Transport startup timed out")
        print(f"PASS {transport}: unauthenticated access rejected", flush=True)
        for mode in ["legacy"] if transport == "sse" else ["auto", "legacy"]:
            async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}) as http:
                connection = (
                    sse_client(url, headers={"Authorization": f"Bearer {token}"})
                    if transport == "sse"
                    else streamable_http_client(url, http_client=http)
                )
                async with Client(connection, mode=mode, read_timeout_seconds=240) as client:
                    tools = await client.list_tools()
                    assert len(tools.tools) == 2
                    result = await client.call_tool(
                        "run_hunting_query",
                        {
                            "query": "DeviceInfo | where Timestamp > ago(1h) | summarize Events=count()",
                            "timespan": "P1D",
                            "max_rows": 1,
                        },
                    )
                    assert not result.is_error
                    assert result.structured_content["returned_rows"] == 1
                    print(f"PASS {transport}/{mode}: discovery and live aggregate", flush=True)
    finally:
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=10)
            except TimeoutError:
                proc.kill()
                await proc.wait()


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--command", default="mcp-mde-kql")
    args = parser.parse_args()
    for transport in ("streamable-http", "sse"):
        await check(args.command, args.env_file, transport)


if __name__ == "__main__":
    asyncio.run(main())

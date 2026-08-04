from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from mcp_mde_kql.tools.hunting import register_hunting_tools
from mcp_mde_kql.utils.config import MdeConfig


def create_server(config: MdeConfig) -> FastMCP:
    mcp = FastMCP("mcp-mde-kql")
    register_hunting_tools(mcp, config)

    if config.host:
        mcp.settings.host = config.host
    if config.port:
        mcp.settings.port = config.port
    if config.mount_path:
        mcp.settings.mount_path = config.mount_path
    if config.sse_path:
        mcp.settings.sse_path = config.sse_path
    if config.streamable_http_path:
        mcp.settings.streamable_http_path = config.streamable_http_path

    return mcp


def run_server(config: MdeConfig) -> None:
    mcp = create_server(config)
    mcp.run(transport=config.transport)

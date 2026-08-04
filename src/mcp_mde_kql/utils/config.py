from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(slots=True)
class MdeConfig:
    tenant_id: str
    client_id: str
    client_secret: str
    transport: str = "stdio"
    host: str | None = None
    port: int | None = None
    mount_path: str | None = None
    sse_path: str | None = None
    streamable_http_path: str | None = None


def resolve_config() -> MdeConfig:
    tenant_id = os.environ.get("MDE_TENANT_ID", "")
    client_id = os.environ.get("MDE_CLIENT_ID", "")
    client_secret = os.environ.get("MDE_CLIENT_SECRET", "")

    if not tenant_id:
        raise ValueError("MDE_TENANT_ID environment variable is required")
    if not client_id:
        raise ValueError("MDE_CLIENT_ID environment variable is required")
    if not client_secret:
        raise ValueError("MDE_CLIENT_SECRET environment variable is required")

    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    host = os.environ.get("MCP_HOST")
    raw_port = os.environ.get("MCP_PORT")
    port = int(raw_port) if raw_port else None
    mount_path = os.environ.get("MCP_MOUNT_PATH")
    sse_path = os.environ.get("MCP_SSE_PATH")
    streamable_http_path = os.environ.get("MCP_STREAMABLE_HTTP_PATH")

    return MdeConfig(
        tenant_id=tenant_id,
        client_id=client_id,
        client_secret=client_secret,
        transport=transport,
        host=host,
        port=port,
        mount_path=mount_path,
        sse_path=sse_path,
        streamable_http_path=streamable_http_path,
    )

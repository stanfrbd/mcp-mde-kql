from __future__ import annotations

import os
from dataclasses import dataclass, field
from hashlib import sha256
from ipaddress import ip_address
from pathlib import Path
from typing import Literal, cast
from urllib.parse import urlsplit

Transport = Literal["stdio", "sse", "streamable-http"]


@dataclass(slots=True)
class MdeConfig:
    tenant_id: str
    client_id: str
    client_secret: str = field(repr=False)
    transport: Transport = "stdio"
    host: str | None = None
    port: int | None = None
    mount_path: str | None = None
    sse_path: str | None = None
    streamable_http_path: str | None = None
    max_concurrency: int = 4
    query_timeout: float = 180.0
    max_retries: int = 2
    auth_token: str | None = field(default=None, repr=False)
    public_url: str | None = None
    presence_enabled: bool = True
    presence_days: int = 7
    presence_request_interval: float = 2.1
    presence_cache_path: Path | None = None
    presence_cache_ttl: float = 3600.0

    def __post_init__(self) -> None:
        for name in ("tenant_id", "client_id", "client_secret"):
            if not getattr(self, name).strip():
                raise ValueError(f"MDE_{name.upper()} environment variable is required")
        if self.presence_days != 7:
            raise ValueError("Table presence checks use a fixed seven-day window")
        if not 2 <= self.presence_request_interval <= 3600:
            raise ValueError("MDE_PRESENCE_REQUEST_INTERVAL must be between 2 and 3600 seconds")
        if not 1 <= self.presence_cache_ttl <= 604800:
            raise ValueError("MDE_PRESENCE_CACHE_TTL must be between 1 and 604800 seconds")
        if self.presence_cache_path is None:
            identity = sha256(f"{self.tenant_id}:{self.client_id}".encode()).hexdigest()[:24]
            root = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))
            self.presence_cache_path = root / "mcp-mde-kql" / f"table-presence-{identity}.json"
        if self.transport not in ("stdio", "sse", "streamable-http"):
            raise ValueError("MCP_TRANSPORT must be stdio, sse, or streamable-http")
        if self.mount_path:
            raise ValueError("MCP_MOUNT_PATH was removed in SDK v2; use ASGI root_path mounting")
        if self.port is not None and not 1 <= self.port <= 65535:
            raise ValueError("MCP_PORT must be between 1 and 65535")
        if not 1 <= self.max_concurrency <= 32:
            raise ValueError("MDE_MAX_CONCURRENCY must be between 1 and 32")
        if not 1 <= self.query_timeout <= 600:
            raise ValueError("MDE_QUERY_TIMEOUT must be between 1 and 600 seconds")
        if not 0 <= self.max_retries <= 5:
            raise ValueError("MDE_MAX_RETRIES must be between 0 and 5")
        if self.auth_token is not None and len(self.auth_token) < 32:
            raise ValueError("MCP_AUTH_TOKEN must contain at least 32 characters")
        if self.public_url is not None:
            try:
                url = urlsplit(self.public_url)
                valid_url = (
                    url.scheme in ("http", "https")
                    and bool(url.hostname)
                    and url.username is None
                    and url.password is None
                    and url.port != 0
                    and not url.fragment
                    and not url.query
                    and not any(char.isspace() for char in self.public_url)
                )
            except ValueError:
                valid_url = False
            if not valid_url:
                raise ValueError(
                    "MCP_PUBLIC_URL must be an http(s) URL without userinfo, query, or fragment"
                )
        host = self.host or "127.0.0.1"
        try:
            loopback = ip_address(host.strip("[]")).is_loopback
        except ValueError:
            loopback = host.lower().rstrip(".") == "localhost"
        if self.transport != "stdio" and not loopback and not (self.auth_token and self.public_url):
            raise ValueError("Non-loopback HTTP binding requires MCP_AUTH_TOKEN and MCP_PUBLIC_URL")
        for name in ("mount_path", "sse_path", "streamable_http_path"):
            value = getattr(self, name)
            if value is not None and not value.startswith("/"):
                raise ValueError(f"MCP_{name.upper()} must start with /")


def resolve_config(**overrides: str | int | float | None) -> MdeConfig:
    """Resolve explicit CLI values before environment variables; never read .env implicitly."""

    def value(
        name: str, env: str, default: str | int | float | None = None
    ) -> str | int | float | None:
        explicit = overrides.get(name)
        return explicit if explicit is not None else os.environ.get(env, default)

    enabled = str(value("presence_enabled", "MDE_PRESENCE_ENABLED", "true")).lower()
    if enabled not in ("true", "false", "1", "0"):
        raise ValueError("MDE_PRESENCE_ENABLED must be true or false")
    cache_path = value("presence_cache_path", "MDE_PRESENCE_CACHE_PATH")
    raw_port = value("port", "MCP_PORT")
    return MdeConfig(
        tenant_id=str(value("tenant_id", "MDE_TENANT_ID", "")),
        client_id=str(value("client_id", "MDE_CLIENT_ID", "")),
        client_secret=str(value("client_secret", "MDE_CLIENT_SECRET", "")),
        transport=cast(Transport, value("transport", "MCP_TRANSPORT", "stdio")),
        host=cast(str | None, value("host", "MCP_HOST")),
        port=int(raw_port) if raw_port is not None else None,
        mount_path=cast(str | None, value("mount_path", "MCP_MOUNT_PATH")),
        sse_path=cast(str | None, value("sse_path", "MCP_SSE_PATH")),
        streamable_http_path=cast(
            str | None, value("streamable_http_path", "MCP_STREAMABLE_HTTP_PATH")
        ),
        max_concurrency=int(value("max_concurrency", "MDE_MAX_CONCURRENCY", 4)),  # type: ignore[arg-type]
        query_timeout=float(value("query_timeout", "MDE_QUERY_TIMEOUT", 180)),  # type: ignore[arg-type]
        auth_token=cast(str | None, value("auth_token", "MCP_AUTH_TOKEN")),
        public_url=cast(str | None, value("public_url", "MCP_PUBLIC_URL")),
        presence_enabled=enabled in ("true", "1"),
        presence_cache_path=Path(str(cache_path)).expanduser() if cache_path is not None else None,
        presence_request_interval=float(
            str(value("presence_request_interval", "MDE_PRESENCE_REQUEST_INTERVAL", 2.1))
        ),
        presence_cache_ttl=float(value("presence_cache_ttl", "MDE_PRESENCE_CACHE_TTL", 3600)),  # type: ignore[arg-type]
        max_retries=int(value("max_retries", "MDE_MAX_RETRIES", 2)),  # type: ignore[arg-type]
    )

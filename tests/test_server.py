from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from mcp_mde_kql.server import BearerTokenVerifier, create_server, run_server
from mcp_mde_kql.utils.config import MdeConfig

_TOKEN = "test-token-that-is-at-least-32-characters"
_RESOURCE = "http://localhost:8000/mcp"


@pytest.fixture(autouse=True)
def package_version():
    with patch("mcp_mde_kql.server.version", return_value="1.0.0"):
        yield


def config(**kwargs):
    return MdeConfig("tenant", "client", "secret", **kwargs)


@pytest.mark.asyncio
async def test_bearer_verifier_rejects_invalid_tokens():
    verifier = BearerTokenVerifier(_TOKEN, _RESOURCE)
    assert await verifier.verify_token("wrong-token") is None
    assert await verifier.verify_token("unicode-\N{SNOWMAN}") is None
    access = await verifier.verify_token(_TOKEN)
    assert access is not None
    assert access.client_id == "configured-client"
    assert access.scopes == ["hunting:read"]
    assert access.resource == _RESOURCE


def test_create_server_auth_configuration():
    server = create_server(config(auth_token=_TOKEN, public_url=_RESOURCE))
    assert server.settings.auth is not None
    assert str(server.settings.auth.resource_server_url) == _RESOURCE
    assert server.settings.auth.required_scopes == ["hunting:read"]
    assert server.settings.auth.validate_token_resource is True
    assert create_server(config()).settings.auth is None


@pytest.mark.parametrize(
    "options, expected",
    [
        ({}, {"transport": "stdio"}),
        (
            {"transport": "sse", "port": 9000, "sse_path": "/events"},
            {"transport": "sse", "host": "127.0.0.1", "port": 9000, "sse_path": "/events"},
        ),
        (
            {"transport": "streamable-http", "streamable_http_path": "/hunting"},
            {
                "transport": "streamable-http",
                "host": "127.0.0.1",
                "port": 8000,
                "streamable_http_path": "/hunting",
                "stateless_http": True,
                "json_response": True,
            },
        ),
    ],
)
def test_run_server_transport_options(options, expected):
    server = MagicMock()
    with patch("mcp_mde_kql.server.create_server", return_value=server):
        run_server(config(**options))
    server.run.assert_called_once_with(**expected)


def test_removed_sse_mount_configuration_fails():
    server = MagicMock()
    with patch("mcp_mde_kql.server.create_server", return_value=server):
        with pytest.raises(ValueError, match="root_path"):
            run_server(config(transport="sse", mount_path="/prefix"))
    server.run.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("token, status", [(None, 401), ("invalid-token", 401), (_TOKEN, 200)])
async def test_http_transport_enforces_authentication(token, status):
    server = create_server(
        config(transport="streamable-http", auth_token=_TOKEN, public_url=_RESOURCE)
    )
    app = server.streamable_http_app(json_response=True, stateless_http=True)
    headers = {"Accept": "application/json, text/event-stream"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    with patch("mcp_mde_kql.server.DefenderClient", return_value=mock_client):
        async with server.session_manager.run():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://localhost:8000"
            ) as http:
                response = await http.post(
                    "/mcp",
                    headers=headers,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2025-11-25",
                            "capabilities": {},
                            "clientInfo": {"name": "test", "version": "1.0"},
                        },
                    },
                )
    assert response.status_code == status
    if status == 200:
        assert response.json()["result"]["serverInfo"]["name"] == "mcp-mde-kql"
    else:
        assert "www-authenticate" in response.headers


def test_server_suppresses_verbose_dependency_logging():
    import logging

    server = create_server(config())
    assert server.settings.log_level == "WARNING"
    for name in ("azure", "azure.core.pipeline.policies.http_logging_policy", "httpx", "httpcore"):
        assert logging.getLogger(name).getEffectiveLevel() >= logging.WARNING


@pytest.mark.asyncio
async def test_auth_resource_uses_canonical_url():
    server = create_server(config(auth_token=_TOKEN, public_url="HTTP://LOCALHOST:8000"))
    access = await server._token_verifier.verify_token(_TOKEN)
    assert access.resource == str(server.settings.auth.resource_server_url)

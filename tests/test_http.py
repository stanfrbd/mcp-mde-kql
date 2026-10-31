from unittest.mock import AsyncMock

import httpx
import pytest
from azure.core.credentials import AccessToken
from azure.core.exceptions import ClientAuthenticationError
from mcp.server.mcpserver.exceptions import ToolError

from mcp_mde_kql.utils.config import MdeConfig
from mcp_mde_kql.utils.http import DefenderClient, _retry_delay


@pytest.fixture
def config():
    return MdeConfig("tenant", "client", "secret", max_retries=1)


@pytest.fixture
def credential():
    mock = AsyncMock()
    mock.get_token.return_value = AccessToken("private-token", 9999999999)
    return mock


@pytest.mark.asyncio
async def test_query_retries_throttling_and_preserves_payload(config, credential):
    requests = []

    async def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"results": [{"value": 42}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        async with DefenderClient(config, http_client=http, credential=credential) as client:
            assert await client.query("DeviceEvents | take 1", "P1D") == {
                "results": [{"value": 42}]
            }
        assert not http.is_closed
    assert len(requests) == 2
    assert b'"Timespan":"P1D"' in requests[0].content
    credential.close.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 403, 429, 500])
async def test_upstream_errors_are_sanitized(status, config, credential):
    config.max_retries = 0
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(status, text="secret backend diagnostic")
        )
    ) as http:
        async with DefenderClient(config, http_client=http, credential=credential) as client:
            with pytest.raises(ToolError) as error:
                await client.query("DeviceEvents")
    assert "secret" not in str(error.value)


@pytest.mark.asyncio
async def test_auth_errors_are_sanitized(config, credential):
    credential.get_token.side_effect = ClientAuthenticationError("private details")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={}))
    ) as http:
        async with DefenderClient(config, http_client=http, credential=credential) as client:
            with pytest.raises(ToolError, match="authentication failed"):
                await client.query("DeviceEvents")


@pytest.mark.asyncio
async def test_total_deadline_includes_retry_wait(config, credential):
    config.query_timeout = 0.01
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(429, headers={"Retry-After": "60"})
        )
    ) as http:
        async with DefenderClient(config, http_client=http, credential=credential) as client:
            with pytest.raises(ToolError, match="timed out"):
                await client.query("DeviceEvents")


def test_retry_delay():
    assert _retry_delay("3", 0) == 3
    assert _retry_delay("garbage", 2) == 4
    assert _retry_delay("nan", 1) == 2
    assert _retry_delay("-5", 0) == 0


@pytest.mark.asyncio
async def test_owned_resources_are_closed(config, credential, monkeypatch):
    from mcp_mde_kql.utils import http as module

    http = AsyncMock(spec=httpx.AsyncClient)
    monkeypatch.setattr(module, "create_credential", lambda config: credential)
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kwargs: http)
    async with DefenderClient(config):
        pass
    http.aclose.assert_awaited_once()
    credential.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_concurrency_is_bounded(config, credential):
    import asyncio

    config.max_concurrency = 2
    active = 0
    peak = 0

    async def handler(request):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return httpx.Response(200, json={"results": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        async with DefenderClient(config, http_client=http, credential=credential) as client:
            await asyncio.gather(*(client.query("DeviceEvents") for _ in range(6)))
    assert peak == 2


@pytest.mark.asyncio
async def test_cancelled_query_releases_concurrency_slot(config, credential):
    import asyncio

    config.max_concurrency = 1
    started = asyncio.Event()
    requests = 0

    async def handler(request):
        nonlocal requests
        requests += 1
        if requests == 1:
            started.set()
            await asyncio.Event().wait()
        return httpx.Response(200, json={"results": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        async with DefenderClient(config, http_client=http, credential=credential) as client:
            task = asyncio.create_task(client.query("DeviceEvents"))
            await asyncio.wait_for(started.wait(), timeout=1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert await asyncio.wait_for(client.query("DeviceEvents"), timeout=1) == {
                "results": []
            }
    assert requests == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_type, message",
    [
        (httpx.ConnectError, "Could not connect"),
        (httpx.ReadError, "Could not connect"),
        (httpx.ConnectTimeout, "timed out"),
        (httpx.ReadTimeout, "timed out"),
    ],
)
async def test_transport_errors_are_sanitized(error_type, message, config, credential):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        raise error_type("private token=secret backend details", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        async with DefenderClient(config, http_client=http, credential=credential) as client:
            with pytest.raises(ToolError, match=message) as error:
                await client.query("DeviceEvents")
    assert "secret" not in str(error.value)
    assert "private" not in str(error.value)
    assert calls == 1  # Avoid replaying queries after ambiguous transport failures.


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [b"not-json secret backend", b"[]", b"null", b'"secret"'])
async def test_invalid_upstream_payload_is_sanitized(body, config, credential):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body))
    ) as http:
        async with DefenderClient(config, http_client=http, credential=credential) as client:
            with pytest.raises(ToolError, match="invalid response") as error:
                await client.query("DeviceEvents")
    assert "secret" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status, expected_calls", [(429, 3), (502, 3), (503, 3), (504, 3), (400, 1)]
)
async def test_retry_budget_and_non_retryable_status(status, expected_calls, config, credential):
    config.max_retries = 2
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(status, headers={"Retry-After": "0"}, text="private diagnostic")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        async with DefenderClient(config, http_client=http, credential=credential) as client:
            with pytest.raises(ToolError) as error:
                await client.query("DeviceEvents")
    assert calls == expected_calls
    assert "private" not in str(error.value)


@pytest.mark.asyncio
async def test_table_names_uses_metadata_and_follows_pages(config, credential):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.method == "GET"
        assert request.url.path == "/beta/security/getHuntingSchemaTables"
        if len(requests) == 1:
            assert request.url.params["$select"] == "name"
            return httpx.Response(
                200,
                json={
                    "value": [{"name": "DeviceInfo"}],
                    "@odata.nextLink": "https://graph.microsoft.com/beta/security/getHuntingSchemaTables?$skiptoken=next",
                },
            )
        return httpx.Response(
            200, json={"value": [{"name": "DeviceEvents"}, {"name": "DeviceInfo"}]}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        async with DefenderClient(config, http_client=http, credential=credential) as client:
            assert await client.table_names() == ["DeviceEvents", "DeviceInfo"]
    assert len(requests) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "link",
    [
        "https://attacker.example/beta/security/getHuntingSchemaTables",
        "http://graph.microsoft.com/beta/security/getHuntingSchemaTables",
        "https://graph.microsoft.com:444/beta/security/getHuntingSchemaTables",
        "https://graph.microsoft.com/v1.0/me",
        "https://user:pass@graph.microsoft.com/beta/security/getHuntingSchemaTables",
        "/beta/security/getHuntingSchemaTables",
        42,
    ],
)
async def test_table_names_rejects_unsafe_pagination_before_sending_token(link, config, credential):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"value": [], "@odata.nextLink": link})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        async with DefenderClient(config, http_client=http, credential=credential) as client:
            with pytest.raises(ToolError, match="pagination"):
                await client.table_names()
    assert len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data",
    [{}, {"value": None}, {"value": ["DeviceInfo"]}, {"value": [{"name": "DeviceInfo | take 1"}]}],
)
async def test_table_names_validates_metadata(data, config, credential):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=data))
    ) as http:
        async with DefenderClient(config, http_client=http, credential=credential) as client:
            with pytest.raises(ToolError, match="invalid"):
                await client.table_names()


@pytest.mark.asyncio
async def test_table_names_pagination_is_bounded(config, credential):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={
                "value": [],
                "@odata.nextLink": f"https://graph.microsoft.com/beta/security/getHuntingSchemaTables?$skiptoken={calls}",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        async with DefenderClient(config, http_client=http, credential=credential) as client:
            with pytest.raises(ToolError, match="pagination limit"):
                await client.table_names()
    assert calls == 20

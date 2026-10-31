from __future__ import annotations

import asyncio
import math
import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit

import httpx
from azure.core.credentials_async import AsyncTokenCredential
from azure.core.exceptions import AzureError
from mcp.server.mcpserver.exceptions import ToolError

from mcp_mde_kql.utils.auth import DEFENDER_SCOPE, create_credential
from mcp_mde_kql.utils.config import MdeConfig

_QUERY_URL = "https://graph.microsoft.com/v1.0/security/runHuntingQuery"
_SCHEMA_URL = "https://graph.microsoft.com/beta/security/getHuntingSchemaTables?$select=name"
_MAX_SCHEMA_PAGES = 20
_RETRY_STATUSES = {429, 502, 503, 504}


def _retry_delay(value: str | None, attempt: int) -> float:
    if value:
        try:
            delay = float(value)
        except ValueError:
            try:
                delay = (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds()
            except (ValueError, TypeError, OverflowError):
                delay = float(2**attempt)
        if math.isfinite(delay):
            return max(0.0, delay)
    return float(2**attempt)


class DefenderClient:
    """Lifespan-owned Graph client. Injected clients/credentials remain caller-owned."""

    def __init__(
        self,
        config: MdeConfig,
        *,
        http_client: httpx.AsyncClient | None = None,
        credential: AsyncTokenCredential | None = None,
    ) -> None:
        self.config = config
        self._http = http_client
        self._credential = credential
        self._owns_http = http_client is None
        self._owns_credential = credential is None
        self._semaphore = asyncio.Semaphore(config.max_concurrency)

    async def __aenter__(self) -> DefenderClient:
        if self._credential is None:
            self._credential = create_credential(self.config)
        if self._http is None:
            self._http = httpx.AsyncClient(
                timeout=httpx.Timeout(self.config.query_timeout, connect=10.0),
                limits=httpx.Limits(
                    max_connections=self.config.max_concurrency,
                    max_keepalive_connections=self.config.max_concurrency,
                ),
            )
        return self

    async def __aexit__(self, *args: object) -> None:
        try:
            if self._owns_http and self._http is not None:
                await self._http.aclose()
        finally:
            if self._owns_credential and self._credential is not None:
                await self._credential.close()

    async def query(self, query: str, timespan: str | None = None) -> dict[str, Any]:
        payload = {"Query": query}
        if timespan is not None:
            payload["Timespan"] = timespan
        return await self._request("POST", _QUERY_URL, payload)

    async def table_names(self) -> list[str]:
        """Read schema metadata without scanning hunting event tables."""
        names: set[str] = set()
        next_url: str | None = _SCHEMA_URL
        visited: set[str] = set()
        try:
            async with asyncio.timeout(self.config.query_timeout):
                for _ in range(_MAX_SCHEMA_PAGES):
                    if next_url is None:
                        return sorted(names)
                    if next_url in visited or not _safe_schema_url(next_url):
                        raise ToolError(
                            "Defender schema pagination returned an unsafe or repeated URL."
                        )
                    visited.add(next_url)
                    data = await self._request("GET", next_url)
                    rows = data.get("value")
                    if not isinstance(rows, list):
                        raise ToolError("Defender schema metadata returned an invalid response.")
                    for row in rows:
                        name = row.get("name") if isinstance(row, dict) else None
                        if not isinstance(name, str) or not re.fullmatch(
                            r"[A-Za-z_][A-Za-z0-9_]*", name
                        ):
                            raise ToolError(
                                "Defender schema metadata returned an invalid table name."
                            )
                        names.add(name)
                    link = data.get("@odata.nextLink")
                    if link is not None and not isinstance(link, str):
                        raise ToolError("Defender schema pagination returned an invalid response.")
                    next_url = link
                if next_url is None:
                    return sorted(names)
                raise ToolError("Defender schema metadata exceeded the pagination limit.")
        except TimeoutError:
            raise ToolError("Defender schema metadata timed out. Retry later.") from None

    async def _request(
        self, method: str, url: str, payload: dict[str, str] | None = None
    ) -> dict[str, Any]:
        if self._http is None or self._credential is None:
            raise RuntimeError("Use DefenderClient within its async context manager")
        try:
            async with asyncio.timeout(self.config.query_timeout), self._semaphore:
                token = await self._credential.get_token(DEFENDER_SCOPE)
                for attempt in range(self.config.max_retries + 1):
                    response = await self._http.request(
                        method,
                        url,
                        headers={"Authorization": f"Bearer {token.token}"},
                        json=payload,
                    )
                    if (
                        response.status_code in _RETRY_STATUSES
                        and attempt < self.config.max_retries
                    ):
                        await asyncio.sleep(
                            _retry_delay(response.headers.get("Retry-After"), attempt)
                        )
                        continue
                    response.raise_for_status()
                    data = response.json()
                    if not isinstance(data, dict):
                        raise ToolError(
                            "Defender returned an invalid response. Try a narrower query."
                        )
                    return data
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            messages = {
                400: "Invalid hunting query. Check KQL syntax and available table/column names.",
                401: "Defender authentication failed. Ask the administrator to check credentials.",
                403: "Defender access denied. Check application permissions and admin consent.",
                429: "Defender rate limit reached. Wait before retrying the query.",
            }
            raise ToolError(
                messages.get(status, f"Defender request failed (HTTP {status}). Retry later.")
            ) from None
        except (TimeoutError, httpx.TimeoutException):
            raise ToolError(
                "Defender query timed out. Narrow the time range or simplify the query."
            ) from None
        except httpx.RequestError:
            raise ToolError("Could not connect to Defender. Retry later.") from None
        except AzureError:
            raise ToolError(
                "Defender authentication failed. Ask the administrator to check credentials."
            ) from None
        except ValueError:
            raise ToolError("Defender returned an invalid response. Retry later.") from None
        raise ToolError("Defender query could not be completed.")


def _safe_schema_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        return (
            parsed.scheme == "https"
            and parsed.hostname == "graph.microsoft.com"
            and parsed.port in (None, 443)
            and parsed.username is None
            and parsed.password is None
            and parsed.path == "/beta/security/getHuntingSchemaTables"
            and not parsed.fragment
            and not any(char.isspace() for char in url)
        )
    except ValueError:
        return False

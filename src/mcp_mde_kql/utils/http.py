from __future__ import annotations

from typing import Any

import httpx

from mcp_mde_kql.utils.auth import get_access_token
from mcp_mde_kql.utils.config import MdeConfig

_DEFENDER_API_BASE = "https://graph.microsoft.com/v1.0"


async def run_defender_query(query: str, config: MdeConfig) -> dict[str, Any]:
    token = get_access_token(config)
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{_DEFENDER_API_BASE}/security/runHuntingQuery",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={"Query": query},
            timeout=120.0,
        )
        response.raise_for_status()
        return response.json()  # type: ignore[no-any-return]

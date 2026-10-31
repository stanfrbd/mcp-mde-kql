from __future__ import annotations

from azure.identity.aio import ClientSecretCredential

from mcp_mde_kql.utils.config import MdeConfig

DEFENDER_SCOPE = "https://graph.microsoft.com/.default"


def create_credential(config: MdeConfig) -> ClientSecretCredential:
    """Create an async credential owned and closed by the server lifespan."""
    return ClientSecretCredential(
        tenant_id=config.tenant_id,
        client_id=config.client_id,
        client_secret=config.client_secret,
    )

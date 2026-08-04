from __future__ import annotations

from azure.identity import ClientSecretCredential

from mcp_mde_kql.utils.config import MdeConfig

_DEFENDER_SCOPE = "https://graph.microsoft.com/.default"

_credential: ClientSecretCredential | None = None
_credential_key: tuple[str, str, str] | None = None


def get_access_token(config: MdeConfig) -> str:
    global _credential, _credential_key
    key = (config.tenant_id, config.client_id, config.client_secret)
    if _credential is None or _credential_key != key:
        _credential = ClientSecretCredential(
            tenant_id=config.tenant_id,
            client_id=config.client_id,
            client_secret=config.client_secret,
        )
        _credential_key = key
    return _credential.get_token(_DEFENDER_SCOPE).token

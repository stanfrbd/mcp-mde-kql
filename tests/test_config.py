import pytest

from mcp_mde_kql.utils.config import resolve_config


def test_resolve_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MDE_TENANT_ID", "tid")
    monkeypatch.setenv("MDE_CLIENT_ID", "cid")
    monkeypatch.setenv("MDE_CLIENT_SECRET", "secret")

    config = resolve_config()

    assert config.tenant_id == "tid"
    assert config.client_id == "cid"
    assert config.client_secret == "secret"
    assert config.transport == "stdio"


def test_resolve_config_missing_tenant_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MDE_TENANT_ID", raising=False)
    monkeypatch.delenv("MDE_CLIENT_ID", raising=False)
    monkeypatch.delenv("MDE_CLIENT_SECRET", raising=False)

    with pytest.raises(ValueError, match="MDE_TENANT_ID"):
        resolve_config()


def test_resolve_config_missing_client_id_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MDE_TENANT_ID", "tid")
    monkeypatch.delenv("MDE_CLIENT_ID", raising=False)
    monkeypatch.delenv("MDE_CLIENT_SECRET", raising=False)

    with pytest.raises(ValueError, match="MDE_CLIENT_ID"):
        resolve_config()


def test_resolve_config_missing_secret_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MDE_TENANT_ID", "tid")
    monkeypatch.setenv("MDE_CLIENT_ID", "cid")
    monkeypatch.delenv("MDE_CLIENT_SECRET", raising=False)

    with pytest.raises(ValueError, match="MDE_CLIENT_SECRET"):
        resolve_config()


def test_resolve_config_transport_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MDE_TENANT_ID", "tid")
    monkeypatch.setenv("MDE_CLIENT_ID", "cid")
    monkeypatch.setenv("MDE_CLIENT_SECRET", "secret")
    monkeypatch.setenv("MCP_TRANSPORT", "streamable-http")
    monkeypatch.setenv("MCP_PORT", "8000")

    config = resolve_config()

    assert config.transport == "streamable-http"
    assert config.port == 8000

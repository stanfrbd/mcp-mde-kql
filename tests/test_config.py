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


def test_secret_excluded_from_repr():
    from mcp_mde_kql.utils.config import MdeConfig

    assert "private-password" not in repr(MdeConfig("tid", "cid", "private-password"))


@pytest.mark.parametrize(
    "options",
    [
        {"transport": "invalid"},
        {"port": 0},
        {"max_concurrency": 0},
        {"max_retries": -1},
        {"query_timeout": float("nan")},
        {"sse_path": "relative"},
    ],
)
def test_invalid_config(options):
    from mcp_mde_kql.utils.config import MdeConfig

    with pytest.raises(ValueError):
        MdeConfig("tid", "cid", "secret", **options)


def test_cli_overrides_environment(monkeypatch):
    monkeypatch.setenv("MDE_TENANT_ID", "tid")
    monkeypatch.setenv("MDE_CLIENT_ID", "cid")
    monkeypatch.setenv("MDE_CLIENT_SECRET", "secret")
    monkeypatch.setenv("MCP_PORT", "9000")
    assert resolve_config(port=8000).port == 8000


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.0.2.5", "example.com"])
def test_remote_http_requires_auth(host):
    from mcp_mde_kql.utils.config import MdeConfig

    with pytest.raises(ValueError, match="Non-loopback"):
        MdeConfig("tid", "cid", "secret", transport="streamable-http", host=host)
    config = MdeConfig(
        "tid",
        "cid",
        "secret",
        transport="streamable-http",
        host=host,
        auth_token="a" * 32,
        public_url="https://mcp.example.com/mcp",
    )
    assert "a" * 32 not in repr(config)


@pytest.mark.parametrize("host", [None, "127.0.0.1", "::1", "localhost"])
def test_loopback_http_allows_no_auth(host):
    from mcp_mde_kql.utils.config import MdeConfig

    MdeConfig("tid", "cid", "secret", transport="streamable-http", host=host)


@pytest.mark.parametrize(
    "options",
    [
        {"auth_token": "short"},
        {"public_url": "ftp://example.com"},
        {"public_url": "https://user:password@example.com"},
        {"public_url": "https://example.com:99999"},
        {"public_url": "https://"},
    ],
)
def test_invalid_http_auth_config(options):
    from mcp_mde_kql.utils.config import MdeConfig

    with pytest.raises(ValueError):
        MdeConfig("tid", "cid", "secret", **options)


def test_http_auth_from_environment(monkeypatch):
    monkeypatch.setenv("MDE_TENANT_ID", "tid")
    monkeypatch.setenv("MDE_CLIENT_ID", "cid")
    monkeypatch.setenv("MDE_CLIENT_SECRET", "secret")
    monkeypatch.setenv("MCP_AUTH_TOKEN", "a" * 32)
    monkeypatch.setenv("MCP_PUBLIC_URL", "https://mcp.example.com")
    assert resolve_config().auth_token == "a" * 32
    assert resolve_config().public_url == "https://mcp.example.com"


def test_presence_cache_is_scoped_and_respects_xdg(monkeypatch, tmp_path):
    from mcp_mde_kql.utils.config import MdeConfig

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    config = MdeConfig("tenant-one", "client", "secret")
    other = MdeConfig("tenant-two", "client", "secret")
    assert config.presence_enabled
    assert config.presence_days == 7
    assert config.presence_request_interval == 2.1
    assert config.presence_cache_ttl == 3600
    assert config.presence_cache_path.parent == tmp_path / "mcp-mde-kql"
    assert config.presence_cache_path != other.presence_cache_path
    assert "tenant-one" not in config.presence_cache_path.name
    assert not config.presence_cache_path.exists()  # Resolving config performs no cache writes.


def test_presence_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("MDE_TENANT_ID", "tid")
    monkeypatch.setenv("MDE_CLIENT_ID", "cid")
    monkeypatch.setenv("MDE_CLIENT_SECRET", "secret")
    monkeypatch.setenv("MDE_PRESENCE_ENABLED", "false")
    monkeypatch.setenv("MDE_PRESENCE_CACHE_TTL", "120")
    monkeypatch.setenv("MDE_PRESENCE_REQUEST_INTERVAL", "3")
    monkeypatch.setenv("MDE_PRESENCE_CACHE_PATH", str(tmp_path / "presence.json"))
    config = resolve_config()
    assert not config.presence_enabled
    assert config.presence_cache_ttl == 120
    assert config.presence_request_interval == 3
    assert config.presence_cache_path == tmp_path / "presence.json"
    monkeypatch.setenv("MDE_PRESENCE_ENABLED", "nope")
    with pytest.raises(ValueError, match="MDE_PRESENCE_ENABLED"):
        resolve_config()


@pytest.mark.parametrize(
    "options",
    [
        {"presence_days": 30},
        {"presence_request_interval": 1.0},
        {"presence_request_interval": float("nan")},
        {"presence_cache_ttl": 0},
    ],
)
def test_invalid_presence_config(options):
    from mcp_mde_kql.utils.config import MdeConfig

    with pytest.raises(ValueError):
        MdeConfig("tid", "cid", "secret", **options)

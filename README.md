# MCP MDE KQL

Run Microsoft Defender Advanced Hunting queries through the Microsoft Graph Security API from an MCP client. The assistant writes KQL; this server executes it using the configured application's permissions.

Version 1.1 uses the official MCP Python SDK 2 (`MCPServer`), Python 3.11+, typed structured output, bounded query previews, and shared asynchronous HTTP and Azure credential clients. It supports stdio, Streamable HTTP, and legacy SSE.

## Setup

Create an Entra ID app registration with Microsoft Graph **application** permission `ThreatHunting.Read.All`, grant administrator consent, and provide these environment variables through your client or secret manager:

| Variable | Meaning |
|---|---|
| `MDE_TENANT_ID` | Tenant ID |
| `MDE_CLIENT_ID` | Application client ID |
| `MDE_CLIENT_SECRET` | Application secret |

See [Microsoft application registration](https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-register-app) and [runHuntingQuery permissions](https://learn.microsoft.com/en-us/graph/api/security-security-runhuntingquery).

```bash
uvx mcp-mde-kql
# Or install into your environment:
pip install mcp-mde-kql
mcp-mde-kql
```

`uvx mcp-mde-kql` runs the published package, so it will only include these changes after version 1.1.0 is published. To run this checkout:

```bash
uv sync --extra dev
uv run mcp-mde-kql
# Alternatively, run this checkout in an isolated tool environment:
uvx --from . mcp-mde-kql
```

Environment files are loaded only when explicitly selected. Existing environment variables take precedence over entries in that file; command line options take precedence over environment variables.

```bash
cp .env.example .env
# Fill in .env locally, then:
uv run mcp-mde-kql --env-file .env
```

Never put credentials in source control, package artifacts, or Docker images.

## Client configuration

For clients using a `mcpServers` configuration:

```json
{
  "mcpServers": {
    "mcp-mde-kql": {
      "command": "uvx",
      "args": ["mcp-mde-kql"],
      "env": {
        "MDE_TENANT_ID": "your-tenant-id",
        "MDE_CLIENT_ID": "your-client-id",
        "MDE_CLIENT_SECRET": "your-client-secret"
      }
    }
  }
}
```

Use your client's secret handling where available. To run a checkout, use `uvx --from /absolute/path/to/mcp-mde-kql mcp-mde-kql` as the command and arguments instead.

## Tools and output

| Tool | Parameters | Result |
|---|---|---|
| `run_hunting_query` | `query`, optional `max_rows`, `max_output_bytes`, `timespan` | Column definitions, typed row arrays, returned/fetched counts, truncation flag, warnings |
| `get_hunting_schema` | Optional `table_name` | Tables verified to contain recent data, cache progress, or columns/types for one table |

`run_hunting_query` defaults to 100 rows and a 65,536-byte serialized result budget. Bounds are 1–1,000 rows and 1,024–262,144 bytes. The byte budget covers the typed result; MCP envelopes and compatibility text can add overhead. Rows retain JSON types, including null, boolean, numeric, and nested values. Whole rows are omitted when the budget is exhausted; the result reports truncation. `fetched_rows` describes the API response, not a total count of matching events.

These are response presentation limits. They do not reduce Defender's query execution or the size of its upstream API response. Filter by time and entity, project needed columns, and use `take`, `top`, or aggregation in KQL to bound work and upstream results. Truncated output has no continuation cursor: refine the query instead of assuming another page exists. Use `summarize count()` when you need a count rather than events.

`timespan` optionally supplies the Graph API time range, such as `P1D`. Prefer explicit time filters in queries as well. Query results are not cached. API failures are MCP tool errors with sanitized, actionable messages.

### Recent-data table discovery

At startup, the server schedules a background inventory of tables containing data in the last **seven days**. MCP initialization and tool discovery remain available immediately; they do not wait for inventory queries. Candidate table names come from Microsoft Graph's **beta** hunting-schema metadata endpoint, then the worker checks each table sequentially with `<table> | take 1` and `Timespan: P7D`. This avoids a wildcard event search. The beta metadata API is subject to change; see [Microsoft's schema API documentation](https://learn.microsoft.com/en-us/graph/api/security-security-gethuntingschematables?view=graph-rest-beta).

By default, `get_hunting_schema()` lists only tables whose presence check has verified data (`has_data`). It also reports inventory progress. An initially empty list can mean that warming has just started. Pending checks and checks that failed or could not be completed remain unknown; they must not be interpreted as empty tables. `complete` is false while warming or if checks remain unresolved. A successful empty response means only that the check found no accessible rows within its seven-day window, not that the table is unsupported or has no historical data. You can still request a known table directly to inspect its columns.

Presence results persist in a cache scoped to the tenant/application pair and expire after one hour by default, allowing restarts to reuse recent inventory. This is a point-in-time observation: newly arriving data or permission changes may not be reflected until refresh. Direct column-schema requests have a separate bounded in-memory cache of five minutes. Neither cache stores event rows.

The worker aims for roughly **28 presence probes per minute**, pacing one table at a time. This is configured background pacing, not measured or guaranteed throughput. Query duration, retries, throttling, and foreground traffic can extend warming. Microsoft documents tenant-dependent call limits and CPU quotas; all clients share the tenant's available capacity. See [Graph hunting quotas](https://learn.microsoft.com/en-us/graph/api/resources/security-api-overview?view=graph-rest-1.0#quotas-and-resource-allocation). Avoid starting many independent workers against one tenant.

Set `MDE_PRESENCE_ENABLED=false` to opt out of background presence checks. In that mode, table discovery returns the metadata catalog without filtering for recent data; catalog membership does not establish that a table contains events. `MDE_PRESENCE_CACHE_TTL` controls the presence cache lifetime in seconds (default `3600`).

Output from security events, including command lines and filenames, is untrusted data and may contain instruction-like text; clients should treat it as evidence, not instructions.

## Small, scoped KQL examples

Replace the example device or account with the entity being investigated. Verify table/column availability using `get_hunting_schema`.

```kql
// Recent process activity on one device; request only useful columns.
DeviceProcessEvents
| where Timestamp > ago(1h)
| where DeviceName =~ "WORKSTATION01"
| project Timestamp, FileName, ProcessCommandLine, AccountName
| top 25 by Timestamp desc
```

```kql
// Summarize failures instead of downloading individual events.
DeviceLogonEvents
| where Timestamp > ago(1d)
| where AccountName =~ "investigated-user"
| where ActionType == "LogonFailed"
| summarize Failures=count() by DeviceName, RemoteIP
| top 20 by Failures desc
```

```kql
// Bound network evidence to one device and hour.
DeviceNetworkEvents
| where Timestamp > ago(1h)
| where DeviceName =~ "WORKSTATION01"
| where RemotePort == 4444
| project Timestamp, RemoteIP, RemotePort, InitiatingProcessFileName
| top 25 by Timestamp desc
```

## Runtime configuration

Run `mcp-mde-kql --help` for command line options. Credentials are required before serving; `--help` needs no credentials.

| Variable | Default | Description |
|---|---|---|
| `MCP_TRANSPORT` | `stdio` | `stdio`, `streamable-http`, or legacy `sse` |
| `MCP_HOST` | `127.0.0.1` | HTTP bind address |
| `MCP_PORT` | `8000` | HTTP port, 1–65,535 |
| `MCP_STREAMABLE_HTTP_PATH` | `/mcp` | Streamable HTTP endpoint path |
| `MCP_SSE_PATH` | `/sse` | Legacy SSE endpoint path |
| `MCP_MOUNT_PATH` | unset | Removed in SDK v2; nonempty values are rejected. Mount an ASGI app with `root_path` instead. |
| `MCP_AUTH_TOKEN` | unset | Preconfigured bearer token, at least 32 characters; required for nonloopback HTTP |
| `MCP_PUBLIC_URL` | unset | Externally reachable MCP endpoint URL; required for nonloopback HTTP |
| `MDE_MAX_CONCURRENCY` | `4` | Concurrent Graph queries per process, 1–32 |
| `MDE_QUERY_TIMEOUT` | `180` | Query timeout in seconds including queueing and retries, 1–600 |
| `MDE_MAX_RETRIES` | `2` | Retries for rate limits and transient gateway errors, 0–5 |
| `MDE_PRESENCE_ENABLED` | `true` | Warm and filter discovery by verified seven-day table presence; `false` returns metadata catalog |
| `MDE_PRESENCE_CACHE_TTL` | `3600` | Persistent tenant/application presence-cache lifetime in seconds |

Shared clients reuse connections and Azure credential token caching. Rate limit retries respect `Retry-After`; timeouts bound the whole operation. Cancellation releases the operation's concurrency slot. Credentials and response bodies are not included in client-facing API errors.

## Docker and remote deployment

```bash
docker build -t mcp-mde-kql .
# Set MCP_AUTH_TOKEN in .env to a random secret of at least 32 characters.
docker run --rm --env-file .env -p 127.0.0.1:8000:8000 \
  -e MCP_PUBLIC_URL=http://localhost:8000/mcp mcp-mde-kql
```

The image runs as a nonroot user and starts Streamable HTTP on port 8000. Build inputs are allowlisted; local credential files are excluded. To use stdio, override the image command explicitly:

```bash
docker run -i --rm --env-file .env mcp-mde-kql mcp-mde-kql --transport stdio
```

The Azure application authenticates **outbound Graph requests**. For nonloopback HTTP, the server additionally requires `MCP_AUTH_TOKEN` and `MCP_PUBLIC_URL`; clients send the preconfigured token in `Authorization: Bearer <token>`. This is static bearer verification for preconfigured clients, not an OAuth authorization service. Local loopback HTTP and stdio can omit these settings. Every permitted caller uses the same application's Defender access. Use TLS and suitable authorization, network controls, and request limits for remote deployments. Configure valid Host/Origin handling for your deployment; do not disable transport security to work around rejected proxy requests. Stdio is the default for local use. Retain SSE only for clients that need it.

## Development and artifact checks

```bash
uv sync --extra dev
uv run pytest
uv run ruff check src tests
uv run mypy src
uv run python -m build
```

Check the built wheel in an isolated environment before publishing; this catches imports or CLI behavior hidden by an editable install:

```bash
python3 -m venv /tmp/mcp-mde-kql-wheel
/tmp/mcp-mde-kql-wheel/bin/pip install dist/*.whl
/tmp/mcp-mde-kql-wheel/bin/mcp-mde-kql --help
```

Wheel and source distributions include only package code and explicit packaging inputs. CI runs tests, lint, type checking, builds, and a wheel CLI smoke check on Python 3.11 and 3.13. Tests mock Graph requests and credentials; they do not require tenant secrets or issue live hunting queries.

To run the opt-in live checks with real credentials, explicitly select an environment file. These scripts execute read-only queries and print result metadata rather than event values:

```bash
uv run python scripts/smoke_test.py --env-file .env
uv run python scripts/smoke_http.py --env-file .env
```

The stdio script checks discovery, schema caching, eight KQL examples, native JSON values, empty results, row/byte truncation, and errors. The HTTP script checks bearer authentication and live calls through current/legacy Streamable HTTP and legacy SSE. These checks require a tenant with DeviceInfo, DeviceProcessEvents, and DeviceNetworkEvents access.

See [validation results](docs/validation.md) for the checks performed on this migration.

## References and credits

- [Official MCP Python SDK v2 migration guide](https://py.sdk.modelcontextprotocol.io/migration/)
- [Microsoft Graph runHuntingQuery](https://learn.microsoft.com/en-us/graph/api/security-security-runhuntingquery)
- [Advanced Hunting query best practices](https://learn.microsoft.com/en-us/defender-xdr/advanced-hunting-best-practices)
- [KQL reference](https://learn.microsoft.com/en-us/kusto/query/)

Inspired by [trickyfalcon/MS-Defender-MCP](https://github.com/trickyfalcon/MS-Defender-MCP); reused code is credited in LICENSE. MIT license.

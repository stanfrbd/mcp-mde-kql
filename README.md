# MCP MDE KQL

This project is inspired from this MCP Server (that doesn't use the new Graph API): https://github.com/trickyfalcon/MS-Defender-MCP
Since some parts of his code are used, he is credited in the LICENSE file. Thanks [@trickyfalcon](https://github.com/trickyfalcon)

**_Hunt threats in Microsoft Defender with natural language._**

Model Context Protocol server for Microsoft Defender Advanced Hunting. Enables AI assistants to investigate security events across endpoints, email, identity, and cloud apps by translating natural language into KQL and executing it against the Microsoft Graph Security API.

This project is packaged as a standard Python distribution and can be launched with:

- `uvx mcp-mde-kql`
- `pip install mcp-mde-kql` then `mcp-mde-kql`

## Why this server

- Run KQL queries against Defender Advanced Hunting without leaving your AI assistant.
- Discover available tables and columns dynamically from your own Defender instance.
- Covers all Advanced Hunting workloads: Device, Identity, Email, Cloud App, and AI tables.
- Supports `stdio`, `sse`, and `streamable-http` transports.
- Compatible with Claude Desktop, Claude Code, VS Code, Cursor, and any MCP-capable client.

## Prerequisites

An **Azure AD App Registration** with the following Microsoft Graph application permission:

| Permission | Purpose |
|---|---|
| `ThreatHunting.Read.All` | Run Advanced Hunting queries |

Grant admin consent after adding the permission. Note down the **Tenant ID**, **Client ID**, and **Client Secret** — these become your `MDE_*` environment variables.

> See [Microsoft docs: Register an application](https://learn.microsoft.com/en-us/azure/active-directory/develop/quickstart-register-app)

## Installation

### Use with `uvx` (no install required)

```bash
MDE_TENANT_ID=<id> MDE_CLIENT_ID=<id> MDE_CLIENT_SECRET=<secret> uvx mcp-mde-kql
```

### Use with `pip`

```bash
pip install mcp-mde-kql
MDE_TENANT_ID=<id> MDE_CLIENT_ID=<id> MDE_CLIENT_SECRET=<secret> mcp-mde-kql
```

### Local development

```bash
git clone https://github.com/stanfrbd/mcp-mde-kql
cd mcp-mde-kql

# Install with uv (recommended)
uv sync --extra dev

# Or with pip
pip install -e ".[dev]"
```

## Docker

The default container command starts in `streamable-http` mode on port `8000`.

```bash
docker build -t mcp-mde-kql .

docker run --rm -p 8000:8000 \
  -e MDE_TENANT_ID=your-tenant-id \
  -e MDE_CLIENT_ID=your-client-id \
  -e MDE_CLIENT_SECRET=your-client-secret \
  mcp-mde-kql
```

To use `stdio` transport:

```bash
docker run -i --rm \
  -e MDE_TENANT_ID=your-tenant-id \
  -e MDE_CLIENT_ID=your-client-id \
  -e MDE_CLIENT_SECRET=your-client-secret \
  -e MCP_TRANSPORT=stdio \
  mcp-mde-kql
```

## Configuration

Copy `.env.example` to `.env` and fill in your credentials:

```bash
cp .env.example .env
```

### Environment variables

All configuration is provided via environment variables.

| Variable | Required | Default | Description |
|---|---|---|---|
| `MDE_TENANT_ID` | Yes | — | Azure AD tenant ID |
| `MDE_CLIENT_ID` | Yes | — | Azure AD application client ID |
| `MDE_CLIENT_SECRET` | Yes | — | Azure AD application client secret |
| `MCP_TRANSPORT` | No | `stdio` | Transport: `stdio`, `sse`, or `streamable-http` |
| `MCP_HOST` | No | SDK default | Host for HTTP transports |
| `MCP_PORT` | No | SDK default | Port for HTTP transports |
| `MCP_MOUNT_PATH` | No | SDK default | Mount path for SSE transport |
| `MCP_SSE_PATH` | No | SDK default | SSE endpoint path |
| `MCP_STREAMABLE_HTTP_PATH` | No | SDK default | Endpoint path for streamable-http |

## MCP Client Integration

### Claude configuration

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

With Docker + stdio:

```json
{
  "mcpServers": {
    "mcp-mde-kql": {
      "command": "docker",
      "args": [
        "run", "-i", "--rm",
        "-e", "MDE_TENANT_ID",
        "-e", "MDE_CLIENT_ID",
        "-e", "MDE_CLIENT_SECRET",
        "-e", "MCP_TRANSPORT=stdio",
        "mcp-mde-kql"
      ],
      "env": {
        "MDE_TENANT_ID": "your-tenant-id",
        "MDE_CLIENT_ID": "your-client-id",
        "MDE_CLIENT_SECRET": "your-client-secret"
      }
    }
  }
}
```

### Claude Code

```bash
claude mcp add mcp-mde-kql uvx mcp-mde-kql \
  -e MDE_TENANT_ID=your-tenant-id \
  -e MDE_CLIENT_ID=your-client-id \
  -e MDE_CLIENT_SECRET=your-client-secret
```

### VS Code

Create `.vscode/mcp.json` in your workspace:

```json
{
  "servers": {
    "mcp-mde-kql": {
      "type": "stdio",
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

## Available Tools

| Tool | Description |
|---|---|
| `run_hunting_query` | Execute a KQL query against Defender Advanced Hunting and return formatted results |
| `get_hunting_schema` | List all available tables, or get columns and types for a specific table |

### `run_hunting_query`

```
query (required): KQL query string to execute
```

Returns results as a pipe-separated table with row count and execution time stats.

### `get_hunting_schema`

```
table_name (optional): table to inspect — omit to list all available tables
```

Returns available tables or the column name + type for a specific table. Always call this before writing queries.

## Example Prompts

### Threat Hunting

- Show me all logon failures for `user@domain.com` in the last 7 days
- List processes launched by `cmd.exe` on device `WORKSTATION01` in the past 24 hours
- Find all outbound connections to port 4444 across my endpoints this week
- Which devices ran `powershell.exe` with encoded commands yesterday?
- Show me all email attachments with `.exe` or `.js` extensions received this month

### Incident Investigation

- I have an alert for device `DESKTOP-ABC123` — what happened on it in the last hour?
- Find all lateral movement from `10.0.0.5` — look at logons, process execution, and network events
- Which users logged into `SERVER01` after 11 PM last night?
- Show me all files dropped by `suspicious.exe` and any child processes it spawned
- Was there any DNS traffic to `evil-domain.com` in the past 30 days?

### Schema Discovery

- What tables are available in Advanced Hunting?
- Show me the columns in `DeviceNetworkEvents`
- What fields does `EmailEvents` have?

### Raw KQL Examples

```kql
// Logon failures in the last day
DeviceLogonEvents
| where Timestamp > ago(1d)
| where ActionType == "LogonFailed"
| summarize count() by AccountName, DeviceName, RemoteIP
| sort by count_ desc

// Suspicious PowerShell with encoded commands
DeviceProcessEvents
| where Timestamp > ago(7d)
| where FileName =~ "powershell.exe"
| where ProcessCommandLine has_any ("-enc", "-EncodedCommand", "FromBase64String")
| project Timestamp, DeviceName, AccountName, ProcessCommandLine

// Outbound connections to uncommon ports
DeviceNetworkEvents
| where Timestamp > ago(1d)
| where ActionType == "ConnectionSuccess"
| where not(RemotePort in (80, 443, 53, 22, 25))
| summarize count() by RemoteIP, RemotePort, DeviceName
| sort by count_ desc
```

## Development

```bash
# Run tests
uv run pytest

# Lint
uv run ruff check src tests

# Type check
uv run mypy src

# Security scan
uv run bandit -r src
```

## Project Structure

```
src/mcp_mde_kql/
├── __main__.py     # entry point
├── server.py       # FastMCP server setup
├── tools/
│   └── hunting.py  # run_hunting_query, get_hunting_schema
└── utils/
    ├── auth.py     # Azure AD token acquisition
    ├── config.py   # MdeConfig dataclass + resolve_config
    └── http.py     # Defender API HTTP client
```

## API Reference

- [Microsoft Graph: runHuntingQuery](https://learn.microsoft.com/en-us/graph/api/security-security-runhuntingquery)
- [Advanced Hunting overview](https://learn.microsoft.com/en-us/microsoft-365/security/defender/advanced-hunting-overview)
- [KQL quick reference](https://learn.microsoft.com/en-us/azure/data-explorer/kql-quick-reference)

## Acknowledgements

- [Model Context Protocol](https://modelcontextprotocol.io)
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [Microsoft Graph Security API](https://learn.microsoft.com/en-us/graph/api/resources/security-api-overview)

## License

MIT

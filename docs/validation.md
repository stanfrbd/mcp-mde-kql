# Migration validation — 2026-10-28

Version: 1.1.0. Official MCP Python SDK: 2.3.0.

## Automated checks

All 145 tests pass on each of Python 3.11, 3.12, and 3.13. Tests use fake credentials and mocked Graph responses, including real SDK client/server negotiation and installed subprocess startup.

Coverage includes input/output schemas; native JSON values; byte and row budgets; empty/malformed responses; schema cache expiry, bounds and concurrent reuse; error signaling; cancellation cleanup; connection failures; retry limits and deadlines; CLI environment precedence and explicit dotenv loading; lifespan cleanup; and HTTP bearer authentication.

Ruff lint/format checks, strict mypy, Bandit, and distribution builds pass. Both wheel and source archives were inspected for their allowlisted contents. A fresh `uvx --from ./dist/mcp_mde_kql-1.1.0-py3-none-any.whl mcp-mde-kql` environment successfully starts the package. Frozen-lock environments were verified on Python 3.11 and 3.13; Python 3.12 additionally tested fresh dependency resolution.

## Live read-only checks

The local `.env` was explicitly selected. No credentials or returned event values are stored in this report. The built wheel was exercised through uvx over stdio:

- Tool discovery and direct DeviceInfo column schema.
- Metadata table discovery without event scanning (368 names returned in 0.88 seconds).
- Seven-day presence checks on DeviceInfo, DeviceProcessEvents, and DeviceNetworkEvents; all three returned data.
- Nonblocking cache start (0.1 ms in the sample), persistent restart reuse without repeat probes, and filtered list output.
- Graph SByte boolean serialization regression (projected true is returned as integer 1).
- Eight KQL examples: constants, nested JSON/Unicode, an oversized scalar, zero rows, device count, process aggregation, network aggregation, and a deterministic three-row preview.
- Row and byte truncation metadata, preserving native JSON types.
- Invalid KQL returns an MCP error; invalid schema identifiers are rejected.

Authenticated local transport checks also executed a small device aggregate over current and legacy Streamable HTTP and legacy SSE. Unauthenticated HTTP requests were rejected. Automated tests separately verify invalid-token rejection.

## Boundaries

Docker is not installed in the validation environment, so image build/run was not exercised. The Dockerfile and context allowlist were reviewed; CLI transport arguments and remote-binding requirements are covered by tests. No package was published, and public `uvx mcp-mde-kql` will obtain the published release until a new release is uploaded.

Response budgets apply to canonical result JSON. The compatibility text and structured content both appear on the wire; protocol framing adds further overhead. Presentation limits do not reduce upstream query computation or response size. The first complete seven-day presence warmup was not run across all 368 tables. Probes are paced in the background, save partial progress, and return only confirmed nonempty tables; pending and failed checks are reported separately. Metadata uses the Graph beta endpoint. No throughput benchmark or production load test was performed.

## Code organization

- `__main__.py`: explicit CLI and dotenv loading.
- `server.py`: SDK construction, transport settings, inbound bearer verification, and resource lifespan.
- `tools/hunting.py`: MCP input schemas, annotations, context injection, compact result serialization.
- `service.py`: query result budgets, filtered presence snapshots, and bounded schema cache.
- `presence.py`: paced seven-day existence checks, resumable private disk cache, TTL refresh, and cancellation.
- `models.py`: public typed result contracts.
- `utils/http.py`, `utils/auth.py`: pooled Graph requests, async Azure credentials, concurrency/retry/deadline handling.
- `utils/config.py`: validated environment configuration.

Run live checks deliberately using the scripts documented in README; they are excluded from automated CI to avoid requiring tenant credentials.

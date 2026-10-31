"""Exercise the packaged entry point in a subprocess without tenant credentials."""

import os
from importlib.metadata import version
from pathlib import Path
import subprocess
import sys

import pytest


def cli_environment():
    return {key: value for key, value in os.environ.items() if not key.startswith(("MDE_", "MCP_"))}


def run_cli(*args, cwd=None):
    return subprocess.run(
        [sys.executable, "-m", "mcp_mde_kql", *args],
        env=cli_environment(),
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=15,
    )


def test_help_needs_no_credentials():
    result = run_cli("--help")
    assert result.returncode == 0
    assert "--env-file" in result.stdout
    assert "--transport" in result.stdout
    assert result.stderr == ""


def test_version_matches_installed_distribution():
    result = run_cli("--version")
    assert result.returncode == 0
    assert result.stdout.strip() == version("mcp-mde-kql")
    assert result.stderr == ""


def test_installed_console_script():
    executable = Path(sys.executable).parent / (
        "mcp-mde-kql.exe" if os.name == "nt" else "mcp-mde-kql"
    )
    result = subprocess.run(
        [str(executable), "--help"],
        env=cli_environment(),
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0
    assert "--transport" in result.stdout


def test_missing_configuration_fails_cleanly(tmp_path):
    # A cwd .env must never be consumed implicitly.
    (tmp_path / ".env").write_text("MDE_TENANT_ID=unrequested-file\n")
    result = run_cli(cwd=tmp_path)
    assert result.returncode == 2
    assert "MDE_TENANT_ID" in result.stderr
    assert "Traceback" not in result.stderr
    assert result.stdout == ""


def test_missing_explicit_env_file_fails_cleanly(tmp_path):
    result = run_cli("--env-file", str(tmp_path / "missing.env"))
    assert result.returncode == 2
    assert "--env-file must name an existing file" in result.stderr
    assert "Traceback" not in result.stderr
    assert result.stdout == ""


def test_module_imports_have_no_network_or_credential_side_effects():
    script = """
from unittest.mock import patch
with patch("socket.socket", side_effect=AssertionError("network during import")), \\
     patch("azure.identity.aio.ClientSecretCredential", side_effect=AssertionError("credential during import")):
    import mcp_mde_kql.__main__
    import mcp_mde_kql.server
    import mcp_mde_kql.utils.http
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        env=cli_environment(),
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


@pytest.mark.asyncio
async def test_stdio_subprocess_lists_tools_without_graph_calls():
    import asyncio

    from mcp import Client, StdioServerParameters

    env = cli_environment()
    env.update(
        MDE_TENANT_ID="fake-tenant",
        MDE_CLIENT_ID="fake-client",
        MDE_CLIENT_SECRET="fake-secret",
        MDE_PRESENCE_ENABLED="false",
    )
    params = StdioServerParameters(command=sys.executable, args=["-m", "mcp_mde_kql"], env=env)
    async with asyncio.timeout(15), Client(params, read_timeout_seconds=5) as client:
        tools = await client.list_tools()
    assert {tool.name for tool in tools.tools} == {"run_hunting_query", "get_hunting_schema"}

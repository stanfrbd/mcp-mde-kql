from __future__ import annotations

import argparse
from importlib.metadata import version
from pathlib import Path

from mcp_mde_kql.utils.config import resolve_config


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Microsoft Defender hunting MCP server")
    parser.add_argument("--version", action="version", version=version("mcp-mde-kql"))
    parser.add_argument(
        "--env-file", type=Path, help="Explicit dotenv file; existing environment wins"
    )
    parser.add_argument("--transport", choices=("stdio", "sse", "streamable-http"))
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--mount-path")
    parser.add_argument("--sse-path")
    parser.add_argument("--streamable-http-path")
    parser.add_argument("--max-concurrency", type=int)
    parser.add_argument("--query-timeout", type=float)
    parser.add_argument("--max-retries", type=int)
    args = vars(parser.parse_args(argv))
    env_file = args.pop("env_file")
    if env_file is not None:
        from dotenv import load_dotenv

        if not env_file.is_file():
            parser.error("--env-file must name an existing file")
        load_dotenv(env_file, override=False)
    try:
        config = resolve_config(**args)
    except ValueError as exc:
        parser.error(str(exc))
    from mcp_mde_kql.server import run_server

    run_server(config)


if __name__ == "__main__":
    main()

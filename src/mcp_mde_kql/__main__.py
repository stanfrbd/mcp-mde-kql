from mcp_mde_kql.server import run_server
from mcp_mde_kql.utils.config import resolve_config


def main() -> None:
    run_server(resolve_config())


main()

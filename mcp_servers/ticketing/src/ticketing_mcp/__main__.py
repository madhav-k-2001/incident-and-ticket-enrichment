"""Entry point: `python -m ticketing_mcp` or the `ticketing-mcp` script."""

from ticketing_mcp.config import get_settings
from ticketing_mcp.observability import configure_logging
from ticketing_mcp.server import create_server


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    server = create_server(settings)

    if settings.mcp_transport == "streamable-http":
        server.run("streamable-http", host=settings.mcp_host, port=settings.mcp_port)
    else:
        server.run("stdio")


if __name__ == "__main__":
    main()

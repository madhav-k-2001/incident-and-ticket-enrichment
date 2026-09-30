"""Entry point: `python -m alarm_mcp` or the `alarm-mcp` script."""

from alarm_mcp.config import get_settings
from alarm_mcp.observability import configure_logging
from alarm_mcp.server import create_server


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

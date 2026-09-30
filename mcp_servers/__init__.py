"""MCP Servers root package exposing BaseSimulatorClient, SimulatorConfig, exceptions, and the 4 services."""

from mcp_servers.client import BaseSimulatorClient
from mcp_servers.config import SimulatorConfig
from mcp_servers.exceptions import (
    AuthenticationError,
    ConflictError,
    NotFoundError,
    SimulatorAPIError,
    SimulatorClientError,
    SimulatorConnectionError,
    SimulatorServerError,
    ValidationError,
)
from mcp_servers.services.alarm_analytics_service import AlarmAnalyticsService
from mcp_servers.services.alarm_service import AlarmService
from mcp_servers.services.asset_service import AssetService
from mcp_servers.services.ticket_service import TicketService

__all__ = [
    "BaseSimulatorClient",
    "SimulatorConfig",
    "SimulatorClientError",
    "SimulatorConnectionError",
    "SimulatorAPIError",
    "NotFoundError",
    "ValidationError",
    "AuthenticationError",
    "ConflictError",
    "SimulatorServerError",
    "AssetService",
    "AlarmService",
    "AlarmAnalyticsService",
    "TicketService",
]

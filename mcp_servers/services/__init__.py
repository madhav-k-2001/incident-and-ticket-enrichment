"""Service package for Simulator API integrations."""

from mcp_servers.services.asset_service import AssetService
from mcp_servers.services.alarm_service import AlarmService
from mcp_servers.services.alarm_analytics_service import AlarmAnalyticsService
from mcp_servers.services.ticket_service import TicketService
from mcp_servers.services.document_retrieval_service import DocumentRetrievalService

RetrievalService = DocumentRetrievalService

__all__ = [
    "AssetService",
    "AlarmService",
    "AlarmAnalyticsService",
    "TicketService",
    "DocumentRetrievalService",
    "RetrievalService",
]


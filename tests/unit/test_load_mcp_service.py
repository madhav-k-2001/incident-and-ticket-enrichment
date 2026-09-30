import json
import pytest

from agents.mcp import (
    MCPServerSse,
    MCPServerStdio,
    MCPServerStreamableHttp,
)
from agents.tool import HostedMCPTool
from common.load_mcp_service import MCPServerConfigService


@pytest.fixture
def service():
    return MCPServerConfigService()


def test_load_streamable_http(service):
    payload = {
        "servers": [
            {
                "name": "calendar",
                "type": "streamable_http",
                "params": {"url": "http://localhost:8000/mcp"},
                "options": {"cache_tools_list": True},
            }
        ]
    }
    servers = service.load(payload)
    assert len(servers) == 1
    server = servers[0]
    assert isinstance(server, MCPServerStreamableHttp)
    assert server.name == "calendar"
    assert server.params["url"] == "http://localhost:8000/mcp"
    assert server.cache_tools_list is True


def test_load_sse(service):
    payload = {
        "servers": [
            {
                "name": "docs",
                "type": "sse",
                "params": {
                    "url": "http://localhost:8001/sse",
                    "headers": {"Authorization": "Bearer token"},
                },
            }
        ]
    }
    servers = service.load(payload)
    assert len(servers) == 1
    server = servers[0]
    assert isinstance(server, MCPServerSse)
    assert server.name == "docs"
    assert server.params["url"] == "http://localhost:8001/sse"
    assert server.params["headers"] == {"Authorization": "Bearer token"}


def test_load_stdio(service):
    payload = {
        "servers": [
            {
                "name": "filesystem",
                "type": "stdio",
                "params": {
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-filesystem", "./data"],
                },
            }
        ]
    }
    servers = service.load(payload)
    assert len(servers) == 1
    server = servers[0]
    assert isinstance(server, MCPServerStdio)
    assert server.name == "filesystem"
    assert server.params.command == "npx"
    assert server.params.args == ["-y", "@modelcontextprotocol/server-filesystem", "./data"]


def test_load_hosted(service):
    payload = {
        "servers": [
            {
                "name": "weather",
                "type": "hosted",
                "params": {
                    "server_url": "https://api.weather.com/mcp",
                    "require_approval": "always",
                },
            }
        ]
    }
    servers = service.load(payload)
    assert len(servers) == 1
    tool = servers[0]
    assert isinstance(tool, HostedMCPTool)
    assert tool.tool_config["server_label"] == "weather"
    assert tool.tool_config["server_url"] == "https://api.weather.com/mcp"
    assert tool.tool_config["type"] == "mcp"
    assert tool.tool_config["require_approval"] == "always"


def test_load_from_json_string(service):
    json_str = json.dumps({
        "servers": [
            {"name": "calendar", "type": "streamable_http", "params": {"url": "http://localhost:8000/mcp"}},
            {"name": "docs", "type": "streamable_http", "params": {"url": "http://localhost:8001/mcp"}},
        ]
    })
    servers = service.load(json_str)
    assert len(servers) == 2
    assert all(isinstance(s, MCPServerStreamableHttp) for s in servers)
    assert [s.name for s in servers] == ["calendar", "docs"]


def test_invalid_type(service):
    payload = {
        "servers": [
            {"name": "bad", "type": "unknown_type", "params": {}}
        ]
    }
    with pytest.raises(ValueError, match="Unknown server type"):
        service.load(payload)

import json
import pytest

from agents.mcp import (
    MCPServerSse,
    MCPServerStdio,
    MCPServerStreamableHttp,
)
from agents.tool import HostedMCPTool
from apps.backend.services.load_mcp_service import MCPServerConfigService


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


def test_require_approval_list_becomes_always_policy(service):
    payload = {
        "servers": [
            {
                "name": "tickets",
                "type": "streamable_http",
                "params": {"url": "http://localhost:8002/mcp"},
                "require_approval": ["create_ticket", "close_ticket", "create_ticket"],
                "options": {"cache_tools_list": True},
            }
        ]
    }
    (server,) = service.load(payload)
    assert server.cache_tools_list is True
    assert server._needs_approval_policy == {"create_ticket": True, "close_ticket": True}


def test_require_approval_defaults_to_none(service):
    (server,) = service.load(
        [{"name": "docs", "type": "sse", "params": {"url": "http://localhost:8001/sse"}}]
    )
    assert server._needs_approval_policy is False


def test_require_approval_always_string_passes_through(service):
    (server,) = service.load(
        [
            {
                "name": "docs",
                "type": "sse",
                "params": {"url": "http://localhost:8001/sse"},
                "require_approval": "always",
            }
        ]
    )
    assert server._needs_approval_policy is True


def test_require_approval_hosted_goes_into_tool_config(service):
    (tool,) = service.load(
        [
            {
                "name": "weather",
                "type": "hosted",
                "params": {"server_url": "https://api.weather.com/mcp"},
                "require_approval": ["delete_forecast"],
            }
        ]
    )
    assert tool.tool_config["require_approval"] == {
        "always": {"tool_names": ["delete_forecast"]}
    }


def test_require_approval_does_not_mutate_input_spec(service):
    spec = {
        "name": "weather",
        "type": "hosted",
        "params": {"server_url": "https://api.weather.com/mcp"},
        "require_approval": ["delete_forecast"],
    }
    service.load([spec])
    assert "require_approval" not in spec["params"]


@pytest.mark.parametrize("bad", [42, ["ok", 3], [""]])
def test_require_approval_invalid_value(service, bad):
    spec = {"name": "x", "type": "sse", "params": {"url": "http://x"}, "require_approval": bad}
    with pytest.raises(ValueError, match="require_approval"):
        service.load([spec])


def test_require_approval_specified_twice_is_an_error(service):
    spec = {
        "name": "x",
        "type": "sse",
        "params": {"url": "http://x"},
        "options": {"require_approval": "always"},
        "require_approval": ["a"],
    }
    with pytest.raises(ValueError, match="once"):
        service.load([spec])

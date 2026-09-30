"""
MCP Server Configuration Service.
Converts a fixed JSON schema containing MCP server specifications into
OpenAI Agents SDK MCP server objects.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Union

from agents.mcp import (
    MCPServer,
    MCPServerSse,
    MCPServerStdio,
    MCPServerStreamableHttp,
)
from agents.tool import HostedMCPTool

MCPAnyServer = Union[MCPServer, HostedMCPTool]


class MCPServerConfigService:
    """
    Service for converting fixed-schema MCP server JSON specifications into OpenAI Agents SDK objects.

    Expected JSON Schema:
    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "title": "MCPServerSpecs",
      "type": "object",
      "required": ["servers"],
      "properties": {
        "servers": {
          "type": "array",
          "items": {
            "type": "object",
            "required": ["name", "type", "params"],
            "properties": {
              "name": {
                "type": "string",
                "description": "Unique identifier for the MCP server"
              },
              "type": {
                "type": "string",
                "enum": ["streamable_http", "sse", "stdio", "hosted"],
                "description": "Type of MCP transport or tool"
              },
              "params": {
                "type": "object",
                "description": "Connection parameters specific to the server type"
              },
              "options": {
                "type": "object",
                "description": "Optional kwargs passed to the server constructor (e.g. cache_tools_list, require_approval)"
              }
            }
          }
        }
      }
    }
    ```

    Example JSON Payload:
    ```json
    {
      "servers": [
        {
          "name": "calendar",
          "type": "streamable_http",
          "params": {
            "url": "http://localhost:8000/mcp"
          }
        },
        {
          "name": "docs",
          "type": "sse",
          "params": {
            "url": "http://localhost:8001/sse"
          }
        },
        {
          "name": "filesystem",
          "type": "stdio",
          "params": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "./data"]
          }
        },
        {
          "name": "weather",
          "type": "hosted",
          "params": {
            "server_url": "https://api.weather.com/mcp"
          }
        }
      ]
    }
    ```
    """

    def load(self, config: Union[str, Dict[str, Any], List[Dict[str, Any]]]) -> List[MCPAnyServer]:
        """
        Convert the fixed-schema JSON string or dictionary into a list of OpenAI MCP server objects.

        Args:
            config: JSON string, parsed dictionary containing `{"servers": [...]}`,
                    or a direct list of server specifications.

        Returns:
            List of MCPServerStreamableHttp, MCPServerSse, MCPServerStdio, and/or HostedMCPTool instances.
        """
        if isinstance(config, str):
            config = json.loads(config)

        if isinstance(config, dict):
            specs = config.get("servers", [])
        elif isinstance(config, list):
            specs = config
        else:
            raise ValueError(f"Expected dict or list, got {type(config).__name__}")

        return [self.load_server(spec) for spec in specs]

    def load_server(self, spec: Dict[str, Any]) -> MCPAnyServer:
        """
        Convert a single server specification into its corresponding OpenAI MCP object.

        Args:
            spec: Dictionary containing 'name', 'type', 'params', and optional 'options'.

        Returns:
            MCPAnyServer: An initialized MCP server or HostedMCPTool object.
        """
        name: str = spec["name"]
        server_type: str = spec["type"].lower()
        params: Dict[str, Any] = spec.get("params", {})
        options: Dict[str, Any] = spec.get("options", {})

        if server_type == "streamable_http":
            return MCPServerStreamableHttp(name=name, params=params, **options)

        elif server_type == "sse":
            return MCPServerSse(name=name, params=params, **options)

        elif server_type == "stdio":
            return MCPServerStdio(name=name, params=params, **options)

        elif server_type == "hosted":
            tool_config = {
                "type": "mcp",
                "server_label": name,
                **params,
            }
            # Allow "url" as an alias for "server_url"
            if "url" in tool_config and "server_url" not in tool_config:
                tool_config["server_url"] = tool_config.pop("url")

            on_approval_request = options.get("on_approval_request")
            return HostedMCPTool(tool_config=tool_config, on_approval_request=on_approval_request)

        else:
            raise ValueError(
                f"Unknown server type '{server_type}'. "
                f"Supported types are: 'streamable_http', 'sse', 'stdio', 'hosted'."
            )


# Alias for backward compatibility or concise usage
MCPConfigService = MCPServerConfigService

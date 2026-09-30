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
              "require_approval": {
                "description": "Tools that need explicit user approval before they run. A list of tool names (recommended), or 'always' / 'never' for every tool on the server, or the SDK's {'always': {'tool_names': [...]}, 'never': {'tool_names': [...]}} form.",
                "oneOf": [
                  {"type": "array", "items": {"type": "string"}},
                  {"type": "string", "enum": ["always", "never"]},
                  {"type": "object"}
                ]
              },
              "options": {
                "type": "object",
                "description": "Optional kwargs passed to the server constructor (e.g. cache_tools_list)"
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
          },
          "require_approval": ["write_file", "move_file"]
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

    Tool approval: list the tools that must always be confirmed by the user in
    ``require_approval``. The agent pauses before running one of them and the
    call only proceeds once the user approves it (see ``AgentService.resume``).
    Tools that are not listed run without asking. This works for every server
    type, including ``hosted`` ones.
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
        params: Dict[str, Any] = dict(spec.get("params", {}))
        options: Dict[str, Any] = dict(spec.get("options", {}))

        if "require_approval" in spec:
            if "require_approval" in options or "require_approval" in params:
                raise ValueError(
                    f"Server '{name}': set 'require_approval' once, at the top level of the "
                    f"server spec, not also in 'options' or 'params'."
                )
            approval = _normalize_require_approval(spec["require_approval"], name)
            # Local servers take it as a constructor kwarg; hosted servers send it to the
            # provider inside the tool config.
            (params if server_type == "hosted" else options)["require_approval"] = approval

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


def _normalize_require_approval(value: Any, server_name: str) -> Any:
    """Turn the config's ``require_approval`` into the form the SDK understands.

    A list of tool names becomes ``{"always": {"tool_names": [...]}}``; every
    other tool on the server keeps running without approval. ``"always"`` /
    ``"never"`` and the SDK's dict form pass through (the SDK validates them).
    """
    if isinstance(value, list):
        if not all(isinstance(t, str) and t for t in value):
            raise ValueError(
                f"Server '{server_name}': 'require_approval' must be a list of tool name strings."
            )
        return {"always": {"tool_names": list(dict.fromkeys(value))}}
    if isinstance(value, (str, dict)):
        return value
    raise ValueError(
        f"Server '{server_name}': 'require_approval' must be a list of tool names, "
        f"'always', 'never' or an object, got {type(value).__name__}."
    )


# Alias for backward compatibility or concise usage
MCPConfigService = MCPServerConfigService

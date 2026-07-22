"""Safe access to the platform's explicitly registered MCP tools."""

from oncall.mcp_gateway.client import McpGateway, McpGatewayError
from oncall.mcp_gateway.schemas import ToolResult

__all__ = ["McpGateway", "McpGatewayError", "ToolResult"]

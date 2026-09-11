"""Transport-neutral MCP adapter; only three read-only tools are exposed."""
from __future__ import annotations
from .mcp_contract import TOOLS, TOOL_SCHEMAS

class McpServer:
    def __init__(self, service, *, transport="stdio"): self.service, self.transport = service, transport
    def list_tools(self):
        return [{"name":name, "inputSchema":TOOL_SCHEMAS[name]} for name in TOOLS]
    def call_tool(self, name, arguments, authorization, request_id=None):
        return self.service.call(name, arguments, authorization, transport=self.transport, request_id=request_id)

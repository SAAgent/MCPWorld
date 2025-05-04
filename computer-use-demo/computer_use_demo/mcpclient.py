from typing import Any
from contextlib import AsyncExitStack

from .tools.base import ToolResult
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from anthropic.types.beta import BetaToolParam

class MCPClient:
    def __init__(self):
        self.sessions: list[ClientSession] = []
        self.exit_stack = AsyncExitStack()

    async def connect_to_server(self, server_script_path: str) -> None:
        """Connect to an MCP server

        Args:
            server_script_path: Path to the server script (.py or .js)
        """
        is_python = server_script_path.endswith('.py')
        is_js = server_script_path.endswith('.js')
        if not (is_python or is_js):
            raise ValueError("Server script must be a .py or .js file")

        command = "python" if is_python else "node"
        server_params = StdioServerParameters(
            command=command,
            args=[server_script_path],
            env=None
        )

        stdio_transport = await self.exit_stack.enter_async_context(stdio_client(server_params))
        self.stdio, self.write = stdio_transport
        session = await self.exit_stack.enter_async_context(ClientSession(self.stdio, self.write))

        await session.initialize()
        self.sessions.append(session)

    async def list_tools(self) -> list[BetaToolParam]:
        if not self.sessions:
            return []
        tools = []
        for session in self.sessions:
            response = await session.list_tools()
            tools.extend(response.tools)
        print("\nConnected to server with tools:", [tool.name for tool in tools])
        tool_params = [
            BetaToolParam(
                name=tool.name,
                description=tool.description,
                input_schema=tool.inputSchema
            )
            for tool in tools
        ]
        return tool_params

    async def call_tool(self, name: str, tool_input: dict[str, Any]) -> ToolResult:
        if not self.sessions:
            raise RuntimeError("No active sessions. Please connect to a server first.")
        for session in self.sessions:
            response = await session.list_tools()
            session_tools = [tool.name for tool in response.tools]
            if name not in session_tools:
                continue
            temp_result = await session.call_tool(name, tool_input)
            item = temp_result.content[0]
            if hasattr(item, "type") and item.type == "text":
                result = ToolResult(output=item.text)
            elif hasattr(item, "type") and item.type == "image":
                result = ToolResult(base64_image=item.data)
            else:
                raise ValueError(f"Unsupported content type: {item.type}")
            return result
        raise ValueError(f"Tool {name} not found in any active session.")

    async def cleanup(self):
        await self.exit_stack.aclose()

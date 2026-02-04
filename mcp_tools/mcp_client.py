import json
import sys
import os
from contextlib import AsyncExitStack
from typing import Optional, Dict, Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class MCPClient:
    def __init__(self, server_script_path: str):
        python_executable = sys.executable
        project_root = os.path.abspath(os.getcwd())
        env = os.environ.copy()

        current_pythonpath = env.get("PYTHONPATH", "")
        if current_pythonpath:
            env["PYTHONPATH"] = f"{project_root}{os.pathsep}{current_pythonpath}"
        else:
            env["PYTHONPATH"] = project_root

        self.server_params = StdioServerParameters(
            command=python_executable,
            args=[server_script_path],
            env=env
        )

        self.session: Optional[ClientSession] = None
        self.exit_stack = AsyncExitStack()
        self._read = None
        self._write = None

    async def connect(self):
        try:
            stdio_transport = await self.exit_stack.enter_async_context(stdio_client(self.server_params))
            self._read, self._write = stdio_transport
            self.session = await self.exit_stack.enter_async_context(ClientSession(self._read, self._write))
            await self.session.initialize()
            print("✅ MCP Client Connected.")
        except Exception as e:
            print(f"❌ MCP Connection Failed: {e}")
            print(f"   Command: {self.server_params.command}")
            print(f"   Args: {self.server_params.args}")
            raise e

    async def list_tools(self):
        if not self.session:
            raise RuntimeError("Client not connected")
        return await self.session.list_tools()

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> str | Dict:
        if not self.session:
            raise RuntimeError("Client not connected")

        try:
            result = await self.session.call_tool(tool_name, arguments)
            if result.content and hasattr(result.content[0], 'text'):
                text_response = result.content[0].text
                try:
                    return json.loads(text_response)
                except json.JSONDecodeError:
                    return text_response
            return str(result)
        except Exception as e:
            return f"Tool execution failed: {str(e)}"

    async def close(self):
        await self.exit_stack.aclose()
        print("🔌 MCP Client Closed.")

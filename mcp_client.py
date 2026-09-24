"""A minimal MCP client: starts the server and speaks JSON-RPC over its stdio.

`chat.py --mcp` uses this to discover the server's tools and route the model's
tool calls to it. It also runs standalone for a quick check:

    python mcp_client.py
"""

import json
import subprocess
import sys
from pathlib import Path

SERVER = Path(__file__).parent / "mcp_server.py"


class MCPClient:
    def __init__(self, command: list[str] | None = None) -> None:
        command = command or [sys.executable, str(SERVER)]
        self._next_id = 0
        self.proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        self._request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "slm-chat-demo", "version": "1.0.0"},
        })
        self._notify("notifications/initialized")

    def _send(self, message: dict) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()

    def _notify(self, method: str, params: dict | None = None) -> None:
        message = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        self._send(message)

    def _request(self, method: str, params: dict | None = None) -> dict:
        self._next_id += 1
        request_id = self._next_id
        message = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        self._send(message)

        assert self.proc.stdout is not None
        while True:
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError("MCP server closed the connection")
            data = json.loads(line)
            if data.get("id") != request_id:
                continue  # ignore notifications / unrelated messages
            if "error" in data:
                raise RuntimeError(data["error"].get("message", "MCP error"))
            return data.get("result", {})

    def openai_tools(self) -> list[dict]:
        """The server's tools, converted to the OpenAI `tools` schema."""
        tools = self._request("tools/list").get("tools", [])
        return [
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get("inputSchema") or {"type": "object", "properties": {}},
                },
            }
            for tool in tools
        ]

    def call_tool(self, name: str, arguments: dict) -> str:
        result = self._request("tools/call", {"name": name, "arguments": arguments})
        texts = [part.get("text", "") for part in result.get("content", []) if part.get("type") == "text"]
        return "\n".join(texts) or json.dumps(result)

    def close(self) -> None:
        if self.proc.stdin:
            self.proc.stdin.close()
        self.proc.terminate()
        self.proc.wait(timeout=5)


if __name__ == "__main__":
    client = MCPClient()
    try:
        print("Tools exposed by the MCP server:")
        for tool in client.openai_tools():
            print(f"  - {tool['function']['name']}: {tool['function']['description']}")
        print("\nCalling get_current_time() over MCP...")
        print("  " + client.call_tool("get_current_time", {}))
        print("\nCalling get_weather({'city': 'Wellington'}) over MCP...")
        print("  " + client.call_tool("get_weather", {"city": "Wellington"}))
    finally:
        client.close()

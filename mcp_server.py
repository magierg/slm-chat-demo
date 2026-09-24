"""A minimal MCP (Model Context Protocol) server over stdio.

It exposes the same tools as `chat.py` (`get_weather`, `get_forecast`,
`calculate`, `get_current_time`) so any MCP host - or our own `mcp_client.py` -
can list and call them.

Protocol: JSON-RPC 2.0, newline-delimited, over stdin/stdout. Only stdout carries
protocol messages; anything human-readable goes to stderr.

Try it by hand:

    echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python mcp_server.py

Or connect our client:

    python mcp_client.py
"""

import json
import sys

from slm import TOOLS, execute_tool

PROTOCOL_VERSION = "2024-11-05"

# MCP tool shape: {name, description, inputSchema}. Our built-ins already use the
# OpenAI shape, so this is just a rename of "parameters" -> "inputSchema".
TOOL_SPECS = [
    {
        "name": tool["function"]["name"],
        "description": tool["function"]["description"],
        "inputSchema": tool["function"]["parameters"],
    }
    for tool in TOOLS
]


def send(message: dict) -> None:
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def handle(method: str, params: dict) -> dict:
    if method == "initialize":
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "slm-chat-demo", "version": "1.0.0"},
        }

    if method == "ping":
        return {}

    if method == "tools/list":
        return {"tools": TOOL_SPECS}

    if method == "tools/call":
        name = params.get("name", "")
        arguments = params.get("arguments") or {}
        result = execute_tool(name, arguments)
        is_error = '"error"' in result
        return {"content": [{"type": "text", "text": result}], "isError": is_error}

    raise ValueError(f"unknown method: {method}")


def main() -> None:
    print("slm-chat-demo MCP server ready on stdio", file=sys.stderr)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue

        # Notifications (e.g. "notifications/initialized") carry no id and need no reply.
        if "id" not in request:
            continue

        request_id = request["id"]
        try:
            result = handle(request.get("method", ""), request.get("params") or {})
            send({"jsonrpc": "2.0", "id": request_id, "result": result})
        except Exception as error:  # noqa: BLE001 - report any failure back to the client
            send({
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32603, "message": str(error)},
            })


if __name__ == "__main__":
    main()

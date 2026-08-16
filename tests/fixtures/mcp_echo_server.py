#!/usr/bin/env python3
"""Minimal MCP stdio echo server for tests (JSON-RPC newline protocol)."""

from __future__ import annotations

import json
import sys


def reply(msg_id, result=None, error=None):
    payload = {"jsonrpc": "2.0", "id": msg_id}
    if error is not None:
        payload["error"] = error
    else:
        payload["result"] = result
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = msg.get("method")
        msg_id = msg.get("id")
        if method == "initialize":
            reply(
                msg_id,
                {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "echo-mcp", "version": "1.0.0"},
                },
            )
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            reply(
                msg_id,
                {
                    "tools": [
                        {
                            "name": "echo",
                            "description": "Echo arguments",
                            "inputSchema": {
                                "type": "object",
                                "properties": {"message": {"type": "string"}},
                            },
                        },
                        {
                            "name": "get-customer",
                            "description": "Fake CRM lookup",
                            "inputSchema": {
                                "type": "object",
                                "properties": {"customerId": {"type": "string"}},
                            },
                        },
                    ]
                },
            )
        elif method == "tools/call":
            params = msg.get("params") or {}
            name = params.get("name")
            args = params.get("arguments") or {}
            if name == "get-customer":
                text = json.dumps(
                    {
                        "customerId": args.get("customerId"),
                        "name": "Ada Lovelace",
                        "status": "active",
                    }
                )
            else:
                text = json.dumps({"echo": args})
            reply(
                msg_id,
                {
                    "content": [{"type": "text", "text": text}],
                    "isError": False,
                },
            )
        elif msg_id is not None:
            reply(msg_id, error={"code": -32601, "message": f"Method not found: {method}"})


if __name__ == "__main__":
    main()

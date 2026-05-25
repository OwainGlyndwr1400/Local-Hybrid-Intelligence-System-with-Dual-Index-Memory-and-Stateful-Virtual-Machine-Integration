"""Lumos MCP server (Phase 37) — exposes read-only tools to external clients.

What this is:
  A Model Context Protocol server that wraps our existing tool registry
  (`lumos_node.tools`) and exposes a curated subset (see `mcp_allowlist.py`)
  over stdio JSON-RPC. External MCP clients — Claude Desktop, Claude Code,
  Cline, any other MCP-aware tool — can spawn this server and call its
  tools to reach into Lumos's brain from outside the local HUD.

Why stdio:
  - Default MCP transport. Works out of the box with Claude Desktop's
    `mcpServers` config, Claude Code's `.mcp.json`, and most MCP clients.
  - Authentication via process boundary: the client spawned us; only the
    client's process can communicate with us. No port to lock down.
  - No CORS, no TLS, no auth tokens to manage. The right default for local-only.

Transport upgrade path:
  HTTP/SSE transport is a small change (`mcp.server.sse.SseServerTransport`
  instead of `mcp.server.stdio.stdio_server`). Add later if the operator
  wants network access — for now, stdio keeps the threat model small.

Design choices:
  1. **Schema pass-through.** Our tools register OpenAI-format JSON schemas
     in `tool.parameters`. MCP's `Tool.inputSchema` field accepts the same
     shape (both are JSON Schema). We pass them through unchanged — no
     translation, no information loss.
  2. **Tool descriptions stay identical.** No re-wording. If we tightened
     descriptions in Phase 35 to save tokens, those tighter descriptions
     also serve external MCP clients.
  3. **Re-uses the SAME FAISS indices the running `lumos serve` uses.**
     `get_identity_store()` / `get_knowledge_store()` are module-level
     singletons; we hit the same in-memory indices. External MCP queries
     see current state including dream-cycle consolidations.
  4. **Failure isolation.** A tool throwing an exception returns a MCP
     error message; never crashes the server. Operator sees the error in
     the client (Claude Desktop displays tool errors inline).
"""

from __future__ import annotations

import json
from typing import Any

from .log import get_logger
from .mcp_allowlist import MCP_EXPOSED_TOOLS
from .tools import execute_tool, get_registry


log = get_logger(__name__)


def _build_mcp_tool_list() -> list[dict[str, Any]]:
    """Construct the MCP-facing tool catalog from our registry, filtered by allowlist.

    Each entry is a dict matching `mcp.types.Tool` shape:
      {name: str, description: str, inputSchema: JSON Schema dict}

    Returned as plain dicts here so this function is testable without the
    MCP SDK imported. The actual server-side code wraps each in `Tool(...)`.
    """
    registry = get_registry()
    out: list[dict[str, Any]] = []
    for name, tool in registry.items():
        if name not in MCP_EXPOSED_TOOLS:
            continue
        out.append({
            "name": name,
            "description": tool.description,
            "inputSchema": tool.parameters,
        })
    out.sort(key=lambda t: t["name"])
    return out


def exposed_tool_count() -> int:
    """Count of registry tools that pass the allowlist filter. Useful for
    `lumos mcp-list-tools` CLI output and HANDOFF metrics."""
    registry = get_registry()
    return sum(1 for name in registry if name in MCP_EXPOSED_TOOLS)


async def _serve_stdio() -> None:
    """Main MCP server loop over stdio. Called from `lumos mcp-serve`."""
    # Imports kept local so the module loads even when `mcp` isn't installed
    # (e.g., during static analysis / partial deployments).
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import TextContent, Tool

    server: Server = Server("lumos")

    @server.list_tools()
    async def handle_list_tools() -> list[Tool]:
        catalog = _build_mcp_tool_list()
        log.info("mcp.list_tools", count=len(catalog))
        return [
            Tool(
                name=t["name"],
                description=t["description"],
                inputSchema=t["inputSchema"],
            )
            for t in catalog
        ]

    @server.call_tool()
    async def handle_call_tool(
        name: str, arguments: dict[str, Any] | None = None
    ) -> list[TextContent]:
        # Allowlist enforcement — even if a client sends a tool name not in
        # the catalog, we reject it. This is defense-in-depth on top of the
        # list_tools filtering.
        if name not in MCP_EXPOSED_TOOLS:
            log.warning("mcp.call_blocked", tool=name, reason="not_in_allowlist")
            return [
                TextContent(
                    type="text",
                    text=json.dumps({
                        "error": f"tool {name!r} not exposed via MCP",
                        "exposed_tools": sorted(MCP_EXPOSED_TOOLS),
                    }),
                )
            ]
        args = arguments or {}
        log.info("mcp.call_tool", tool=name, args_keys=list(args.keys()))
        result_str = await execute_tool(name, args)
        return [TextContent(type="text", text=result_str)]

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def run() -> None:
    """Entry point for `lumos mcp-serve` CLI command.

    Blocks until the parent client closes the stdio streams (typical lifetime
    is one Claude Desktop session). On any unhandled exception, logs and
    re-raises so the parent client sees the failure.
    """
    import asyncio

    log.info("mcp.server.starting", exposed_tools=exposed_tool_count())
    try:
        asyncio.run(_serve_stdio())
    except KeyboardInterrupt:
        log.info("mcp.server.interrupted")
    except Exception as e:  # noqa: BLE001
        log.error("mcp.server.crashed", error=str(e))
        raise
    finally:
        log.info("mcp.server.shutdown")

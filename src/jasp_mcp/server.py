"""
JASP MCP Server.

Bridges JASP's JSON-RPC v2 API to MCP tools so AI assistants can create
analyses, set options, retrieve results, and manage data in JASP.

**Fully dynamic:** on startup the server calls JASP's ``rpc.discover`` and
registers every available method as an MCP tool.  No method schemas are
embedded — if JASP is not reachable the server refuses to start.

Usage:
    uvx jasp-mcp          # after publishing
    python -m jasp_mcp    # during development

Configuration:
    JASP_RPC_URL  — JASP RPC endpoint (default: http://127.0.0.1:48164/rpc)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

import mcp.server.stdio
import mcp.types as types
from mcp.server.lowlevel import NotificationOptions, Server
from mcp.server.models import InitializationOptions

from .jasp_client import JASPClient, JASPClientError

# ============================================================================
# Server instructions — shown to the AI client to guide usage
# ============================================================================

SERVER_INSTRUCTIONS = """\
You are connected to JASP statistical software through this MCP server.

## Workflow for running an analysis

1.  **Load data** — Use `jasp_data_load` with a file path. For small datasets,
    pass `wait=true` (default) for a blocking call that returns column metadata.
    For large files, pass `wait=false` to start an async load — poll with
    `jasp_data_load_status` until the status is `"complete"`. Then use
    `jasp_data_info` to inspect columns and their types.

    Both `jasp_data_load` and `jasp_data_load_status` default to a 30-second
    timeout (`timeoutMs`). For large files, pass a higher `timeoutMs` on
    `jasp_data_load_status` when polling so the poll doesn't fire prematurely.
2.  **Discover analyses** — Use `jasp_modules_list` to see available modules and analyses.
3.  **Get analysis context** — Use `jasp_analysis_context` for the target analysis. This
    returns help text describing every available option, its type, valid values,
    and allowed column types. Always do this before configuring an unfamiliar analysis.
4.  **Create the analysis** — Use `jasp_analysis_create` with the module and analysis name.
    Save the returned `analysisId`. The response includes default `options` and
    `optionMeta` describing the kind of each control.
5.  **Configure and run** — Use `jasp_analysis_run` with the `analysisId` and an
    options object. This sets options and runs the analysis in a single call.
    `jasp_analysis_run` defaults to a 30-second timeout (`timeoutMs`). For
    complex analyses (e.g. Bayesian methods, bootstrapping, large datasets),
    pass a higher `timeoutMs` to avoid unnecessary polling. Common option patterns:
    - Checkboxes: `{ "wantsEffectSize": true }`
    - Dropdowns/combo boxes: `{ "hypothesis": "groupOneGreater" }`
    - Variable assignments: `{ "dependent": ["score"], "groupingVariable": ["group"] }`
    - Numbers: `{ "confidenceInterval": 0.95 }`
    Always consult the `optionMeta` from `jasp_analysis_create`,
    `jasp_analysis_getOptions`, or `jasp_analysis_context` to know which
    options are available and what shape their values should take.

    `jasp_analysis_run` returns one of:
    - `"success"` — analysis finished; the `results` field contains tables, plots, and notes.
    - `"running"` — timeout elapsed but the analysis is still computing; poll with
      `jasp_analysis_results` (also defaults to 30 s; pass a higher `timeoutMs` if needed).
    - `"error"` — options validation failed; check the `message` field for details.

    Some analyses show/hide options or change available choices based on prior
    selections. The `optionMeta` in the response always reflects the current state —
    inspect it for newly revealed controls and call `jasp_analysis_run` again with
    any newly surfaced options, iterating until satisfied.
6.  **Poll if running** — If step 5 returned `"running"`, poll with
    `jasp_analysis_results` (same `analysisId`) until the status is `"success"`.
    Do NOT call `jasp_analysis_results` if step 5 already returned `"success"` —
    the results are already final.
7.  **Compose output** — Use `jasp_analysis_composeResults` to reorder result
    elements, insert Markdown annotations (use `#` for headings), or present a
    curated subset of tables and plots.

## Important notes

- Variable lists expect an array of column name strings.
- Plot results contain base64-encoded images under `"data"` keys — summarise
  these rather than including the raw base64 in your response.
- Strongly consider using the default for RadioButtonGroup if that choice is appropriate.
- If an analysis returns `"fatalError"`, use `jasp_analysis_getOptions` to review the
  current options and check that required variables are assigned and options are valid.
"""

# ============================================================================
# Dynamic tool builder
# ============================================================================


def _build_tools(methods: list[dict[str, Any]]) -> list[types.Tool]:
    """Build MCP tools from the full ``rpc.discover`` method definitions.

    Each method dict follows the OpenRPC spec: ``name``, ``summary``,
    ``params`` (each with ``name``, ``description``, ``required``, and a
    ``schema`` key containing a JSON Schema descriptor), and ``result``.
    """
    tools: list[types.Tool] = []
    for m in methods:
        properties: dict[str, Any] = {}
        required: list[str] = []
        for p in m.get("params", []):
            prop: dict[str, Any] = dict(p["schema"])
            prop.setdefault("description", p.get("description", ""))
            properties[p["name"]] = prop
            if p.get("required"):
                required.append(p["name"])
        input_schema: dict[str, Any] = {"type": "object", "properties": properties}
        if required:
            input_schema["required"] = required
        tools.append(
            types.Tool(
                name=f"jasp_{m['name']}",
                description=m.get("summary", f"JASP RPC method: {m['name']}"),
                inputSchema=input_schema,
            )
        )
    return tools


# ============================================================================
# MCP server
# ============================================================================

server = Server("jasp-mcp")

# Populated lazily on first successful discovery.
_tools: list[types.Tool] = []
_jasp: JASPClient | None = None
_session: Any = None  # stored from first handler, used by poller to notify


async def _try_discover() -> bool:
    """Attempt to discover JASP methods and populate ``_tools``.

    Returns ``True`` if tools were just discovered (transitioned from
    empty → populated).
    """
    global _tools
    if _tools or _jasp is None:
        return False
    try:
        result = await _jasp.call("rpc_discover", {})
        methods: list[dict[str, Any]] = result.get("methods", [])
        if methods:
            _tools = _build_tools(methods)
            print(
                f"Discovered {len(_tools)} JASP RPC method(s) → MCP tools.",
                file=sys.stderr,
            )
            return True
    except (JASPClientError, OSError):
        pass
    return False


async def _notify_client() -> None:
    """Send ``tools/list_changed`` if we have a session."""
    if _session is not None:
        try:
            await _session.send_notification(types.ToolListChangedNotification())
        except Exception:
            pass  # session may have disconnected


async def _poll_jasp(interval: float = 2.0) -> None:
    """Background task: periodically try to discover JASP.

    If tools are already populated, does a health check via ``ping``.
    On disconnect, clears tools and notifies the client so it knows
    the tools are temporarily unavailable.
    """
    global _tools
    while True:
        if _tools:
            # Health check: if JASP disappeared, clear tools and notify.
            try:
                await _jasp.call("ping", {})
            except (JASPClientError, OSError):
                _tools = []
                await _notify_client()
        else:
            if await _try_discover():
                await _notify_client()
        await asyncio.sleep(interval)


@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    """Return the dynamically-discovered list of JASP MCP tools."""
    global _session
    _session = server.request_context.session
    await _try_discover()
    return _tools


@server.call_tool()
async def handle_call_tool(
    name: str, arguments: dict[str, Any]
) -> list[types.TextContent]:
    """Dispatch a tool call to the JASP JSON-RPC endpoint."""
    global _jasp, _session
    _session = server.request_context.session
    await _try_discover()
    if _jasp is None:
        return [
            types.TextContent(
                type="text",
                text="Error: JASP client is not initialised. "
                "The server may not have started correctly.",
            )
        ]

    try:
        # Strip the jasp_ prefix to get the JASP RPC method name
        jasp_method = name.removeprefix("jasp_")
        result = await _jasp.call(jasp_method, arguments)
        if isinstance(result, str):
            text = result
        else:
            text = json.dumps(result, indent=2, ensure_ascii=False, default=str)
        return [types.TextContent(type="text", text=text)]

    except JASPClientError as exc:
        detail = f"JASP RPC error: {exc}"
        if exc.code is not None:
            detail += f" (code {exc.code})"
        return [types.TextContent(type="text", text=detail)]

    except OSError as exc:
        return [
            types.TextContent(
                type="text",
                text=(
                    f"Cannot reach JASP at {_jasp.url!r}: {exc}. "
                    "Make sure JASP is running with the RPC server enabled."
                ),
            )
        ]


# ============================================================================
# Entry point
# ============================================================================


async def run(jasp_url: str | None = None) -> None:
    """Start the MCP server on stdio.

    The server begins serving immediately.  JASP method discovery happens
    lazily on the first ``list_tools`` or ``call_tool`` request — if JASP
    is not yet running the server will keep retrying on each call.
    """
    global _jasp

    async with JASPClient(url=jasp_url) as client:
        _jasp = client
        print(
            f"JASP MCP server ready (JASP endpoint: {client.url!r}).",
            file=sys.stderr,
        )

        # Build capabilities with listChanged so the client knows to re-query
        # when tools are discovered lazily.
        caps = server.get_capabilities(
            notification_options=NotificationOptions(),
            experimental_capabilities={},
        )
        caps.tools = types.ToolsCapability(listChanged=True)

        async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
            # Start background poller in case JASP isn't up yet
            poll_task = asyncio.create_task(_poll_jasp())
            await server.run(
                read_stream,
                write_stream,
                InitializationOptions(
                    server_name="jasp-mcp",
                    server_version="0.1.0",
                    capabilities=caps,
                    instructions=SERVER_INSTRUCTIONS,
                ),
            )
            poll_task.cancel()
            try:
                await poll_task
            except asyncio.CancelledError:
                pass


def main() -> None:
    """CLI entry point for ``jasp-mcp`` / ``python -m jasp_mcp``."""
    parser = argparse.ArgumentParser(
        description="JASP MCP Server — bridge JASP's JSON-RPC API to MCP tools."
    )
    parser.add_argument(
        "--url",
        default=None,
        help=(
            "JASP RPC endpoint URL "
            "(overrides JASP_RPC_URL env var; default http://127.0.0.1:48164/rpc)"
        ),
    )
    args = parser.parse_args()
    asyncio.run(run(jasp_url=args.url))


if __name__ == "__main__":
    main()

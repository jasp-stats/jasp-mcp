# JASP MCP Server

An [MCP (Model Context Protocol)](https://modelcontextprotocol.io) server that
bridges AI assistants to [JASP](https://jasp-stats.org/) statistical software
via JASP's JSON-RPC v2 API.

## Quick Start

```bash
# Install and run with uvx (recommended)
uvx jasp-mcp
```

Or during development:

```bash
git clone <repo> && cd JASP_MCP
uv run python -m jasp_mcp
```

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `JASP_RPC_URL` | `http://127.0.0.1:48164/rpc` | JASP JSON-RPC v2 endpoint |

Make sure JASP is running with its remote RPC server enabled before starting
the MCP server.

## Available Tools

The server exposes every method from JASP's OpenRPC spec as an MCP tool:

### Meta
- **`ping`** — Connectivity check.
- **`rpc_discover`** — List all registered JASP RPC methods.

### Modules
- **`modules_list`** — List loaded modules and their analyses.

### Analysis Lifecycle
- **`analysis_create`** — Create and start an analysis.
- **`analysis_setOptions`** — Set options on an existing analysis.
- **`analysis_getOptions`** — Retrieve current analysis options.
- **`analysis_results`** — Retrieve analysis results (tables, plots).
- **`analysis_status`** — Query analysis run status.
- **`analysis_context`** — Retrieve QML form, help text, and metadata.

### Data Management
- **`data_load`** — Load a data file (synchronous).
- **`data_load_async`** — Start async data load.
- **`data_load_status`** — Poll async load job.
- **`data_info`** — Get dataset metadata.

## MCP Client Configuration

Add to your MCP client's config (e.g., Claude Desktop):

```json
{
  "mcpServers": {
    "jasp": {
      "command": "uvx",
      "args": ["jasp-mcp"],
      "env": {
        "JASP_RPC_URL": "http://127.0.0.1:48164/rpc"
      }
    }
  }
}
```

## Typical Workflow

1. **`jasp_data_load`** — Load a dataset into JASP. For small datasets
   `jasp_data_load` is synchronous and returns immediately. Large datasets
   should be loaded with `jasp_data_load_async` to avoid timeouts — poll
   with `jasp_data_load_status` until the status is `"complete"`. Then
   `jasp_data_info` to inspect columns and their types.
2. **`jasp_modules_list`** — Browse available modules and analyses.
3. **`jasp_analysis_context`** — Get the QML form and help text for a
   specific analysis. This describes every available option, its type, valid
   values, and allowed column types.
4. **`jasp_analysis_create`** — Create an analysis instance. Save the
   returned `analysisId`.
5. **`jasp_analysis_setOptions`** — Configure the analysis. Use
   `jasp_analysis_setOptions` with the `analysisId` and an options object.
   The options object keys are the control names from the QML form or
   `optionMeta`. Common option patterns:
   - Checkboxes: `{ "wantsEffectSize": true }`
   - Dropdowns/combo boxes: `{ "hypothesis": "groupOneGreater" }`
   - Variable assignments: `{ "dependent": ["score"], "groupingVariable": ["group"] }`
   - Numbers: `{ "confidenceInterval": 0.95 }`
   Always consult `jasp_analysis_context` or the `optionMeta` from
   `jasp_analysis_create` first.

   `jasp_analysis_setOptions` returns the updated `options` and
   `optionMeta`. Analyse these for newly revealed controls or updated
   valid values (some analyses show/hide options or change available
   choices based on prior selections). Call `jasp_analysis_setOptions`
   again with any newly surfaced options, and iterate until satisfied.
6. **`jasp_analysis_status`** — Wait for the analysis to complete (status
   `"complete"`).
7. **`jasp_analysis_results`** — Retrieve tables, plots, and notes for
   interpretation.

## License

MIT

# JASP MCP Server

An [MCP (Model Context Protocol)](https://modelcontextprotocol.io) server that
bridges AI assistants to [JASP](https://jasp-stats.org/) statistical software
via JASP's JSON-RPC v2 API.

The server is **fully dynamic**: on startup it calls JASP's `rpc.discover` and
registers every available method as an MCP tool. No schemas are hardcoded.

## MCP Client Configuration

### Claude Desktop

Add to `~/.config/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "jasp": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/jasp-stats/jasp-mcp", "jasp-mcp"],
      "env": {
        "JASP_RPC_URL": "http://127.0.0.1:48164/rpc"
      }
    }
  }
}
```

### Zed

Add to `~/.config/zed/settings.json`:

```json
{
  "context_servers": {
    "jasp": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/jasp-stats/jasp-mcp", "jasp-mcp"],
      "env": {
        "JASP_RPC_URL": "http://127.0.0.1:48164/rpc"
      }
    }
  }
}
```

### VS Code / Cursor / Continue

Add to `.vscode/mcp.json`:

```json
{
  "mcpServers": {
    "jasp": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/jasp-stats/jasp-mcp", "jasp-mcp"],
      "env": {
        "JASP_RPC_URL": "http://127.0.0.1:48164/rpc"
      }
    }
  }
}
```

## Quick Start

```bash
# Install and run with uvx (recommended)
uvx --from git+https://github.com/jasp-stats/jasp-mcp jasp-mcp
```

Or clone and run locally:

```bash
git clone https://github.com/jasp-stats/jasp-mcp.git
cd jasp-mcp
uv run python -m jasp_mcp
```

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `JASP_RPC_URL` | `http://127.0.0.1:48164/rpc` | JASP JSON-RPC v2 endpoint |

Make sure JASP is running with its remote RPC server enabled before starting
the MCP server. The server polls JASP in the background — it will register
tools automatically once JASP becomes reachable.

## Available Tools

All JASP RPC methods are exposed as MCP tools with a `jasp_` prefix.

### Meta

- **`jasp_ping`** — Connectivity check. Returns `"pong"`.
- **`jasp_rpc_discover`** — List all registered JASP RPC methods with full schemas.

### Data Management

- **`jasp_data_load`** — Load a CSV, SPSS, JASP, Excel, etc. file. Pass `wait=true` (default) for blocking load; pass `wait=false` for async (returns a `jobId` for polling).
- **`jasp_data_load_status`** — Poll or block on an async data load job by `jobId`.
- **`jasp_data_info`** — Get metadata about the currently loaded dataset (columns, types, row count).

### Modules & Analyses

- **`jasp_modules_list`** — List all loaded modules and their available analyses.
- **`jasp_analyses_list`** — List all analyses currently in the workspace.
- **`jasp_analysis_context`** — Get help text and metadata for an analysis (describes every option, its type, valid values, and allowed column types).
- **`jasp_analysis_create`** — Create a new analysis instance. Returns the `analysisId` and default options with metadata.
- **`jasp_analysis_getOptions`** — Retrieve the current options of an existing analysis.
- **`jasp_analysis_run`** — Set options and run an analysis. This is the primary tool for both configuration and execution. Returns results on completion, or a `"running"` status if the timeout fires (poll with `jasp_analysis_results`).
- **`jasp_analysis_results`** — Poll for results of a running analysis (only needed when `jasp_analysis_run` times out).
- **`jasp_analysis_composeResults`** — Reorder, slice, and annotate an analysis's results. Insert Markdown annotations alongside result tables and plots.

## Typical Workflow

### 1. Load data

Use `jasp_data_load` with a file path. For small datasets it blocks and
returns immediately with column metadata. For large files, pass `wait=false`
to avoid timeouts — poll with `jasp_data_load_status` until the status is
`"complete"`. Then use `jasp_data_info` to inspect columns and their types.

### 2. Discover analyses

Use `jasp_modules_list` to browse available modules and analyses.

### 3. Get analysis context

Use `jasp_analysis_context` for the target analysis. This returns help text
describing every available option, its type, valid values, and allowed
column types. Always do this before configuring an unfamiliar analysis.

### 4. Create the analysis

Use `jasp_analysis_create` with the module and analysis name. Save the
returned `analysisId`. The response includes default `options` and
`optionMeta` describing the kind of each control.

### 5. Configure and run

Use `jasp_analysis_run` with the `analysisId` and an options object.
This sets options and starts the analysis in one call. Common option patterns:

- **Checkboxes**: `{ "wantsEffectSize": true }`
- **Dropdowns / combos**: `{ "hypothesis": "groupOneGreater" }`
- **Variable assignments**: `{ "dependent": ["score"], "groupingVariable": ["group"] }`
- **Numbers**: `{ "confidenceInterval": 0.95 }`

Always consult the `optionMeta` from `jasp_analysis_create`, `jasp_analysis_getOptions`,
or `jasp_analysis_context` to know which options are available and what shape
their values should take.

`jasp_analysis_run` returns one of:

| Status | Meaning |
|--------|---------|
| `"success"` | Analysis finished; `results` contains tables, plots, and notes. |
| `"running"` | Timeout elapsed; poll with `jasp_analysis_results`. |
| `"error"` | Options validation failed; check `message` for details. |

Some analyses show or hide options based on prior selections. The
`optionMeta` in the response always reflects the current state — inspect it
for newly revealed controls and iterate with additional `jasp_analysis_run`
calls if needed.

### 6. Poll if still running

If `jasp_analysis_run` returned `"running"`, poll with `jasp_analysis_results`
(using the same `analysisId`) until the status is `"success"`.

### 7. Interpret results

The results object contains tables, plots, and notes. Plots include
base64-encoded images under `"data"` keys — summarise these rather than
including raw base64 in your response.

Use `jasp_analysis_composeResults` to reorder output, insert annotations,
or present a curated subset of results.

## License

MIT

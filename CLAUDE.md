# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository overview

This repo contains three co-located but independently versioned projects:

| Directory | What it is |
|-----------|------------|
| `aisuite/` | Python library: unified Chat + Agents API across 20+ LLM providers |
| `platform/` | OpenCoworker desktop agent app (FastAPI backend + React/Tauri GUI) built on aisuite |
| `aisuite-js/` | TypeScript mirror of aisuite's Chat API (no Agents API yet) |

`aisuite/` is the stable API layer. `platform/` is the application layer. Changes to `aisuite/` public interfaces ripple into `platform/` and `aisuite-js/`.

---

## Commands

### aisuite (Python library)

```bash
# Install with all provider SDKs + dev + test deps
poetry install --all-extras --with dev,test

# Run fast unit tests (no API calls)
poetry run pytest -m "not integration and not llm and not mcp_server"

# Run a single test file
poetry run pytest tests/agents/test_runner.py

# Run a single test
poetry run pytest tests/agents/test_runner.py::test_name

# Run integration tests (requires API keys in environment)
poetry run pytest -m integration

# Format check
poetry run black --check .

# Auto-format
poetry run black .
```

### platform / OpenCoworker (Python app)

```bash
# Install (creates its own venv, aisuite sourced from this worktree via .pth)
cd platform
pip install -e ".[dev]"

# Run the server (requires at least one of OPENAI_API_KEY / ANTHROPIC_API_KEY / GOOGLE_API_KEY)
./.venv/bin/coworker-server --cwd /path/to/project --port 8765
# or
coworker-server --cwd . --port 8765

# Run the TUI
coworker

# Run platform tests
cd platform
pytest

# Run a single platform test
pytest tests/test_engine.py
```

### platform GUI (React)

```bash
# Two-terminal dev workflow:
# Terminal 1 — start server (see above)

# Terminal 2 — start UI
cd platform/surfaces/gui
npm install     # first time only
npm run dev     # → http://localhost:5173

# Build for production
npm run build
```

### aisuite-js (TypeScript)

```bash
cd aisuite-js
npm install
npm run build        # tsc compile
npm test             # jest unit tests
npm run dev          # tsc --watch
```

---

## Architecture

### aisuite library layer

**Provider pattern** (`aisuite/provider.py`): New providers require only one file — `aisuite/providers/{name}_provider.py` — with a class named `{Name}Provider(Provider)`. `ProviderFactory` discovers it by glob. Model routing uses `"provider:model-name"` strings everywhere.

**Async strategy**: `Provider.achat_completions_create()` defaults to `asyncio.to_thread()` wrapping the sync method. Providers with native async SDKs (OpenAI, Anthropic) override it. All providers are awaitable without per-provider effort.

**Tool calling flow** (`aisuite/utils/tools.py`):
1. Python functions → schema inferred from signature + docstring (via `docstring-parser` + Pydantic)
2. OpenAI-format JSON specs → passed through as-is
3. MCP config dicts (`{"type": "mcp", ...}`) → auto-instantiated as `MCPClient` and tools extracted

When `max_turns` is set, `client.chat.completions.create()` runs the full tool loop internally. Without `max_turns`, it returns tool-call requests for manual handling.

**Agents API** (`aisuite/agents/`): `Agent` is a pure dataclass (name, model, instructions, tools). `Runner.run()` is the async executor — it manages turns, calls `Tools.aexecute_tool()`, applies `ToolPolicy` before each execution, persists state via `StateStore`, and emits `TraceEvent`s to `TraceSink`s. `Runner.run_sync()` is a blocking wrapper.

**State persistence** (`aisuite/agents/state_store.py`): Three implementations — `InMemoryStateStore` (tests), `FileStateStore` (local JSON with atomic writes), `PostgresStateStore` (production). All use optimistic concurrency via a `revision` field. Resume a run by passing the same `thread_id` to `Runner.run()`.

**MCP integration** (`aisuite/mcp/client.py`): `MCPClient` supports both stdio (`command` + `args`) and HTTP (`server_url`) transports. Tools are exposed as Python callables. Lazy-connected, auto-reconnects on disconnect.

### platform layer

**Dual provider abstraction**: `platform/coworker/providers/` is a *separate* provider layer from `aisuite/providers/`. The platform layer is streaming-first and event-driven (yields `Event` objects for UI consumption). Currently implements OpenAI, Anthropic, Google Gemini. This duplication is intentional for the streaming architecture.

**TurnEngine** (`platform/coworker/engine.py`): The core async event loop. Receives user input, calls `ProviderClient.stream()`, executes tool calls concurrently for reads / sequentially for writes and shell, applies `PermissionEngine` gating, and yields typed `Event` objects consumed by the GUI or TUI. Supports mid-turn interrupt via `request_interrupt()` and steering via `queue_steering()`.

**PermissionEngine** (`platform/coworker/permissions.py`): Four execution modes — `DISCUSS` (read-only, no tools), `PLAN` (propose plan, await approval), `APPROVAL` (execute pre-approved plan), `INTERACTIVE` (approve each tool call). Mode is set at runtime.

**Tool assembly** (`platform/coworker/agent.py`): `assemble_engine()` wires `Agent.tools + aisuite.toolkits + skills + connectors + memory tools` into a `ToolRegistry`, then into a `TurnEngine`. This is the integration point where all tool sources are combined.

**Connectors** (`platform/coworker/connectors/`): Plugin system for external integrations (email, Slack, browser automation). Each connector exposes callable tools via `get_tools()`. Credentials stored via platform-native secret stores (Keychain / Credential Manager).

**Skills** (`platform/coworker/skills/`): Dynamically loaded Python modules from `~/.coworker/skills/`. `SkillLoader` discovers and hot-loads them; their functions are exposed as agent tools. No core changes needed to add a skill.

### aisuite-js layer

Mirrors the Python Chat API only: `Client` → `chat.completions.create()` with streaming. Same `"provider:model"` routing. No Agents API, no `Runner`, no `StateStore`. Provider implementations: OpenAI, Anthropic, Mistral, Groq, Deepgram (ASR).

---

## Key conventions

**Model string format**: always `"provider:model-name"` (e.g., `"anthropic:claude-sonnet-4-6"`, `"openai:gpt-4o"`).

**Tool schema inference**: aisuite infers tool JSON schemas from Python function signatures and Google-style docstrings. Param descriptions come from the `Args:` section. Keep docstrings accurate — they are the tool descriptions the model sees.

**Test markers**: Use `@pytest.mark.integration` for tests hitting real APIs, `@pytest.mark.llm` for tests that incur LLM costs (subset of integration), `@pytest.mark.mcp_server` for tests requiring a live MCP server process.

**Tracing**: Trace events write to `.aisuite/events.jsonl` by default via `LocalTraceSink`. The embedded viewer UI (`aisuite/tracing/viewer.py`) renders this file for debugging agent runs.

**Platform server ↔ GUI contract**: The GUI communicates with the FastAPI server over HTTP + WebSocket. Override the server address with env vars `VITE_COWORKER_HTTP` / `VITE_COWORKER_WS` for non-default ports.

**Code generation**: Claude never writes production code until it has written documentation explaining why.
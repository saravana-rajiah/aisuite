# Repository Guide for New Senior Engineers

> This is not a file inventory. It tells you what each part of the codebase is *for*, what matters inside it, and how the pieces connect.

---

## Codebase in One Sentence

This repo is **aisuite** — a provider-agnostic Python library for building with LLMs — plus **OpenCoworker**, a production desktop AI agent application built on top of it. The library and the app live in the same git repository but are independently installable Python packages.

---

## Folder Map

```
ocw-proposalfactory/
│
├── aisuite/              ← Python library (the stable API layer)
├── platform/             ← OpenCoworker desktop app (the application layer)
├── aisuite-js/           ← TypeScript port of the chat API (browser/Node.js)
│
├── tests/                ← Tests for aisuite/ (mirrors its structure)
├── docs/                 ← Architecture docs, quickstarts
├── guides/               ← Per-provider setup guides
├── examples/             ← Notebooks and example apps
└── cli/                  ← Standalone code-analysis CLI tool
```

**Dependency direction:**

```
aisuite-js/   (no Python dependency)
    ↑
platform/  →  aisuite/  →  LLM provider SDKs
               ↑
             tests/
```

`aisuite/` knows nothing about `platform/`. `platform/` imports from `aisuite/`. Neither depends on `aisuite-js/`.

---

## `aisuite/` — The Library

The library has two public layers stacked on top of each other.

### Layer 1 — Chat Completions API

A thin normalization layer. Call it like the OpenAI SDK; it routes to any of 22 providers.

```python
import aisuite as ai
client = ai.Client()
client.chat.completions.create(model="anthropic:claude-sonnet-4-6", messages=[...])
```

**What matters:**

| Class / File | What it does |
|---|---|
| `client.py` → `Client` | Entry point. Owns `Chat` and `Audio` sub-objects. Parses `"provider:model"` strings. When `max_turns` is set, runs the tool loop internally. |
| `provider.py` → `Provider` (ABC) | One abstract method: `chat_completions_create()`. Default `achat_completions_create()` wraps it with `asyncio.to_thread()` — all providers are awaitable for free. |
| `provider.py` → `ProviderFactory` | Discovers `aisuite/providers/{name}_provider.py` by glob. No registration needed — drop a file and it works. |
| `providers/` | 22 adapter files. Each normalizes its SDK's request/response format to the OpenAI schema. The interesting ones are `openai_provider.py` (overrides async natively) and `anthropic_provider.py`. |
| `framework/message.py` | Canonical response types: `ChatCompletionResponse`, `Message`, `ChatCompletionMessageToolCall`. Every provider normalizes to these. |

### Layer 2 — Agents API

Sits on top of the Chat API. Adds tools, multi-turn loops, persistence, and observability.

```python
from aisuite import Agent, Runner
agent = Agent(name="helper", model="openai:gpt-4o", tools=[my_fn])
result = Runner.run(agent, "Do the thing.")
```

**What matters:**

| Class / File | What it does |
|---|---|
| `agents/types.py` → `Agent` | Pure dataclass: name, model, instructions, tools list, model settings. No logic. |
| `agents/runner.py` → `Runner` | The async executor. Calls the model, detects tool calls, executes them, loops until done or `max_turns`. Wires state, artifacts, tracing, and tool policy together. `run_sync()` is a blocking wrapper via `nest_asyncio`. |
| `utils/tools.py` → `Tools` | Schema inference engine. Takes Python functions → infers JSON schema from signature + docstring → validates args with Pydantic → calls the function → enforces `ToolPolicy`. This is where `max_turns` magic lives. |
| `agents/state_store.py` | Protocol + three implementations: `InMemoryStateStore` (tests), `FileStateStore` (JSON, atomic writes), `PostgresStateStore` (production, optimistic concurrency via `revision` field). Pass `thread_id` to resume a prior run. |
| `agents/policies.py` | `AllowAllToolPolicy`, `DenyAllToolPolicy`, `AllowToolsPolicy`, `RequireApprovalPolicy`. Also `tool()` decorator to attach metadata to a function. |
| `agents/artifact_store.py` | Dehydrates large tool outputs out of the message history. `FileArtifactStore` stores to `.aisuite/artifacts/`. |
| `mcp/client.py` → `MCPClient` | Wraps any MCP server (stdio subprocess or HTTP) as Python callables. Lazy-connect, auto-reconnect. Inline config dicts (`{"type": "mcp", ...}`) in `tools=` are auto-instantiated by `Client`. |
| `toolkits/` | Prebuilt sandboxed tool families returned as callable lists: `files()`, `git()`, `shell()`. Pass directly to `Agent(tools=[...])`. |
| `tracing/sinks.py` | `TraceEvent` + `TraceSink` protocol. `LocalTraceSink` writes `.aisuite/events.jsonl`. `tracing/viewer.py` is an embedded HTML trace viewer. |

---

## `platform/` — The Application

OpenCoworker is a desktop AI agent built on aisuite. It is a FastAPI HTTP server with a React/Tauri GUI in front of it and a Python backend behind.

### Important note: Two provider layers

`platform/coworker/providers/` is a **separate** provider abstraction from `aisuite/providers/`. The platform reimplements it because it needs streaming-first, event-driven output. The platform layer supports only OpenAI, Anthropic, and Google. This duplication is intentional but is the most significant piece of technical debt to be aware of.

### Sub-folder map

```
platform/
├── coworker/             ← Python backend package (pip install -e .)
│   ├── engine.py         ← TurnEngine: the async agent loop
│   ├── agent.py          ← build_engine(): wires everything together
│   ├── permissions.py    ← PermissionEngine: mode-based access control
│   ├── roots.py          ← RootDir: directory access model
│   ├── providers/        ← Streaming LLM adapters (separate from aisuite's)
│   ├── tools/            ← Platform-specific tool implementations
│   ├── connectors/       ← Messaging platform adapters (Slack, Telegram, email)
│   ├── memory/           ← Cross-session persistent memory (SQLite)
│   ├── skills/           ← Hot-loadable skill modules (SKILL.md format)
│   ├── automation/       ← Cron scheduler + task store
│   ├── server/           ← FastAPI app + session lifecycle
│   ├── agents.py         ← Agent definitions (code_agent, cowork_agent, etc.)
│   ├── config.py         ← Config loading (config.json + CLI args + env)
│   └── secrets.py        ← Credential store (Keychain / Credential Manager / env)
│
└── surfaces/
    ├── gui/              ← React + Tauri desktop app
    └── tui/              ← Textual terminal UI
```

### Important classes

**`TurnEngine`** (`engine.py`) — The core of everything. One instance per session. Async generator: `engine.run(user_input)` yields `Event` objects that the GUI or TUI consume. The loop: stream model output → collect tool calls → authorize each → execute concurrently (reads) or serially (writes/shell) → append results → next iteration. `request_interrupt()` cancels mid-turn. `queue_steering()` injects text mid-turn.

**`PermissionEngine`** (`permissions.py`) — Stateful gate that sits between the `TurnEngine` and tool execution. Five modes:

| Mode | Behavior |
|---|---|
| `DISCUSS` | Read-only. Writes and shell blocked. No plan workflow. |
| `PLAN` | Read-only. Agent is steered toward `propose_plan` before executing. |
| `INTERACTIVE` | Reads auto-approved. Writes and shell prompt the user. |
| `AUTO` | Full access, path-scoped only. |
| `CUSTOM` | Interactive + a configured allowlist of auto-approved tools. |

Shares the same `list[RootDir]` reference with the file toolkit and context injector — adding a folder mid-session is seen everywhere immediately.

**`build_engine()`** (`agent.py`) — Assembly function. Call it with an `Agent` + workspace path + model + mode and it returns a wired `TurnEngine`. It is the integration seam: takes tools from the agent definition, toolkits, connectors, web search, memory tools, skill catalog, scheduling tools, and the `propose_plan` meta-tool, and registers them all into a `ToolRegistry`.

**`SessionManager`** (`server/manager.py`) — Owns the lifecycle of named sessions. Creates `TurnEngine` instances on demand, routes incoming user turns to the right engine, and holds the shared `MemoryStore`, `TaskStore`, and `Scheduler`.

**`RootDir`** (`roots.py`) — The directory access model. A `list[RootDir]` is shared by reference across `PermissionEngine`, the file toolkit, and the per-turn `context_provider` lambda. Mutating the list mid-session (via `request_directory` tool approval) is immediately visible to all three consumers.

**`MemoryStore`** (`memory/base.py`) — Abstract store for durable cross-session facts. Three scopes: `GLOBAL` (user-wide), `WORKSPACE` (per project), `SESSION`. The agent calls `remember()` / `memory_update()` / `memory_forget()` as tools. Known memories are injected into the system prompt at session start.

**`SkillLoader`** (`skills/base.py`) — Discovers `SKILL.md` modules from `~/.coworker/skills/` and `{workspace}/.coworker/skills/`. Progressive disclosure: at session start the agent receives only a catalog (name + one-line description). It calls `load_skill(name)` to load full instructions on demand, keeping context window usage proportional to actual need.

**`Scheduler`** (`automation/scheduler.py`) — `asyncio.Task` that ticks every 30 seconds. Two hard-coded policies: run-once catch-up on startup (fires any overdue task exactly once), skip-on-overlap (no stacking). The actual agent execution is injected as a `runner` callback — the scheduler knows nothing about LLMs.

### Major services (what runs at runtime)

| Service | Entry point | Role |
|---|---|---|
| `coworker-server` | `coworker/server/run.py:main()` | uvicorn FastAPI server. The single long-running process. Hosts all sessions, the scheduler, and the WebSocket event stream. |
| GUI | `surfaces/gui/` (React + Tauri) | Desktop UI. Connects to the server over HTTP + WebSocket. In dev mode runs as a browser app against a local server. |
| TUI | `surfaces/tui/` (Textual) | Terminal UI. Drives `SessionManager` directly (in-process), no HTTP. |
| Scheduler loop | Started inside `SessionManager.__init__()` | `asyncio.Task` ticking at 30s inside the server's event loop. |

---

## `aisuite-js/` — The TypeScript Port

A TypeScript implementation of the aisuite Chat Completions API for browser and Node.js. Mirrors the Python `"provider:model"` routing pattern. Supports OpenAI, Anthropic, Mistral, Groq, and Deepgram (ASR).

**What it does not have:** the Agents API, `Runner`, `StateStore`, `MCPClient`, or toolkits. If you need stateful agent sessions from a web surface, you have three options: port those pieces to TypeScript, call the platform FastAPI server from the browser, or keep agent logic server-side.

**Entry point:** `aisuite-js/src/client.ts` → `Client` → `chat.completions.create()`.

---

## `tests/`

Mirrors the `aisuite/` package structure. Key sub-folders:

| Folder | What it tests |
|---|---|
| `tests/agents/` | `Runner`, `StateStore`, tool policies, continuation, tracing, async paths |
| `tests/client/` | `Client` API, manual tool calling, async client |
| `tests/mcp/` | MCP client integration (unit + e2e; e2e requires `npx`) |
| `tests/toolkits/` | File, git, shell sandboxing |
| `tests/providers/` | Per-provider normalization (mostly integration-marked) |

Pytest markers: `integration` (hits real APIs), `llm` (costs money — subset of integration), `mcp_server` (needs live MCP process). Run `pytest -m "not integration and not llm and not mcp_server"` for fast local tests.

---

## `cli/`

A standalone command-line tool (`aisuite-code-cli`) for running LLM queries against a local code repository. Separate `pyproject.toml`, not part of the `aisuite` or `coworker` packages. Useful for one-off code analysis tasks without standing up the full server.

---

## How a user message becomes an agent response

```
GUI sends message
        │
        ▼
FastAPI WebSocket handler (server/app.py)
        │
        ▼
SessionManager.send(session_id, text)
        │
        ▼
TurnEngine.run(text)  ← async generator
  │
  ├── context_provider()  ← injects live directory list + mode reminder
  ├── ProviderClient.stream()  ← runs in thread pool, yields text deltas → ASSISTANT_DELTA events
  ├── PermissionEngine.evaluate()  ← authorize each tool call
  ├── (optional) Approver callback  ← suspend for user approval → PERMISSION_REQUIRED event
  ├── Tool execution  ← concurrent (reads) or serial (writes/shell) in thread pool
  └── loop until no more tool calls
        │
        ▼
TURN_END event
        │
        ▼
WebSocket pushes all events to GUI
```

---

## What to read first

If you are working on the **library** (`aisuite/`): start with `client.py`, then `utils/tools.py`, then `agents/runner.py`.

If you are working on the **application** (`platform/`): start with `coworker/engine.py`, then `coworker/permissions.py`, then `coworker/agent.py` (`build_engine`).

If you are adding a **new LLM provider**: copy any file in `aisuite/providers/`, rename it `{name}_provider.py`, implement `chat_completions_create()`.

If you are adding a **new agent tool or integration**: implement it in `platform/coworker/tools/` or `platform/coworker/connectors/`, then register it in `build_engine()`.

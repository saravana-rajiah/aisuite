# Architecture Overview

> **Audience:** Chief Architect and senior engineers owning this codebase long-term.
> This document describes the *system*, not the files. Read it once; use the CLAUDE.md for day-to-day commands.

---

## 1. High-Level Architecture

The repository contains three co-located but independently versioned layers. They share a single git history but have separate package manifests, separate test suites, and a deliberate dependency direction: only `platform/` depends on `aisuite/`; `aisuite/` depends on nothing in `platform/`.

```mermaid
graph TD
    subgraph Desktop["Desktop Distribution"]
        GUI["React / Tauri GUI<br/><i>platform/surfaces/gui</i>"]
        TUI["Textual TUI<br/><i>platform/surfaces/tui</i>"]
    end

    subgraph Platform["platform/ — OpenCoworker App Layer"]
        Server["FastAPI Server<br/><i>coworker-server</i>"]
        SM["SessionManager"]
        TE["TurnEngine"]
        PE["PermissionEngine"]
        TR["ToolRegistry"]
    end

    subgraph Library["aisuite/ — LLM Abstraction Library"]
        Client["ai.Client<br/>Chat + Audio API"]
        Runner["Agent Runner"]
        Tools["Tool System"]
        Providers["22+ Provider Adapters"]
        MCP["MCP Client"]
        Tracing["Tracing / TraceSink"]
        StateStore["StateStore<br/>InMemory / File / Postgres"]
    end

    subgraph JS["aisuite-js/ — TypeScript Mirror"]
        JSClient["Client (TS)<br/>Chat API only"]
        JSProviders["OpenAI · Anthropic<br/>Mistral · Groq"]
    end

    GUI -- "HTTP + WebSocket" --> Server
    TUI -- "Python API" --> SM
    Server --> SM
    SM --> TE
    TE --> PE
    TE --> TR
    TE --> Client

    Client --> Providers
    Client --> MCP
    Runner --> Client
    Runner --> StateStore
    Runner --> Tracing

    GUI -. "aisuite-js (browser)" .-> JSClient
    JSClient --> JSProviders

    style Library fill:#dbeafe,stroke:#3b82f6
    style Platform fill:#dcfce7,stroke:#22c55e
    style Desktop fill:#fef9c3,stroke:#eab308
    style JS fill:#f3e8ff,stroke:#a855f7
```

**Dependency rule:** `aisuite/` is a pure library — no imports from `platform/`. `platform/` imports from `aisuite/` (toolkits, tracing, `ai.tool()`). The TypeScript layer `aisuite-js/` is independent.

---

## 2. Major Components

### 2.1 aisuite Library Components

| Component | Location | Responsibility |
|-----------|----------|----------------|
| `Client` | `aisuite/client.py` | Unified entry point: routes `"provider:model"` strings, manages the tool-calling loop when `max_turns` is set, handles MCP inline configs |
| `ProviderFactory` | `aisuite/provider.py` | Convention-based dynamic loader: discovers `{name}_provider.py` files in `aisuite/providers/`, instantiates on first use |
| `Tools` | `aisuite/utils/tools.py` | Infers OpenAI-format JSON schemas from Python function signatures + docstrings (via `docstring-parser` + Pydantic); validates args before execution; applies `ToolPolicy`; emits trace events |
| `Agent` / `Runner` | `aisuite/agents/` | Declarative agent definition + async multi-turn executor; manages `StateStore`, `ArtifactStore`, `TraceSink`, `ToolPolicy` |
| `StateStore` | `aisuite/agents/state_store.py` | Persist/resume run state across processes: `InMemoryStateStore`, `FileStateStore`, `PostgresStateStore` |
| `MCPClient` | `aisuite/mcp/client.py` | Wraps any MCP server (stdio or HTTP) as Python callables; lazy-connect, auto-reconnect |
| Toolkits | `aisuite/toolkits/` | Sandboxed prebuilt tool families: `files()`, `git()`, `shell()` |
| Tracing | `aisuite/tracing/` | Structured `TraceEvent` emission to pluggable `TraceSink`s (JSONL file, HTTP, DB); embedded viewer UI |

### 2.2 Platform (OpenCoworker) Components

| Component | Location | Responsibility |
|-----------|----------|----------------|
| `TurnEngine` | `platform/coworker/engine.py` | Core async event loop: streams model output, authorizes + executes tools, emits typed `Event` objects consumed by surfaces |
| `PermissionEngine` | `platform/coworker/permissions.py` | Mode-based access control gate: DISCUSS / PLAN / INTERACTIVE / AUTO / CUSTOM; path-scopes writes to `RootDir` list |
| `SessionManager` | `platform/coworker/server/manager.py` | Lifecycle of named sessions: create, resume, route events to the appropriate `TurnEngine` |
| `build_engine()` | `platform/coworker/agent.py` | Assembly function: wires agent tools + connectors + web + memory + skills + scheduling into a `ToolRegistry` and `TurnEngine` |
| `ToolRegistry` | `platform/coworker/tools/registry.py` | Named tool store: schema generation, `execute(name, args)`, metadata lookup |
| Platform `ProviderClient` | `platform/coworker/providers/` | **Separate** from aisuite providers — streaming-first, event-driven interface for OpenAI / Anthropic / Google |
| `MemoryStore` | `platform/coworker/memory/` | Durable cross-session facts: GLOBAL / WORKSPACE / SESSION scopes; SQLite backend |
| `SkillLoader` | `platform/coworker/skills/` | Discovers `SKILL.md` modules; progressive disclosure — catalog injected at start, full body loaded on demand |
| Connectors | `platform/coworker/connectors/` | Messaging platform adapters (Slack, Telegram, email) implementing `BasePlatformAdapter` |
| `Scheduler` | `platform/coworker/automation/scheduler.py` | Cron-based task executor with run-once-catch-up and skip-on-overlap policy |
| FastAPI App | `platform/coworker/server/app.py` | HTTP + WebSocket API surface: run lifecycle, approval responses, config |

---

## 3. Startup Flow

There are two startup paths: the desktop app (Tauri sidecar model) and standalone server (`coworker-server`).

### 3.1 Desktop App Startup

```mermaid
sequenceDiagram
    participant Tauri as Tauri Shell (Rust)
    participant Sidecar as Python Sidecar<br/>(PyInstaller bundle)
    participant Server as FastAPI Server
    participant GUI as React GUI

    Tauri->>Sidecar: spawn process<br/>COWORKER_EXIT_WITH_PARENT=1<br/>COWORKER_PARENT_PID={pid}
    Sidecar->>Sidecar: _exit_when_orphaned()<br/>watches parent PID in daemon thread
    Sidecar->>Server: uvicorn.run(create_app(manager))
    Server->>Server: SessionManager.__init__()<br/>load config, init MemoryStore<br/>init TaskStore, start Scheduler
    Server-->>Tauri: listening on 127.0.0.1:8765
    Tauri->>GUI: open WebView
    GUI->>Server: GET /config
    Server-->>GUI: {model, mode, roots}
    GUI->>Server: WS /sessions/{id}/events
    Note over Tauri,Sidecar: If Tauri dies → daemon thread calls os._exit(0)
```

**Orphan guard**: The server registers the parent PID at startup and polls it (POSIX: `kill(pid, 0)`) or blocks on a Windows handle (`WaitForSingleObject`). When the GUI process exits, the sidecar exits immediately — preventing leaked server processes across app restarts.

### 3.2 Standalone Server Startup

```mermaid
flowchart LR
    CLI["coworker-server --cwd /path --model gpt-4o --mode interactive"]
    --> Config["load_config(workspace)\nMerge: defaults → config.json → CLI args"]
    --> Build["build_app(workspace, model, mode)\n→ SessionManager(data_dir, model, mode)"]
    --> SM["SessionManager.__init__\n  MemoryStore (SQLite)\n  TaskStore (SQLite)\n  Scheduler.start()"]
    --> App["create_app(manager)\n  FastAPI routes\n  WebSocket handler"]
    --> Uvicorn["uvicorn.run(app, host, port)"]
```

---

## 4. Runtime Architecture

### 4.1 Process Model

```mermaid
graph LR
    subgraph TauriProcess["Tauri Process"]
        WebView["WebView\n(React UI)"]
    end

    subgraph PythonProcess["Python Sidecar Process"]
        Uvicorn["uvicorn\n(async event loop)"]
        SM2["SessionManager"]
        S1["Session A\nTurnEngine"]
        S2["Session B\nTurnEngine"]
        SchedLoop["Scheduler Loop\n(asyncio.Task)"]
    end

    subgraph ExternalProviders["External LLM APIs"]
        OAI["OpenAI"]
        Claude["Anthropic"]
        Gemini["Google"]
    end

    subgraph ExternalMCP["MCP Servers (subprocess)"]
        MCPProc["npx @mcp/filesystem\n(stdio)"]
    end

    WebView -- "HTTP + WS" --> Uvicorn
    Uvicorn --> SM2
    SM2 --> S1
    SM2 --> S2
    S1 -- "asyncio.to_thread" --> OAI
    S1 -- "asyncio.to_thread" --> Claude
    S2 -- "asyncio.to_thread" --> Gemini
    S1 -- "subprocess stdio" --> MCPProc
    SchedLoop --> SM2
```

**Key concurrency decisions:**
- The server runs a single asyncio event loop (uvicorn). All sessions share it.
- Provider streaming calls run in a thread pool via `asyncio.to_thread()` so the event loop stays responsive to incoming WebSocket messages (including interrupt signals) during long model calls.
- Tool execution: low-risk tools (`risk_level="low"`) within a single turn execute concurrently with `asyncio.gather()`; write and shell tools execute serially.

### 4.2 Event System

Every `TurnEngine.run()` call is an async generator that yields typed `Event` objects. Surfaces consume them via WebSocket or direct async iteration.

```mermaid
stateDiagram-v2
    [*] --> TURN_START
    TURN_START --> ASSISTANT_DELTA : streaming text chunks
    ASSISTANT_DELTA --> ASSISTANT_MESSAGE : turn complete
    ASSISTANT_MESSAGE --> TURN_END : no tool calls
    ASSISTANT_MESSAGE --> TOOL_PROPOSED : tool calls present
    TOOL_PROPOSED --> PERMISSION_REQUIRED : needs_user=True
    PERMISSION_REQUIRED --> TOOL_PROPOSED : approved/denied
    TOOL_PROPOSED --> TOOL_STARTED : authorized
    TOOL_STARTED --> TOOL_FINISHED : execution complete
    TOOL_FINISHED --> ASSISTANT_DELTA : next iteration
    ASSISTANT_MESSAGE --> PLAN_PROPOSED : propose_plan tool (plan mode)
    PLAN_PROPOSED --> ASSISTANT_DELTA : plan approved/rejected
    ASSISTANT_MESSAGE --> DIRECTORY_REQUESTED : request_directory tool
    TURN_END --> [*]
    INTERRUPTED --> [*] : cancel signal received
    ERROR --> [*] : provider failure
```

---

## 5. Workspace Management

### 5.1 RootDir Model

A session owns a **list of `RootDir` objects** — the directories it is permitted to access. This list is a **shared mutable reference** passed by pointer to:
- `PermissionEngine` (scopes write operations)
- File toolkit (resolves relative paths)
- `context_provider` lambda (tells the agent which directories exist each turn)

This means a folder grant granted mid-session (via the `request_directory` tool) is immediately visible to all three consumers without rebuilding the engine.

```mermaid
classDiagram
    class RootDir {
        +Path path
        +bool writable
        +str label
        +to_dict() dict
    }

    class PermissionEngine {
        +Path workspace_root
        +Mode mode
        +list[RootDir] roots
        +evaluate(tool, args, meta) Decision
        +allow_tool_for_session(name)
        +allow_command_for_session(cmd)
    }

    class TurnEngine {
        +list[RootDir] roots
        +context_provider() str
        +directory_requester() Awaitable
    }

    class FileToolkit {
        +list[RootDir] roots
        +resolve(path) Path
    }

    RootDir "1..*" --o PermissionEngine : shared list ref
    RootDir "1..*" --o TurnEngine : shared list ref
    RootDir "1..*" --o FileToolkit : shared list ref
```

### 5.2 Mode Enforcement and Path Scoping

```mermaid
flowchart TD
    Propose["Tool Call Proposed"] --> CheckMode{"Mode?"}
    CheckMode -- "DISCUSS or PLAN" --> CheckConsequential{"Consequential?\n(write/shell/requires_approval)"}
    CheckConsequential -- "yes" --> Deny["DENY\nread-only mode"]
    CheckConsequential -- "no" --> Allow["ALLOW\nlow risk"]

    CheckMode -- "AUTO" --> CheckPath{"Write tool?\nCheck path in\nwritable root"}
    CheckPath -- "outside roots" --> DenyPath["DENY\npath not in writable root"]
    CheckPath -- "inside roots" --> AllowAuto["ALLOW\nfull access"]

    CheckMode -- "INTERACTIVE / CUSTOM" --> CheckSession{"In session\nallowlist?"}
    CheckSession -- "yes" --> AllowSession["ALLOW\nsession memory"]
    CheckSession -- "no" --> CheckConfig{"CUSTOM mode +\nin auto_allow_tools?"}
    CheckConfig -- "yes" --> AllowConfig["ALLOW\nconfig-based"]
    CheckConfig -- "no" --> AskUser["needs_user=True\n→ emit PERMISSION_REQUIRED\n→ await Approver callback"]
    AskUser --> ApprovalOutcome{"Outcome?"}
    ApprovalOutcome -- "ONCE" --> AllowOnce["ALLOW once"]
    ApprovalOutcome -- "ALWAYS_TOOL" --> AllowAlways["ALLOW + add to\nsession_allow_tools"]
    ApprovalOutcome -- "ALWAYS_COMMAND" --> AllowCmd["ALLOW + add to\nsession_allow_commands"]
    ApprovalOutcome -- "DENY" --> DenyUser["DENY\nuser rejected"]
```

### 5.3 Plan Mode Workflow

Plan mode enforces a structured explore → propose → execute cycle across a single session:

```mermaid
sequenceDiagram
    participant User
    participant Engine as TurnEngine
    participant PE as PermissionEngine
    participant Agent as LLM Agent

    User->>Engine: turn (mode=PLAN)
    Engine->>Agent: messages + _PLAN_MODE_CONTEXT injected per turn
    Agent->>Agent: explore read-only (writes blocked)
    Agent->>Engine: call propose_plan(plan="...")
    Engine->>Engine: emit PLAN_PROPOSED event
    Engine->>User: show plan for review
    User->>Engine: approve (mode="interactive"|"auto")
    Engine->>PE: mode = Mode.INTERACTIVE (or AUTO)
    Engine->>Agent: result: {approved: true, mode: "interactive", note: "implement now"}
    Agent->>Agent: executes plan (writes now permitted)
    Note over PE: Mode flip is in-place — same session, same context
```

---

## 6. AI Request Lifecycle

This covers one complete user turn from input to final response, including the multi-iteration tool loop.

```mermaid
sequenceDiagram
    participant UI as GUI / TUI
    participant TE as TurnEngine
    participant PE as PermissionEngine
    participant TP as Thread Pool
    participant LLM as Provider (LLM API)
    participant Tool as Tool Function

    UI->>TE: run(user_input)
    TE->>TE: append user message
    TE->>TE: emit TURN_START

    loop up to max_iterations (default 12)
        TE->>TP: run_in_executor(provider.stream(...))
        Note over TP,LLM: blocking HTTP stream runs off event loop
        LLM-->>TP: token deltas
        TP-->>TE: emit ASSISTANT_DELTA per chunk
        LLM-->>TP: AssistantTurn (text + tool_calls)
        TP-->>TE: turn complete

        TE->>TE: append assistant message
        TE->>TE: emit ASSISTANT_MESSAGE

        alt No tool calls
            TE->>UI: emit TURN_END (status=completed)
        else Tool calls present
            loop Each tool call (sequential authorization)
                TE->>TE: emit TOOL_PROPOSED
                TE->>PE: evaluate(tool_name, args, metadata)
                alt needs_user=True
                    TE->>UI: emit PERMISSION_REQUIRED
                    UI->>TE: ApprovalOutcome (ONCE/ALWAYS/DENY)
                end
                alt Allowed
                    TE->>TE: add to cleared list
                end
            end

            par Concurrent (risk_level=low)
                TE->>TP: to_thread(execute tool A)
                TE->>TP: to_thread(execute tool B)
            end
            loop Serial (writes / shell / unannotated)
                TE->>TP: to_thread(execute tool C)
            end

            TP-->>TE: tool results
            TE->>TE: append tool result messages
            TE->>TE: emit TOOL_FINISHED per call
            TE->>TE: emit ITERATION_END
        end
    end
```

### 6.1 Context Injection

The `context_provider` lambda is called on every outbound model call and appends a `<system-context>` block to the last user message. This block is **never persisted** to `self.messages` — it is injected at send time only, ensuring:
- Plan-mode reminders reflect the live mode (mode can flip mid-session).
- The directory listing reflects the current `RootDir` list (folders can be added mid-session).
- Mid-thread system messages (unreliable across providers) are avoided.

---

## 7. AISuite Integration

### 7.1 Two Provider Abstractions (The Key Architectural Tension)

This codebase contains **two independent provider abstraction layers**:

| Aspect | `aisuite/providers/` | `platform/coworker/providers/` |
|--------|---------------------|-------------------------------|
| **Interface** | `Provider.chat_completions_create()` | `ProviderClient.stream()` → `Iterator[StreamChunk]` |
| **Design goal** | Request/response normalization | Streaming-first, event-driven UI |
| **Async** | Thread-wrapped by default; native async optional | Always streaming via thread + queue bridge |
| **Providers** | 22+ (OpenAI, Anthropic, Google, Groq, Mistral, …) | OpenAI, Anthropic, Google only |
| **Used by** | `ai.Client`, `Runner`, `aisuite-js` | `TurnEngine` (platform) |
| **Tool format** | OpenAI-format JSON dicts | Same, but parsed into `ToolCall` dataclasses |
| **Converges?** | Not yet — intentional divergence for streaming needs | Long-term: consider unifying |

### 7.2 aisuite Tool Calling Flow

```mermaid
flowchart LR
    subgraph Input["Tool Input Forms"]
        Fn["Python function\ndef my_tool(x: str)"]
        JSON["OpenAI JSON spec\n{type: function, function: {...}}"]
        MCPDict["MCP config dict\n{type: mcp, command: npx, ...}"]
    end

    subgraph Schema["Schema Generation (Tools class)"]
        Infer["Infer from signature\n+ docstring (Pydantic)"]
        Passthrough["Pass through as-is"]
        MCPDiscover["MCPClient.get_callable_tools()\n→ wrap as callables"]
    end

    subgraph Execution["Execution Loop"]
        Validate["Pydantic validation\nof model's args"]
        Policy["ToolPolicy.evaluate()\nAllowAll / DenyAll / AllowTools / RequireApproval"]
        Execute["fn(**validated_args)"]
        Truncate["Truncate result\n(max 2000 chars)"]
        Artifact["Large outputs →\nArtifactStore"]
    end

    Fn --> Infer --> Validate
    JSON --> Passthrough --> Validate
    MCPDict --> MCPDiscover --> Validate
    Validate --> Policy --> Execute --> Truncate --> Artifact
```

### 7.3 aisuite Runner State Machine

```mermaid
stateDiagram-v2
    [*] --> Initialized : Runner.run(agent, input)
    Initialized --> Running : emit run.started trace event
    Running --> ModelCall : client.chat.completions.create()
    ModelCall --> ToolLoop : tool_calls present
    ModelCall --> Completed : no tool calls
    ToolLoop --> PolicyCheck : Tools._prepare_tool_call()
    PolicyCheck --> Denied : policy rejects
    PolicyCheck --> Executing : policy allows
    Denied --> ModelCall : tool error appended to messages
    Executing --> ModelCall : tool result appended, loop continues
    Completed --> StateSaved : StateStore.save_state()
    StateSaved --> [*] : RunResult returned
    Running --> Failed : provider error
    Failed --> [*] : RunResult(status=failed)

    note right of StateSaved : thread_id + revision\noptimistic concurrency
```

---

## 8. File Management

### 8.1 File Tool Architecture

File operations in the platform layer are sandboxed by a shared `RootDir` list. The file toolkit enforces three invariants:
1. **Reads** must resolve inside any root (read-only or writable).
2. **Writes** must resolve inside a **writable** root — checked by both the file tool itself and by `PermissionEngine`.
3. **Relative paths** resolve against `root_list[0]` (the primary/scratch directory).

```mermaid
classDiagram
    class FileToolkit {
        +list_files(path, pattern) list
        +read_file(path, offset, limit) str
        +read_file_lines(path, start, end) str
        +search_files(pattern, path) list
        +write_file(path, content) str
        +create_file(path, content) str
        +append_file(path, content) str
        +delete_file(path) str
        +rename_file(src, dst) str
        -_resolve(path) Path
        -_check_readable(path)
        -_check_writable(path)
    }

    class RootDir {
        +Path path
        +bool writable
    }

    FileToolkit "1" --> "*" RootDir : sandboxed within
    FileToolkit ..> PermissionEngine : double-checked by
```

**Risk metadata** on each tool:
- Read tools: `risk_level="low"` — eligible for concurrent execution in the turn loop.
- Write tools: `risk_level="high"`, `requires_approval` where configured — always serial.

### 8.2 ArtifactStore (Large Output Handling)

When tool results exceed the message-size threshold (~2,000 chars), the `ArtifactStore` is used to keep the conversation context window lean:

```mermaid
flowchart LR
    Tool["Tool returns\nlarge output"] --> Size{"> threshold?"}
    Size -- "no" --> Message["Inline in messages"]
    Size -- "yes" --> Store["ArtifactStore.save()\n→ ArtifactRef {id, type, title}"]
    Store --> Ref["ArtifactRef injected\ninto message instead"]
    Ref --> Hydrate["UI resolves ref\nfor display/download"]
```

**Implementations:**
- `InMemoryArtifactStore` — testing
- `FileArtifactStore` — disk at `.aisuite/artifacts/{run_id}/{artifact_id}/`

---

## 9. Scheduling Architecture

### 9.1 Scheduler Design

```mermaid
flowchart TD
    subgraph Server Startup
        Start["SessionManager.__init__()"]
        --> TS["TaskStore(SQLite)\nload existing tasks"]
        --> Sched["Scheduler(store, runner)\n.start()"]
        --> Loop["asyncio.Task: _loop()"]
    end

    subgraph Scheduler Loop
        Loop --> Catchup["First tick: catchup\n(run all due tasks once)"]
        Catchup --> Sleep["asyncio.sleep(30s)"]
        Sleep --> Tick["_tick(): store.due()\nfor each task"]
        Tick --> Guard{"task.id in\n_running_ids?"}
        Guard -- "yes (skip-on-overlap)" --> Sleep
        Guard -- "no" --> Mark["add to _running_ids"]
        Mark --> Run["await runner(task, trigger)"]
        Run --> Advance["task.run_count++\ntask.last_run = now\nstore.save(task)"]
        Advance --> Discard["discard from _running_ids"]
        Discard --> Sleep
    end
```

**Two scheduler policies (hard-coded, by design):**
- **Run-once catch-up**: On startup, any task whose `next_run` is in the past fires exactly once. This recovers missed runs from downtime without stacking.
- **Skip-on-overlap**: If a task's previous run is still executing when the next fire time arrives, the new run is silently skipped. Prevents unbounded queuing for slow agents.

### 9.2 Task Lifecycle

```mermaid
stateDiagram-v2
    [*] --> Scheduled : Agent calls schedule_task(cron, prompt, workspace)
    Scheduled --> Due : croniter computes next_run
    Due --> Running : Scheduler._tick() fires runner()
    Running --> Completed : TaskRun(status=completed)
    Running --> Error : TaskRun(status=error)
    Completed --> Due : next_run recomputed
    Error --> Due : next_run recomputed (retry)
    Scheduled --> Deleted : Agent calls delete_task(id)
    Due --> Deleted
```

### 9.3 Task Execution Path

When the scheduler fires a task, the injected `runner` callback is called with a `ScheduledTask`. In the server context, this runner creates a headless `TurnEngine` session (no approver — tasks run non-interactively in AUTO mode) and returns a `TaskRun` record:

```mermaid
flowchart LR
    Scheduler --> Runner["runner(task, trigger)"]
    Runner --> BuildEngine["build_engine(\n  agent=cowork_agent,\n  workspace=task.workspace,\n  mode=AUTO,\n  approver=None\n)"]
    BuildEngine --> TE2["TurnEngine.run(task.prompt)"]
    TE2 --> TaskRun["TaskRun(status, output, started_at, ended_at)"]
    TaskRun --> Store["TaskStore.add_run()"]
```

---

## 10. Extension Points

### 10.1 Adding a New LLM Provider (aisuite)

Create one file. No core changes required:

```
aisuite/providers/myprovider_provider.py

class MyproviderProvider(Provider):
    def chat_completions_create(self, model, messages, **kwargs):
        ...
    # Optional: override for native async
    async def achat_completions_create(self, model, messages, **kwargs):
        ...
```

`ProviderFactory` discovers it by glob (`*_provider.py`) and instantiates via `MyproviderProvider(**config)`. Install with `pip install 'aisuite[myprovider]'` by adding to `pyproject.toml` extras.

### 10.2 Adding a New Tool Toolkit (aisuite)

Return a list of annotated callables:

```python
# aisuite/toolkits/mytools.py
import aisuite as ai

def my_toolkit(*, root: str) -> list:
    def read_thing(id: str) -> str:
        """Read a thing by ID."""
        ...

    return [
        ai.tool(read_thing, metadata=ai.ToolMetadata(
            category="mytools", risk_level="low"
        ))
    ]
```

### 10.3 Adding a Skill (platform)

Drop a folder in `~/.coworker/skills/` or `{workspace}/.coworker/skills/`:

```
my-skill/
  SKILL.md        ← YAML frontmatter (name, description) + markdown instructions
  resources/      ← optional scripts, templates, reference files
```

`SkillLoader` discovers it at session start. The agent sees only the catalog (name + description) until it calls `load_skill("my-skill")` — progressive disclosure keeps the context window lean.

### 10.4 Adding a Messaging Connector (platform)

Implement `BasePlatformAdapter`:

```python
# platform/coworker/connectors/myplatform.py

class MyPlatformAdapter(BasePlatformAdapter):
    platform = "myplatform"

    async def connect(self) -> bool: ...
    async def disconnect(self) -> None: ...
    async def send(self, chat_id, text, *, thread_id=None) -> SendResult: ...
```

Register it in `platform/coworker/connectors/__init__.py`. Credentials are stored via `SecretStore` (Keychain / Credential Manager / env var fallback).

### 10.5 Adding a StateStore Backend (aisuite)

Implement the `StateStore` protocol:

```python
class MyStateStore:
    def save_state(self, thread_id, state, *, revision=None, metadata=None) -> StoredRunState: ...
    def load_state(self, thread_id) -> Optional[StoredRunState]: ...
    def delete_state(self, thread_id) -> None: ...
```

Pass it to `Runner.run(..., state_store=MyStateStore())`. The optimistic concurrency contract: increment `revision` on save; raise `StateConflictError` if the stored revision doesn't match the expected one.

### 10.6 Adding a TraceSink (aisuite)

```python
class MyTraceSink:
    def emit(self, event: TraceEvent) -> None:
        # write to your observability backend
        ...
```

Pass it to `Runner.run(..., trace_sinks=[MyTraceSink()])`.

### 10.7 Custom Tool Policy (aisuite)

```python
class AuditPolicy:
    def evaluate(self, context: ToolPolicyContext) -> ToolPolicyDecision:
        log_audit(context)
        return ToolPolicyDecision(allowed=True)

Runner.run(agent, "...", tool_policy=AuditPolicy())
# Or as a callable:
Runner.run(agent, "...", tool_policy=lambda ctx: ToolPolicyDecision(allowed=True))
```

### 10.8 Extension Point Summary

```mermaid
graph TD
    subgraph aisuite["aisuite Extension Points"]
        EP1["New LLM Provider\naisuite/providers/{name}_provider.py"]
        EP2["New Toolkit\naisuite/toolkits/{name}.py"]
        EP3["New StateStore\nImplement StateStore protocol"]
        EP4["New TraceSink\nImplement TraceSink protocol"]
        EP5["Custom ToolPolicy\nCallable or class with .evaluate()"]
        EP6["MCP Server\nInline config dict or MCPClient"]
    end

    subgraph platform["platform Extension Points"]
        EP7["New Skill\n~/.coworker/skills/{name}/SKILL.md"]
        EP8["New Connector\nBasePlatformAdapter subclass"]
        EP9["New Platform Provider\nplatform/coworker/providers/"]
    end

    style aisuite fill:#dbeafe,stroke:#3b82f6
    style platform fill:#dcfce7,stroke:#22c55e
```

---

## Appendix: Key Architectural Decisions to Revisit

| Decision | Current State | Revisit When |
|----------|---------------|--------------|
| Dual provider abstraction | `aisuite/providers/` (22 providers) vs `platform/providers/` (3 providers, streaming) | When platform needs a 4th provider or aisuite gains native streaming |
| Single-user / single-machine | No RBAC, no tenant isolation, all data local | When multi-user or hosted deployment is required |
| Orphan-guard via PID polling | 1.5s poll on POSIX; handle wait on Windows | Known stable; revisit if extended background process support is needed |
| Memory scope model (GLOBAL / WORKSPACE / SESSION) | Coarse; no business-domain scopes | When domain entities (borrower, loan file) need isolated memory |
| Scheduler tick interval | 30 seconds | When sub-minute scheduled tasks are required |
| aisuite-js Agents API gap | Chat API only; no Runner, StateStore, or MCP client | When web surfaces need stateful agent sessions |

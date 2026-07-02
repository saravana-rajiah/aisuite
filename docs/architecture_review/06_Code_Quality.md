# Code Quality Review

> **Audience:** Chief Architect and senior engineers owning this codebase long-term.
> Documents large classes, duplicated logic, high coupling, poor abstractions, dead code, and technical debt with specific file references and prioritised remediation.

---

## At a Glance

| Category | Count | Highest-impact item |
|---|---|---|
| Large classes | 8 | `postgres_state_store.py` — 614 lines, 23 methods |
| Duplicated logic | 4 clusters | Platform providers re-implement aisuite providers (~1,200 lines) |
| High coupling | 3 god objects | `TurnEngine` takes 8+ injected callbacks, owns 6 concerns |
| Poor abstractions | 3 areas | No enforced `MessageConverter` protocol across 16 providers |
| Dead code | 3 items | `provider_interface.py` — file is a single TODO comment |
| Technical debt | 5 items | Pydantic v1/v2 four-branch shim in `deepgram_provider.py` |

---

## 1. Large Classes

### `aisuite/agents/postgres_state_store.py` — 614 lines, 23 methods

One class handles four distinct concerns: session CRUD, artifact storage, step indexing, and pagination. Schema DDL lives in `__init__()`, coupling schema evolution to object construction. Any `ALTER TABLE` that already ran silently succeeds on the first call and errors on the second.

**Decomposition:**
```
PostgresStateStore (614 lines)
├── SessionStore   — save / load / delete sessions
├── ArtifactStore  — create / update / get artifacts
└── StepIndex      — find_steps / get_step / paginate
```

---

### `aisuite/agents/runner.py` — 590 lines

Public API, internal state machine, artifact hydration, trace emission, and tool policy evaluation are all co-located. `_run_impl()` alone is 260+ lines. The module calls `get_configured_sinks()` at import time (line 10) — a side effect that fires before any test setup can intercept it. `ActiveRunContext` is a mutable global manipulated directly from inside the loop.

**Decomposition:**
```
Runner (590 lines)
├── AgentOrchestrator  — run(), _run_impl(), interrupt/steer
├── ArtifactManager    — hydrate_messages / dehydrate_messages
└── TraceCollector     — emission wiring, sink configuration
```

---

### `platform/coworker/engine.py` — 566 lines (`TurnEngine`)

Eight injected callbacks in `__init__()`. The `run()` method touches every one of them in a single execution path — streaming, tool authorization, tool execution, audit logging, and two special-case side channels (directory grants, plan proposals).

**Decomposition:**
```
TurnEngine (566 lines)
├── TurnOrchestrator    — run(), _astream(), loop control
├── ToolExecutor        — _execute_sync(), _record_result()
├── AuthorizationFlow   — _authorize(), _parallel_safe()
└── SpecialToolRouter   — _handle_directory_request(), _handle_plan_proposal()
```

---

### `aisuite/providers/google_provider.py` — 567 lines

LLM chat, audio transcription, and streaming transcription in one file. `GoogleAudio` is 237 lines with nested helpers for recognition config, streaming request generation, and response parsing. A disabled debug block (see §5) adds another 40 lines of noise.

**Fix:** Extract `GoogleAudio` into `aisuite/providers/google_audio.py`.

---

### `aisuite/providers/deepgram_provider.py` — 423 lines

ASR-only provider. Audio chunking, queue threading, resampling, stereo-to-mono conversion, and format detection are all inlined. None is reusable by other ASR providers (HuggingFace, OpenAI audio, Google).

**Fix:** Extract `AudioChunker` and `AudioResampler` into `aisuite/audio/utils.py`.

---

### `platform/coworker/conversations.py` — 350 lines (`ConversationStore`)

JSONL file I/O, SQLite DDL, and session query logic are co-located. DDL in `__init__()` means schema migrations are implicit. The threading lock protects file operations but also gates the SQLite path, serialising operations that could run independently.

**Fix:** Split into `_SessionFileStore` (JSONL append) and `_SessionIndex` (SQLite). Move DDL to an explicit migration function.

---

### `platform/coworker/providers/openai_provider.py` — 423 lines  
### `platform/coworker/providers/gemini_provider.py` — 424 lines

Addressed in §2 (duplication) but also individually oversized for provider shim files.

---

## 2. Duplicated Logic

### 2a. Message converters — 16 reimplementations (~1,600 lines)

Every provider that diverges from the OpenAI wire format ships its own `*MessageConverter`. Three patterns recur identically across `anthropic_provider.py`, `aws_provider.py`, `azure_provider.py`, `cohere_provider.py`, `google_provider.py`, and others:

**System message extraction** — structurally identical in every provider, but with subtle behavioural differences:

```python
# anthropic_provider.py:115 — mutates the caller's list
if messages and messages[0]["role"] == "system":
    system_message = messages[0]["content"]
    messages.pop(0)

# aws_provider.py:50 — rebinds local, does not mutate
if messages and messages[0]["role"] == "system":
    system_message = [{"text": messages[0]["content"]}]
    messages = messages[1:]
```

**Tool result conversion**, **assistant tool-call serialisation**, and **response normalisation** follow the same pattern: each provider re-encodes the same OpenAI-format input into its own wire format with no shared base.

**Fix:** Define a `MessageConverter` protocol in `aisuite/utils/message_converter.py`:

```python
class MessageConverter(Protocol):
    def extract_system(self, messages) -> tuple[str | None, list]: ...
    def convert_request(self, messages) -> list[dict]:              ...
    def convert_response(self, raw) -> ChatCompletionResponse:      ...
    def convert_tool_spec(self, tools) -> list[dict]:               ...
```

`OpenAICompliantMessageConverter` satisfies the protocol. Non-compatible providers override only the methods that differ.

---

### 2b. Platform providers vs aisuite providers (~1,200 lines)

`platform/coworker/providers/` reimplements OpenAI, Anthropic, and Gemini from scratch for streaming. These share ~80% of their request-building logic with the corresponding `aisuite/providers/` files.

| Platform file | aisuite equivalent | Shared scope |
|---|---|---|
| `platform/coworker/providers/openai_provider.py` (423 lines) | `aisuite/providers/openai_provider.py` (248 lines) | Request building, tool-call accumulation, error mapping |
| `platform/coworker/providers/anthropic_provider.py` (380 lines) | `aisuite/providers/anthropic_provider.py` (242 lines) | Message conversion, tool-use block parsing |
| `platform/coworker/providers/gemini_provider.py` (424 lines) | `aisuite/providers/google_provider.py` (567 lines) | Function call handling, safety filter mapping |

CLAUDE.md documents this as "intentional for the streaming architecture." The actual delta is narrow: platform providers yield `StreamChunk` events; aisuite providers return complete responses. Both call the same underlying SDK.

**Fix:** Add `achat_completions_stream()` to the aisuite `Provider` base. Platform providers become thin wrappers that yield `StreamChunk` from the aisuite streaming generator rather than re-implementing SDK calls.

---

### 2c. ASR response parsing (~300 lines)

`openai_provider.py`, `google_provider.py`, and `deepgram_provider.py` each implement `_parse_*_response()` with the same structure: iterate provider-specific word objects → build `Word` dataclasses → build `Segment` dataclasses → build `TranscriptionResult`.

**Fix:** Extract a `TranscriptionParser` base in `aisuite/audio/parser.py` with `_extract_words()` and `_extract_segments()` as overridable hooks.

---

### 2d. Error handling — identical bare `except` in every provider

```python
except Exception as e:
    raise LLMError(f"An error occurred: {e}")
```

No retry, no backoff, no distinction between transient network errors and permanent auth failures. Replicated across all 20+ providers.

**Fix:** Add `aisuite/utils/retry.py` with `@retryable(max_attempts=3, on=(RateLimitError, NetworkError))`. Apply at the `Provider` base class, not per-provider.

---

## 3. High Coupling

### `TurnEngine` (`platform/coworker/engine.py`)

Coupling map — every arrow is a live call inside `run()`:

```
TurnEngine.run()
  → ProviderClient.stream()          (model output)
  → ToolRegistry.get() / execute()   (tool dispatch)
  → PermissionEngine.check()         (authorization)
  → approver()                       (user consent callback)
  → context_provider()               (dynamic context injection)
  → directory_requester()            (directory grant/deny)
  → plan_approver()                  (plan approval)
  → audit_sink()                     (audit log)
```

The callbacks avoided hard imports but pushed the wiring cost onto `assemble_engine()` in `agent.py`, which now constructs an 8-argument object. Any change to permissions, execution, or audit requires navigating the same 566-line file.

---

### `Runner` (`aisuite/agents/runner.py`)

- `get_configured_sinks()` called at module import time (line 10) — fires before test setup
- `ActiveRunContext` is a process-global mutated directly from the loop body via `set_active_run_context` / `reset_active_run_context`
- `ArtifactStore` is instantiated inside `_run_impl()` rather than injected — unit tests cannot stub it without filesystem access
- Circular dependency path: `Runner` → `tracing/sinks.py` → `get_configured_sinks()` → reads process environment on import

---

### `ConversationStore` (`platform/coworker/conversations.py`)

A single threading lock (`self._lock`) serialises both JSONL file writes and SQLite queries. These two I/O paths have no shared state; the serialisation is accidental coupling left over from an earlier monolithic design.

---

## 4. Poor Abstractions

### Message converter — no enforced protocol

Sixteen converters, no shared contract:

| Converter | `convert_request` | `convert_response` | `convert_tool_spec` | Stateless? |
|---|---|---|---|---|
| `OpenAICompliantMessageConverter` | ✓ static | — | — | Yes |
| `AnthropicMessageConverter` | ✓ instance | ✓ | ✓ | Yes |
| `BedrockMessageConverter` | split into 4 static fns | ✓ | — | Yes |
| `AzureMessageConverter` | ✓ static | — | — | Yes |
| `GoogleMessageConverter` | ✓ instance | ✓ | — | Yes |

System message handling alone has four incompatible behaviours: Anthropic mutates the input list, AWS returns the system message separately, Azure ignores system messages, OpenAI passes them through. There is no documented contract; the behaviour is discovered by reading each provider.

---

### Audio base class — structural but not behavioural

`aisuite/audio/base.py` exists but is empty of any interface. Every ASR provider reimplements the same `Audio → Transcriptions` nesting:

```python
class OpenAIProvider:
    self.audio = OpenAIAudio(self.client)     # OpenAIAudio.Transcriptions

class DeepgramProvider:
    self.audio = DeepgramAudio(self.client)   # DeepgramAudio.Transcriptions
```

The base class is never used in `isinstance` checks, never enforces `transcriptions`, and provides no shared behaviour. It gives the appearance of an abstraction without enforcing one.

---

### `aisuite-js` API diverges from Python API

| Python | TypeScript |
|---|---|
| `chat.completions.create()` | `chatCompletion()` |
| `chat.completions.create(stream=True)` | `streamChatCompletion()` |
| `audio.transcriptions.create()` | `transcribe()` |

The divergence is undocumented. Consumers switching between the Python and TypeScript clients must re-learn the API. If the divergence is intentional (ergonomics for JS callers), it should be stated.

---

## 5. Dead Code

### `aisuite/framework/provider_interface.py`

The entire file:
```python
# TODO(rohit): Remove this. This interface is obsolete in favor of Provider.
```

Nothing in the codebase imports it (confirmed). **Delete it.**

---

### `ENABLE_DEBUG_MESSAGES` in `google_provider.py:29`

```python
ENABLE_DEBUG_MESSAGES = False
...
if ENABLE_DEBUG_MESSAGES:
    pprint.pprint(response)
```

The flag is never set to `True` anywhere in the codebase. The `import pprint` on line 16 exists solely for these disabled blocks. **Remove the flag, the conditionals, and the import.** Use `logging.debug()` if runtime inspection is needed.

---

### Unused `pprint` import in `google_provider.py:16`

Consequence of the above. CI runs `black --check` but not `ruff` or `flake8`, so unused imports go undetected. **Add `ruff check .` to CI.**

---

## 6. Technical Debt

### Pydantic version shim — `deepgram_provider.py:96–104`

```python
if hasattr(response, "model_dump"):
    response_dict = response.model_dump()   # Pydantic v2
elif hasattr(response, "to_dict"):
    response_dict = response.to_dict()      # legacy Deepgram SDK
elif hasattr(response, "dict"):
    response_dict = response.dict()         # Pydantic v1
else:
    response_dict = response                # raw dict fallback
```

Four branches indicates `pydantic` is not pinned in `pyproject.toml`. **Pin `pydantic>=2.0` and collapse to `response.model_dump()`.**

---

### Known broken behaviour documented only as TODO comments

These describe currently incorrect behaviour, not future aspirations:

| File | Line | What is wrong |
|---|---|---|
| `aisuite/providers/anthropic_provider.py` | 117 | System message extraction only handles the first message; multiple system messages in a thread are silently dropped |
| `aisuite/providers/google_provider.py` | 125 | Function call response parser reads only the first content block; multi-block responses silently lose content |
| `aisuite/client.py` | 462 | Provider initialisation is not thread-safe; concurrent first-use of two models under the same provider key can race |

Each should be a filed GitHub issue. Replace the inline TODO with `# see issue #NNN` so the code explains itself without hiding the gap.

---

### Parameter shadow in `google_provider.py:288`

```python
def chat_completions_create(self, model, messages, **kwargs):
    ...
    model = GenerativeModel(model, ...)   # 'model' string is now unreachable
```

If `model` is referenced after this line expecting the string (e.g., in a log statement), it produces a `GenerativeModel` repr. **Rename:** `generative_model = GenerativeModel(model, ...)`.

---

### Loose `any[]` types in `aisuite-js` adapters

`aisuite-js/src/providers/anthropic/adapters.ts:63`:
```typescript
const content: any[] = [];
```

Multiple adapter files use `any` where the shape is fully known from the Anthropic SDK's exported types. This suppresses the TypeScript checker over the message-building path. **Replace with `ContentBlock[]` / `ToolUseBlock[]`.**

---

### No `ruff` or `flake8` in CI

`poetry run black --check .` enforces formatting but does not catch unused imports, bare `except`, shadowed variables, or type issues. **Add `ruff check .` to the CI step alongside `black`.**

---

## Remediation Roadmap

### P0 — Eliminate duplication (unblocks P1)

| Task | Size | Impact |
|---|---|---|
| Define `MessageConverter` protocol; refactor 16 converters to inherit | L | Removes ~1,600 lines; all converter bugs fixed once |
| Add `achat_completions_stream()` to aisuite `Provider`; thin-wrap platform providers | L | Removes ~1,200 lines; platform gains future aisuite improvements automatically |
| Extract `TranscriptionParser` base; wire into OpenAI/Google/Deepgram ASR | M | Removes ~300 lines; consistent `TranscriptionResult` construction |
| Add `aisuite/utils/retry.py`; replace per-provider bare `except` | S | Centralises retry policy |

### P1 — Decompose large classes

| Task | Size | Impact |
|---|---|---|
| Split `PostgresStateStore` → `SessionStore` + `ArtifactStore` + `StepIndex` | M | DDL moved to migrations; each class independently testable |
| Split `Runner` → `AgentOrchestrator` + `ArtifactManager` + `TraceCollector` | L | Removes global `ActiveRunContext` side effect; `ArtifactStore` injectable |
| Split `TurnEngine` → `TurnOrchestrator` + `ToolExecutor` + `AuthorizationFlow` + `SpecialToolRouter` | L | Each class independently testable; reduces `assemble_engine()` argument count |
| Split `ConversationStore` → `SessionFileStore` + `SessionIndex` | M | Fixes DDL-in-constructor; decouples file and SQLite locks |

### P2 — Abstraction cleanup

| Task | Size | Impact |
|---|---|---|
| Enforce `Audio` base class (define `transcriptions` property with type) | S | Removes structural boilerplate per provider |
| Document or align `aisuite-js` API naming | S | Reduces cost for cross-language contributors |
| Fix `google_provider.py:288` parameter shadow | XS | Prevents silent misuse in future log statements |

### P3 — Technical debt (quick wins, measurable CI improvement)

| Task | Size | Impact |
|---|---|---|
| Delete `aisuite/framework/provider_interface.py` | XS | Removes misleading file |
| Remove `ENABLE_DEBUG_MESSAGES` and `pprint` import | XS | Cleans production code path |
| Pin `pydantic>=2.0`; collapse Deepgram shim | S | Removes 4-branch version hedge |
| Convert 3 open TODOs to GitHub issues; add `# see issue #NNN` | XS | Makes known gaps trackable |
| Add `ruff check .` to CI | XS | Catches unused imports, bare `except`, type issues going forward |
| Replace `any[]` in `aisuite-js` adapters with SDK types | S | Restores TypeScript coverage over message-building logic |

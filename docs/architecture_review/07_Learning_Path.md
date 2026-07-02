# Seven-Lesson Learning Path

> **Goal:** After completing all seven lessons you can confidently add a provider, write an agent, extend the platform with a skill, and reason about where new features belong.
>
> Each lesson is approximately one hour. Each builds on the previous one. You are expected to read actual source files — this document tells you what to read and what to look for, not what it says.

---

## Lesson 1 — The Chat API (the one abstraction everything else rests on)

**What you will understand:** The single interface that makes aisuite useful. After this lesson you can call any of 20+ providers without knowing their individual SDKs.

### Read these files in order

1. `aisuite/client.py` — read `Client.__init__()` and `chat.completions.create()`. Ignore `max_turns` for now.
2. `aisuite/provider.py` — read `Provider` and `ProviderFactory`. Notice how providers are discovered by glob — no registry file.
3. `aisuite/providers/openai_provider.py` — read the whole file. This is the reference implementation every other provider is compared against.
4. `aisuite/providers/anthropic_provider.py` — read the whole file. Note where it diverges from the OpenAI one and why.

### What to look for

- How does `Client` turn `"openai:gpt-4o"` into a call to `OpenAIProvider`? Trace the exact path.
- What does `ProviderFactory` do when it encounters a provider string it has never seen? Where is the discovery logic?
- `anthropic_provider.py` has a `MessageConverter`. `openai_provider.py` does not. Why?

### Exercise

Without running any code: given a message list that starts with `{"role": "system", ...}` followed by a user message, trace exactly what each provider does with the system message before it reaches the SDK. Write down the difference.

Then run it:
```bash
poetry install --all-extras --with dev,test
poetry run python -c "
import aisuite
c = aisuite.Client()
resp = c.chat.completions.create(
    model='openai:gpt-4o-mini',
    messages=[{'role': 'user', 'content': 'say hello'}]
)
print(resp.choices[0].message.content)
"
```

---

## Lesson 2 — How Providers Work (writing one from scratch)

**What you will understand:** The full provider contract. After this lesson you can add any new LLM provider in one file.

### Read these files in order

1. `aisuite/providers/mistral_provider.py` — the simplest non-trivial provider. OpenAI-compatible wire format, custom auth.
2. `aisuite/providers/aws_provider.py` — a non-compatible provider. Notice how `BedrockMessageConverter` handles the format gap. Trace the four conversion methods.
3. `aisuite/utils/message_converter.py` — the base class for OpenAI-compatible converters.
4. `aisuite/provider.py` again — now read `achat_completions_create()`. This is how async is handled for providers that don't have native async SDKs.

### What to look for

- `MistralProvider` is 30 lines. What does it *not* have to implement, and why?
- `BedrockMessageConverter` has four separate static methods. `AnthropicMessageConverter` has three instance methods. They solve the same problem differently. Which approach is easier to test?
- The `Provider` base class has `achat_completions_create()` that wraps the sync version in `asyncio.to_thread()`. What does this guarantee for callers that always use `await`?

### Exercise

Add a minimal stub provider. Create `aisuite/providers/stub_provider.py`:

```python
from aisuite.provider import Provider
from aisuite.framework import ChatCompletionResponse

class StubProvider(Provider):
    def chat_completions_create(self, model, messages, **kwargs):
        response = ChatCompletionResponse()
        response.choices[0].message.content = f"stub: {messages[-1]['content']}"
        return response
```

Call it with `model="stub:any-model"`. Confirm aisuite discovers it without any registration step. Then delete it.

---

## Lesson 3 — Tool Calling (functions the model can invoke)

**What you will understand:** How Python functions become model-callable tools, how tool results flow back, and how MCP servers plug in. After this lesson you can wire any Python function as a tool.

### Read these files in order

1. `aisuite/utils/tools.py` — read `Tools.__init__()` and `_process_tool()`. There are three input types; find all three.
2. `aisuite/utils/tools.py` again — read `aexecute_tool()` and `_get_function_schema()`. This is where docstrings become JSON schemas.
3. `aisuite/client.py` — now read the `max_turns` loop inside `chat.completions.create()`. This is the complete agentic loop for simple use cases.
4. `tests/test_tools.py` — read the tests to see what the schema inference does with different docstring styles.

### What to look for

- A function without a docstring can still be a tool. What schema does it get? What breaks if the docstring is missing the `Args:` section?
- The tool loop in `client.py` stops when there are no more tool calls in the response. What happens if the model calls a tool that does not exist in the registry?
- MCP tools (`{"type": "mcp", ...}`) are handled differently from Python functions. What is instantiated, and when?

### Exercise

Write a function tool with a Google-style docstring and pass it to `client.chat.completions.create()`:

```python
def add(a: int, b: int) -> int:
    """Add two numbers.

    Args:
        a: First number.
        b: Second number.

    Returns:
        The sum.
    """
    return a + b
```

Before calling: inspect `Tools([add]).tools` and read the JSON schema that was generated. Confirm the parameter descriptions came from the docstring. Then run it with `max_turns=3` and a prompt that should trigger the tool.

---

## Lesson 4 — The Agents API (stateful multi-turn execution)

**What you will understand:** The full Agents API — `Agent`, `Runner`, `StateStore`, `ToolPolicy`, tracing. After this lesson you can write a resumable agent that persists state across process restarts.

### Read these files in order

1. `aisuite/agents/types.py` — read every dataclass. `Agent`, `RunState`, `RunStep`, `ToolPolicy`, `TraceEvent`. Spend time here; these types define the vocabulary for everything that follows.
2. `aisuite/agents/runner.py` — read `run()`, `run_sync()`, and `_run_impl()`. Don't try to understand every line of `_run_impl()` yet — understand the loop structure: what happens each turn, how tool calls are dispatched, when the loop terminates.
3. `aisuite/agents/state_store.py` — read the base class and `FileStateStore`. Understand the `revision` field and what it prevents.
4. `aisuite/agents/runner.py` again — now read `continue_run()`. How does resuming a run differ from starting a new one?
5. `tests/agents/test_runner.py` — read any three tests. Notice how `InMemoryStateStore` is used to keep tests fast and hermetic.

### What to look for

- `Runner.run()` takes a `thread_id`. What happens if you call `run()` twice with the same `thread_id`? What prevents double-execution?
- `ToolPolicy` sits between the runner and tool execution. What are its three decision outcomes, and when would you use each?
- `TraceEvent` is emitted for every meaningful action. Where do these events go by default? Read `aisuite/tracing/sinks.py` to find out.

### Exercise

Run an agent locally with `FileStateStore`. After it completes one turn, kill the process. Call `Runner.run()` again with the same `thread_id` — confirm it picks up where it left off rather than starting over. Read the persisted JSON file in `.aisuite/` to understand what was saved.

```python
from aisuite.agents import Agent, Runner
from aisuite.agents.state_store import FileStateStore

agent = Agent(
    name="counter",
    model="openai:gpt-4o-mini",
    instructions="You are a helpful assistant.",
    tools=[],
)
result = Runner.run_sync(agent, "What is 2 + 2?", thread_id="lesson4")
print(result.output)
```

---

## Lesson 5 — The Platform Layer (streaming, events, and permissions)

**What you will understand:** How the platform layer wraps aisuite with streaming, a permission model, and a typed event system. After this lesson you know how the TUI and GUI receive and render model output.

### Read these files in order

1. `platform/coworker/providers/__init__.py` and `platform/coworker/providers/openai_provider.py` — focus on `stream()`. Compare with the aisuite `OpenAIProvider`. They call the same SDK; what is different about what they return?
2. `platform/coworker/events.py` — read every `Event` type. These are the atoms of the UI.
3. `platform/coworker/permissions.py` — read `Mode` and `PermissionEngine`. Understand the four modes and which tools each mode permits.
4. `platform/coworker/engine.py` — read `TurnEngine.__init__()`, `run()`, and `_handle_tool_calls()`. You do not need to read every method — understand the main loop structure.

### What to look for

- aisuite providers return a `ChatCompletionResponse`. Platform providers yield `StreamChunk` objects. What is the concrete difference in how the caller handles each?
- `DISCUSS` mode disables all tools. `INTERACTIVE` mode gates each tool call. Where exactly in `engine.py` does the mode get checked?
- `TurnEngine` emits `Event` objects. Who consumes them? Read `platform/surfaces/tui/app.py` for 50 lines to see how the TUI handles them.

### Exercise

Read a complete turn trace. Start the platform server against a scratch directory, run one prompt in the TUI, then read `.aisuite/events.jsonl`. Map each event back to the code that emitted it. Find the exact line in `engine.py` that emits the `TOOL_RESULT` event.

---

## Lesson 6 — Assembly and Skills (how the full app is wired)

**What you will understand:** How all the pieces — agent config, toolkits, skills, connectors, memory — are assembled into a running engine. After this lesson you can add a custom skill and understand every argument to `assemble_engine()`.

### Read these files in order

1. `platform/coworker/agent.py` — read `assemble_engine()` top to bottom. This is the integration point; everything from lessons 1–5 converges here.
2. `platform/coworker/tools.py` — read `ToolRegistry`. How does it store tools? What happens on name collision?
3. `platform/coworker/skills/loader.py` — read `SkillLoader`. How does it discover `.py` files in `~/.coworker/skills/`? What makes a function in a skill file eligible to become a tool?
4. `platform/coworker/connectors/` — read one connector (e.g. `browser.py` or `email.py`). Notice the `get_tools()` pattern. This is the plugin interface for external integrations.
5. `platform/coworker/conversations.py` — read `save()` and `load()`. Understand what is persisted and what is reconstructed in memory.

### What to look for

- `assemble_engine()` calls `ToolRegistry` with tools from four different sources. What are they? In what order are they added, and does the order matter?
- A skill file is a plain Python module. What constraints exist on the functions inside it? (Hint: look at what `SkillLoader` inspects on each function.)
- Connectors expose tools via `get_tools()`. A skill exposes tools by being a module with plain functions. What is the architectural difference, and when would you choose one over the other?

### Exercise

Write a custom skill. Create `~/.coworker/skills/mortgage.py`:

```python
def calculate_ltv(loan_amount: float, property_value: float) -> float:
    """Calculate loan-to-value ratio.

    Args:
        loan_amount: The loan amount in dollars.
        property_value: The appraised property value in dollars.

    Returns:
        LTV as a decimal (0.80 = 80%).
    """
    return loan_amount / property_value
```

Start the platform server and confirm the skill appears in the tool list. Call it from a prompt. Then read the trace event that shows the tool invocation and its result.

---

## Lesson 7 — Extending Confidently (shipping a complete feature)

**What you will understand:** Where new things belong. After this lesson you can independently add any of: a new LLM provider, a new agent capability, a new platform integration, or a new aisuite-js provider.

### Four extension patterns

Work through one of the four — whichever is closest to your next real task.

---

#### Pattern A: New LLM provider in aisuite

**Files to read first:**
- `guides/` — pick any provider guide
- `aisuite/providers/mistral_provider.py` — your template for OpenAI-compatible providers
- `aisuite/providers/aws_provider.py` — your template for non-compatible providers

**What to build:** Add `aisuite/providers/together_provider.py` (Together AI). It is OpenAI-compatible; use `mistral_provider.py` as your template. The only difference is the base URL and auth header. Verify with `poetry run pytest tests/ -k together`.

**Checklist before you consider it done:**
- [ ] `ProviderFactory` discovers it without any registration change
- [ ] `achat_completions_create()` works via the inherited `asyncio.to_thread()` wrapper
- [ ] Tool calling works (together supports OpenAI tool format)
- [ ] A unit test exists that mocks the HTTP call

---

#### Pattern B: New agent capability in aisuite

**Files to read first:**
- `aisuite/agents/types.py` — understand `ToolPolicy` and `TraceEvent`
- `aisuite/agents/runner.py` — understand where to hook in new behaviour

**What to build:** Add a `max_cost` parameter to `Runner.run()` that stops execution when estimated token spend exceeds a threshold. The token counts are already in `TraceEvent`; you only need to accumulate them and check.

**Checklist:**
- [ ] `RunResult` includes total tokens used
- [ ] Exceeding `max_cost` raises a specific exception (not a bare `RuntimeError`)
- [ ] State is saved before raising so the run can be inspected post-hoc
- [ ] Test covers the early-exit path

---

#### Pattern C: New platform integration (connector)

**Files to read first:**
- `platform/coworker/connectors/` — read one existing connector fully
- `platform/coworker/agent.py` — see how connectors are passed to `assemble_engine()`

**What to build:** Add a `CalendarConnector` that exposes two tools: `list_events(date: str)` and `create_event(title: str, date: str, time: str)`. Use stub implementations that return hardcoded data — the integration point, not the calendar API, is what you're practising.

**Checklist:**
- [ ] `get_tools()` returns callable wrappers with correct docstrings
- [ ] The connector can be passed to `assemble_engine()` without modifying `agent.py`
- [ ] Tools appear in the TUI tool list at runtime

---

#### Pattern D: New provider in aisuite-js

**Files to read first:**
- `aisuite-js/src/providers/openai/` — read `provider.ts` and `adapters.ts`
- `aisuite-js/src/client.ts` — understand how `"provider:model"` routing works in TypeScript

**What to build:** Add a Groq provider to aisuite-js. Groq is OpenAI-compatible; `adapters.ts` from the OpenAI provider can be reused almost unchanged. The only difference is the base URL.

**Checklist:**
- [ ] `npm test` passes with a mocked Groq response
- [ ] Streaming works (yield chunks, not a complete response)
- [ ] The provider is exported from `aisuite-js/src/index.ts`

---

### Cross-cutting questions — answer these before calling yourself done

These apply regardless of which pattern you picked:

1. **Dependency direction:** Does your change introduce any import from `platform/` into `aisuite/`? If so, it is wrong.
2. **Test marker:** Does your test call a real API? If yes, mark it `@pytest.mark.integration`. If it uses a mock, it should run with `poetry run pytest -m "not integration"`.
3. **Tool docstring:** If you added a tool, does its docstring have an `Args:` section? Without it, the model gets no parameter descriptions.
4. **Async:** If you added a sync provider method, does `achat_completions_create()` still work? It defaults to `asyncio.to_thread()` wrapping — you get it for free unless you broke the inheritance.
5. **Error surface:** What does your code do when the upstream API returns a 429 or a 500? Is the caller left with a `ChatCompletionResponse` or a traceable exception?

---

## Reading order summary

```
Lesson 1  aisuite/client.py
          aisuite/provider.py
          aisuite/providers/openai_provider.py
          aisuite/providers/anthropic_provider.py

Lesson 2  aisuite/providers/mistral_provider.py
          aisuite/providers/aws_provider.py
          aisuite/utils/message_converter.py

Lesson 3  aisuite/utils/tools.py
          aisuite/client.py  (max_turns loop)
          tests/test_tools.py

Lesson 4  aisuite/agents/types.py
          aisuite/agents/runner.py
          aisuite/agents/state_store.py
          aisuite/tracing/sinks.py
          tests/agents/test_runner.py

Lesson 5  platform/coworker/providers/openai_provider.py
          platform/coworker/events.py
          platform/coworker/permissions.py
          platform/coworker/engine.py
          platform/surfaces/tui/app.py  (50 lines)

Lesson 6  platform/coworker/agent.py
          platform/coworker/tools.py
          platform/coworker/skills/loader.py
          platform/coworker/connectors/  (one file)
          platform/coworker/conversations.py

Lesson 7  whichever extension pattern matches your next task
```

---

*Not a commitment to lend. All loans subject to underwriting approval.*

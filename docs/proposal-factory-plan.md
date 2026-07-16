# Proposal Factory — Architecture Design
*Revised: incorporates review feedback on orchestration, knowledge tools, skills, agent surface, and OpenCowork footprint.*

---

## 1. Recommended Architecture

**One sentence:** Proposal Factory is a pip-installable Python package that adds a lightweight orchestration engine, business-oriented knowledge tools, and consulting Skills on top of OpenCowork's existing runtime — without modifying OpenCowork beyond a single shim file.

### Layers

```
┌──────────────────────────────────────────────────────────────────────┐
│                        OpenCowork (unchanged)                        │
│                                                                      │
│  TurnEngine ─ ProviderRouter ─ ToolRegistry ─ PermissionEngine      │
│  SessionManager ─ MemoryStore ─ SkillLoader ─ ConversationStore     │
│  file tools, shell tools, web tools, todo tools                     │
│                                                                      │
│  ┌─────────────────────────────────────────────────┐                │
│  │  platform/coworker/agents/proposal.py           │                │
│  │  (thin shim — imports from proposal_factory)    │                │
│  └─────────────────────────────────────────────────┘                │
└──────────────────────────────────────────────────────────────────────┘
         │ imports
         ▼
┌──────────────────────────────────────────────────────────────────────┐
│                  ProposalFactory-CoreEngine (pip package)            │
│                                                                      │
│  ┌────────────────────────────────────────────────────────────┐     │
│  │  Proposal Agent (agent.py)                                 │     │
│  │  System prompt + single tool exposed to LLM:               │     │
│  │  run_proposal(request) → delegates to Proposal Engine      │     │
│  └────────────────────────────────────────────────────────────┘     │
│                                                                      │
│  ┌────────────────────────────────────────────────────────────┐     │
│  │  Proposal Engine (engine.py)                               │     │
│  │  Orchestration only — no LLM calls                         │     │
│  │  • Scopes request (single deliverable vs complete)         │     │
│  │  • Determines execution sequence + dependencies            │     │
│  │  • Tracks state across deliverables                        │     │
│  │  • Triggers validation after generation                    │     │
│  └────────────────────────────────────────────────────────────┘     │
│                                                                      │
│  ┌────────────────────────────────────────────────────────────┐     │
│  │  Knowledge Tools (tools/knowledge.py)                      │     │
│  │  Business-oriented — encapsulate repo structure            │     │
│  │  find_similar_proposals / recommend_case_studies /         │     │
│  │  load_template / search_lessons_learned /                  │     │
│  │  search_reference_architectures                            │     │
│  └────────────────────────────────────────────────────────────┘     │
│                                                                      │
│  ┌────────────────────────────────────────────────────────────┐     │
│  │  Consulting Skills (skills/)                               │     │
│  │  One SKILL.md per deliverable — methodology only           │     │
│  │  Purpose / Inputs / Reasoning / Deliverable / Validation   │     │
│  └────────────────────────────────────────────────────────────┘     │
└──────────────────────────────────────────────────────────────────────┘
         │ reads (knowledge root)      │ writes (engagement workspace)
         ▼                             ▼
┌──────────────────┐       ┌──────────────────────────────┐
│ ProposalFactory  │       │  ProposalFactory-Engagements  │
│ -Knowledge       │       │  (engagement workspace)       │
│ (read-only root) │       │  requirements/ analysis/      │
└──────────────────┘       │  deliverables/                │
                           └──────────────────────────────┘
```

### Division of Responsibility

| Layer | Responsible For | NOT Responsible For |
|---|---|---|
| **OpenCowork** | LLM calls, streaming, file I/O, memory, skills loading, sessions | Proposal logic of any kind |
| **Proposal Agent** | Recognising proposal requests, delegating to Proposal Factory | Scoping, sequencing, knowledge retrieval |
| **Proposal Engine** | Orchestration: scope, sequence, state, validation | LLM calls, streaming, provider selection |
| **Knowledge Tools** | Structured access to ProposalFactory-Knowledge | Physical repo layout, AI synthesis |
| **Consulting Skills** | Consulting methodology per deliverable | Tool invocation, repo search strategies |

---

## 2. OpenCowork Components Reused — and How

| Component | How Proposal Factory Uses It |
|---|---|
| `TurnEngine` | Streams every turn — unchanged |
| `ProviderRouter` | Routes to whichever LLM the user has configured — unchanged |
| `ToolRegistry` | Registers Proposal Factory tools via normal `register_all()` |
| `PermissionEngine` | Interactive approvals for write operations — unchanged |
| `SkillLoader` | Discovers consulting Skills from `.coworker/skills/` — unchanged |
| `skill_tools` (`load_skill`) | LLM loads a deliverable's methodology on demand |
| `todo_tools` (`todo_write`, `todo_update`) | ProposalEngine writes the deliverable plan; user sees live progress |
| `MemoryStore` | Remembers engagement context and client decisions across sessions |
| `build_engine` | Assembles the Proposal Agent — unchanged |
| `AgentContext` (`workspace`, `roots`) | `workspace` = engagement folder; Knowledge repo mounted as read-only root |
| File tools (`read_file`, `write_file`) | Reads requirements; deliverable tools write output |
| `web_search`, `web_fetch` | Research current technology where required |
| `SessionManager` | Manages engagement sessions — unchanged |
| `ConversationStore` | Persists the full generation conversation — unchanged |

---

## 3. New Proposal Factory Components

### A. Proposal Agent (`proposal_factory/agent.py`)

Defines `PROPOSAL_INSTRUCTIONS` — a consulting-grade system prompt — and `proposal_tool_factory(context)`. The agent is intentionally thin:

- Its system prompt instructs the LLM to recognise proposal-related requests and call `run_proposal`.
- Its tool factory registers exactly: `run_proposal` + the knowledge tools + `write_deliverable` + `validate_deliverable`.
- It does not expose Proposal Builders or internal Proposal Engine concepts to the LLM.

### B. Proposal Engine (`proposal_factory/engine.py`)

A pure-Python orchestration class. Responsibilities:

- **Scope determination**: Is the request for a single named deliverable, a subset, or a complete proposal?
- **Sequence planning**: Defines canonical order (Client Context → … → Executive Summary). Executive Summary is always last; earlier sections may depend on prior ones.
- **State tracking**: Maintains a per-run record of which deliverables have been generated, which are pending, which have failed validation.
- **Validation coordination**: After each deliverable is written, triggers the appropriate validation check before advancing.
- **Dependency enforcement**: Prevents Executive Summary from running until all body sections are complete.

The engine returns structured plans and state to the LLM. It does not call any LLM provider. It does not manage streaming or sessions.

### C. Knowledge Tools (`proposal_factory/tools/knowledge.py`)

Five business-oriented functions. Each encapsulates the physical layout of ProposalFactory-Knowledge behind a consulting-meaningful name:

| Tool | What it does |
|---|---|
| `find_similar_proposals(context)` | Searches prior proposals relevant to the engagement context |
| `recommend_case_studies(context)` | Returns case studies most applicable to the client situation |
| `load_template(deliverable_type)` | Returns the standard template for a named deliverable |
| `search_lessons_learned(context)` | Retrieves relevant lessons from past engagements |
| `search_reference_architectures(context)` | Finds technology reference architectures matching the solution space |

The LLM calls these by consulting intent. The folder conventions of ProposalFactory-Knowledge are invisible to the LLM.

### D. Deliverable Tools (`proposal_factory/tools/deliverables.py`)

- `write_deliverable(number, title, content)` — enforces `01 Client Context.md` naming, writes to `<workspace>/deliverables/`.
- `list_deliverables()` — returns which deliverables have been produced so far.
- `validate_deliverable(deliverable_type, path)` — checks completeness against the skill's validation checklist. Called by the engine after each write.

### E. Consulting Skills (`proposal_factory/skills/`)

One SKILL.md per deliverable. Each contains exactly:

```
Purpose:            What this deliverable achieves in the proposal.
Inputs:             What the agent should read before generating this section.
Reasoning Process:  How a consulting partner would think through this section.
Expected Deliverable: Structure and content expectations.
Validation Checklist: What must be present for the deliverable to be complete.
```

Skills do **not** contain: repository paths, tool invocation sequences, folder structures, or Python-specific guidance. They are consulting methodology, portable and stable across implementation changes.

---

## 4. OpenCowork Files That Change

**One file:**

`platform/coworker/agents/proposal.py` — becomes a thin shim:

```python
try:
    from proposal_factory.agent import PROPOSAL_INSTRUCTIONS, proposal_tool_factory
except ImportError:
    PROPOSAL_INSTRUCTIONS = "Proposal Factory is not installed."
    proposal_tool_factory = None

def proposal_agent() -> Agent:
    return Agent(
        name="proposal",
        title="Proposal",
        system_prompt=PROPOSAL_INSTRUCTIONS,
        needs_workspace=True,
        tool_factory=proposal_tool_factory,
    )
```

**One optional 1-line change:**

`platform/coworker/server/manager.py` line 217 — add `"proposal"` to the auto-provision block so a Proposal session without a pre-selected workspace gets a scratch directory rather than returning `None`. This is identical to how `"cowork"` is handled today.

No other OpenCowork files change.

---

## 5. Proposal Factory Files to Create

```
ProposalFactory-CoreEngine/
├── pyproject.toml                    # pip install -e .
├── install_skills.py                 # copies skills → workspace/.coworker/skills/
│
└── proposal_factory/
    ├── __init__.py
    │
    ├── agent.py                      # PROPOSAL_INSTRUCTIONS + proposal_tool_factory(context)
    │
    ├── engine.py                     # ProposalEngine — scope, sequence, state, validation
    │
    └── tools/
    │   ├── knowledge.py              # find_similar_proposals, recommend_case_studies,
    │   │                             # load_template, search_lessons_learned,
    │   │                             # search_reference_architectures
    │   └── deliverables.py           # write_deliverable, list_deliverables, validate_deliverable
    │
    └── skills/
        ├── client_context/SKILL.md
        ├── business_need/SKILL.md
        ├── current_state/SKILL.md
        ├── target_state/SKILL.md
        ├── solution/SKILL.md
        ├── assumptions/SKILL.md
        ├── risks/SKILL.md
        ├── wbs/SKILL.md
        ├── estimate/SKILL.md
        └── executive_summary/SKILL.md
```

ProposalFactory-Knowledge and ProposalFactory-Engagements require no code — they are folder structures.

---

## 6. Execution Flow: "Generate Client Context"

```
User: "Generate Client Context"
       │
       ▼
TurnEngine receives message → calls LLM with system_prompt + tools
       │
       ▼
LLM calls run_proposal("Generate Client Context")
       │
       ▼
ProposalEngine.run():
  - Identifies scope: single deliverable → "client_context"
  - No prior deliverables required as dependencies
  - Returns plan: { scope: "single", deliverable: "client_context",
                    next: "load_skill then gather knowledge then synthesize then write" }
  - Writes todo item via todo_write (user sees progress panel)
       │
       ▼
LLM calls load_skill("client_context")
  → SkillLoader returns SKILL.md (Purpose, Inputs, Reasoning, Deliverable, Validation)
       │
       ▼
LLM consults knowledge tools:
  → recommend_case_studies("cloud migration government sector")
  → load_template("client_context")
  → search_lessons_learned("client context stakeholder mapping")
       │
       ▼
LLM reads engagement requirements from workspace:
  → read_file("requirements/requirements.md")
  → read_file("requirements/client-brief.md")
       │
       ▼
LLM synthesises content using:
  skill methodology + knowledge results + requirements + system prompt
       │
       ▼
LLM calls write_deliverable(1, "Client Context", "<content>")
  → writes "01 Client Context.md" to workspace/deliverables/
       │
       ▼
ProposalEngine.validate_deliverable("client_context", path)
  → checks against SKILL.md Validation Checklist
  → returns pass/fail with specific gaps if any
       │
       ▼
TurnEngine streams summary to UI
Session saved to ConversationStore
```

---

## 7. Execution Flow: "Generate Complete Proposal"

```
User: "Build the complete proposal"
       │
       ▼
LLM calls run_proposal("Build the complete proposal")
       │
       ▼
ProposalEngine.run():
  - Identifies scope: complete (all 10 deliverables)
  - Determines sequence:
      01 Client Context
      02 Business Need
      03 Current State
      04 Target State
      05 Solution
      06 Assumptions
      07 Risks
      08 WBS
      09 Estimate
      10 Executive Summary  ← last: depends on all others
  - Writes full plan to todo_write (user sees 10 items in progress panel)
  - Returns: { scope: "complete", sequence: [...], current: "client_context" }
       │
       ▼
For each deliverable (sequential — each section may reference prior ones):
  │
  ├─ todo_update(section, "in_progress")
  ├─ load_skill(section)
  ├─ Knowledge tools (section-appropriate subset)
  ├─ read_file("deliverables/<prior sections>")  ← for coherence
  ├─ LLM synthesises content
  ├─ write_deliverable(n, title, content)
  ├─ ProposalEngine validates → pass or flag gaps
  └─ todo_update(section, "done")
       │
       ▼
Executive Summary generated last:
  - LLM reads all 9 prior deliverables
  - Synthesises executive narrative
  - write_deliverable(10, "Executive Summary", content)
  - Final validation
       │
       ▼
TurnEngine streams: "Complete proposal generated.
10 deliverables written to /deliverables/"
```

---

## 8. Phased Implementation Plan

### Phase 1 — Skeleton and Wire-up (1–2 days)

Create the `ProposalFactory-CoreEngine` package skeleton. Write `agent.py` with a full consulting-grade `PROPOSAL_INSTRUCTIONS`. Make OpenCowork's `proposal.py` import from it with a graceful fallback. At this point the Proposal surface loads, the agent can converse, and existing OpenCowork file tools are available. No engine, no knowledge tools yet.

**Exit criteria:** Proposal Agent appears in the sidebar; Claude responds as a consulting partner; `print` in `proposal_tool_factory` confirms agent is loading.

### Phase 2 — Proposal Engine (2–3 days)

Implement `engine.py`: scope determination, sequence definition, dependency map (executive summary last), state tracking, and the skeleton validation hook. Connect `run_proposal()` as the single tool registered in `proposal_tool_factory`. Engine returns structured plans to the LLM; LLM executes steps.

**Exit criteria:** "Generate Client Context" triggers `run_proposal`, engine correctly identifies scope and sequence, todo items appear in the progress panel.

### Phase 3 — Consulting Skills (3–5 days)

Write the 10 SKILL.md files. This is primarily prompt engineering. Each skill: Purpose, Inputs, Reasoning Process, Expected Deliverable, Validation Checklist. Test each skill independently — ask the LLM to generate that section using only file tools and the skill, confirm quality.

**Exit criteria:** Each of the 10 deliverables can be generated at acceptable quality using only skills + file tools.

### Phase 4 — Knowledge Tools (2–3 days)

Implement `knowledge.py` with the five business-oriented tools. Populate ProposalFactory-Knowledge with initial content: previous proposals, case studies, templates, lessons learned, reference architectures. Mount the Knowledge repo as a read-only root in the Proposal session.

**Exit criteria:** `recommend_case_studies()` returns relevant results; `load_template()` returns a usable template; generated deliverables demonstrably incorporate knowledge base content.

### Phase 5 — Deliverable Tools and Validation (1–2 days)

Implement `deliverables.py`: enforced naming convention, `list_deliverables`, `validate_deliverable` with checklist comparison. Wire validation call into the engine after each write.

**Exit criteria:** Deliverables are written with correct naming; validation catches missing sections; engine flags gaps before advancing.

### Phase 6 — End-to-End Engagement Test (2–3 days)

Run "Build the complete proposal" against a real engagement workspace. Evaluate coherence across all 10 sections. Tune skills and system prompt. Establish a baseline quality benchmark.

**Exit criteria:** A complete proposal passes the validation checklist and is reviewed by a consulting practitioner as meeting proposal standards.

---

## 9. Why This Architecture

### The Proposal Engine Restores the Right Boundary

Without an orchestration layer, the LLM decides which deliverables to generate, in what order, and when validation runs. LLMs are probabilistic — they may skip sections, change order, or skip validation. Consulting proposal generation has deterministic structure: sequence matters, executive summary depends on all other sections, every deliverable must pass a checklist. The ProposalEngine owns these rules. The LLM owns synthesis. The boundary is clean.

### Business-Oriented Knowledge Tools Decouple the LLM from the Repo

A generic `search_knowledge(query)` forces the LLM to understand the physical layout of ProposalFactory-Knowledge: where prior proposals live, where case studies are, what naming conventions were used. If the Knowledge repo is reorganised, the LLM's behaviour changes. Business-oriented tools (`find_similar_proposals`, `recommend_case_studies`) hide the layout. The LLM calls by intent. The tool handles location. The repo can evolve without changing the agent.

### Simplified Skills Are Durable

Skills that reference tool invocation logic or folder paths become stale as the implementation evolves. Skills that describe consulting methodology — how a senior partner thinks through a Business Need section — are durable. They remain valid whether the knowledge tools are backed by ripgrep today or a vector index tomorrow. The separation keeps skills reusable across future Proposal Factory versions.

### A Single Business-Facing Tool Preserves the Abstraction

Exposing Proposal Builders directly to the LLM would mean the LLM needs to know which builder handles which deliverable, how builders are named, and what parameters they accept. That is an implementation detail of Proposal Factory. The LLM should know only one thing: there is a `run_proposal` capability. ProposalEngine resolves everything else. This is the same pattern OpenCowork uses — the LLM calls `load_skill`, not a specific skill loader class.

### Minimal OpenCowork Footprint Enables Independent Evolution

One shim file and one optional line. Future OpenCowork upgrades require only that the `Agent` dataclass remains stable — it has been stable since the beginning. Proposal Factory can add new deliverable types, new knowledge tools, new validation rules, and new skills without touching OpenCowork at all. The two products can be maintained and versioned independently, with no merge conflicts between them.
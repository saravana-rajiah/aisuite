# Gap Analysis: Proposal Factory

> **Context:** This document compares the existing aisuite/OpenCoworker architecture against the requirements of an Enterprise Proposal AI Coworker called **Proposal Factory**. It assesses what exists, what is missing, and how large the gap is for each requirement area.
>
> **Gap classifications:**
> - **Configuration** — Achieved by changing settings, config values, or deployment parameters. No code change.
> - **Extension** — Achieved using existing extension points: new Skills, Connectors, Toolkits, Providers, or StateStore backends. No core changes.
> - **Refactoring** — The capability exists but is not shaped correctly for the requirement. Structural changes to existing components without changing the overall architecture.
> - **Missing Capability** — No corresponding system exists. Must be designed and built.
> - **Architecture Change** — The existing architectural assumption conflicts with the requirement. Addressing it changes how the system is structured, not just what it contains.

---

## Summary Table

| Requirement | Gap Classification | Severity |
|---|---|---|
| Proposal Methodology | Architecture Change | High |
| Knowledge Repository | Missing Capability + Architecture Change | High |
| Capabilities | Extension + Missing Capability | Medium |
| Skills | Extension + Refactoring | Low |
| Validation | Missing Capability | High |
| Learning | Missing Capability + Architecture Change | High |
| Proposal Generation | Extension + Missing Capability | Medium |
| Workspace Awareness | Extension + Refactoring | Medium |
| Long Running Tasks | Architecture Change | High |
| Human Review | Refactoring + Missing Capability | High |

---

## 1. Proposal Methodology

### What the requirement means
A structured, repeatable process for producing proposals: discovery → qualification → solution design → draft → internal review → client delivery. Each stage has defined inputs, outputs, and quality gates. The agent should know which stage it is in, what is required to advance, and refuse to skip stages.

### What exists today
The closest existing construct is **Plan Mode** — the agent explores, proposes a plan via `propose_plan`, awaits approval, then executes. This is a two-stage gate: explore and execute. The **Skills system** can encode methodology steps as markdown instructions. **AGENTS.md** can enforce conventions. The **PermissionEngine modes** (DISCUSS → PLAN → APPROVAL → AUTO) give a rough progression of access levels.

### The gap

The existing architecture has no concept of a **proposal lifecycle state machine**. Plan mode is a binary: pre-plan and post-plan. There is no way to enforce that a session has completed stage N before entering stage N+1, no way to attach required artifacts to stage transitions, and no way to persist methodology state across sessions (today, stage state lives only in the TurnEngine's in-memory message history and evaporates when the session closes).

More fundamentally: the TurnEngine is a **single-turn loop**, not a **workflow engine**. A proposal cycle spans days or weeks, involves multiple human touchpoints, and has branching paths (qualify → no-bid, or qualify → standard proposal vs. custom proposal). None of these constructs exist.

**Classification: Architecture Change**

The TurnEngine's turn-based model must be extended — or a layer above it must be built — that represents a proposal as a **durable, multi-session workflow** with an explicit stage model, transition conditions, and artifact requirements per stage. This is not achievable through configuration or extension of the existing engine.

---

## 2. Knowledge Repository

### What the requirement means
A centralized, queryable store of institutional knowledge: past proposals (won and lost), case studies, pricing history, approved legal language, competitor intelligence, product/service catalog, SME directories. The agent must retrieve relevant knowledge contextually — not by being told where to look, but by semantic relevance to the current proposal.

### What exists today
**MemoryStore** (`platform/coworker/memory/`) is a scoped key-value fact store (GLOBAL / WORKSPACE / SESSION) backed by SQLite. **File Toolkit** can read documents within workspace roots. **Web Search/Fetch** can retrieve external information. The **Artifacts system** stores outputs of agent runs.

### The gap

The MemoryStore is designed for **short durable facts** ("user prefers concise summaries"), not for a **corpus of structured institutional knowledge**. It has no semantic search, no ranking, no versioning, no citation tracking, and no ingestion pipeline. It stores individual text strings, not documents.

The file toolkit can read files but has no retrieval layer — the agent must know which file to read. For a knowledge repository, the agent must be able to ask "what have we said about cloud security compliance in past proposals?" and receive ranked, relevant excerpts. This requires a retrieval-augmented generation (RAG) architecture: document ingestion, chunking, embedding, vector storage, and similarity search — none of which exist.

There is also no **knowledge governance model**: no versioning, no approval workflow for adding knowledge, no audit trail for what knowledge informed a given proposal, no mechanism to mark knowledge as superseded.

**Classification: Missing Capability + Architecture Change**

The MemoryStore must be either replaced or supplemented by a document-oriented knowledge store with semantic retrieval. This is a new architectural component that integrates with the agent at the tool layer (a `search_knowledge(query)` tool) but requires a separate pipeline for knowledge ingestion and indexing.

---

## 3. Capabilities

### What the requirement means
A structured catalog of what the organization can deliver: service lines, solution offerings, pricing tiers, delivery methodologies, past performance references, certifications, key personnel. The agent draws on this catalog to match client requirements to available capabilities and to populate the capabilities section of a proposal.

### What exists today
No capabilities catalog exists. The Skills system (`SKILL.md` files) can hold descriptive content, but skills are **agent instructions** (how to behave), not **structured data** (what the organization offers). The file toolkit can read a static capabilities document, but it has no structure, no queryability, and no lifecycle management.

### The gap

A capabilities catalog is a **structured data asset** — it has hierarchy (service line → solution → sub-service), metadata (pricing, delivery time, prerequisites, certifications, references), and lifecycle (approved, deprecated, in-review). None of this structure exists anywhere in the codebase. The closest analogy is a knowledge repository entry, but the access pattern is different: capabilities are browsed and matched to requirements, not retrieved by semantic similarity.

The extension point that partially fits is the **Connectors system** — a capabilities data service could be exposed as a connector with tools like `search_capabilities(requirement)`, `get_capability_detail(id)`, `list_service_lines()`. This is buildable as an extension. However, the underlying data model, versioning, and the approval workflow for updating capabilities are missing.

**Classification: Extension + Missing Capability**

The connector and tool extension points can surface a capabilities catalog to the agent. The catalog itself — its data model, lifecycle management, and search interface — is a new capability to build. The agent-facing layer is an Extension; the catalog service is Missing Capability.

---

## 4. Skills

### What the requirement means
Reusable procedural knowledge that the agent can apply on demand: how to write an executive summary, how to structure a pricing table, how to perform competitive analysis, how to tailor a proposal for a specific vertical, how to check compliance against an RFP's requirements matrix.

### What exists today
The **Skills system** is a direct match in architecture. `SkillLoader` discovers `SKILL.md` files from `~/.coworker/skills/` and `{workspace}/.coworker/skills/`. The catalog (name + description) is injected at session start; full instructions are loaded on demand via `load_skill(name)`. The progressive disclosure model prevents context window bloat.

The structure is: YAML frontmatter (`name`, `description`, `allowed-tools`) + markdown body. Skills can reference resource files in their directory.

### The gap

The Skills architecture is sound for Proposal Factory. The gaps are operational rather than architectural:

1. **No proposal-domain skills exist yet.** The system is ready to hold them; none have been authored. This is a content gap, not an architecture gap.
2. **Skills are flat files with no versioning or approval workflow.** For enterprise use, skills need to go through a review process before they influence production proposals. There is no skill governance layer.
3. **Skills are scoped to a user's machine.** For an enterprise team, skills need to be shared and synchronized across all proposal authors. There is no centralized skill registry or distribution mechanism.
4. **Skills cannot be parameterized.** A skill for "write executive summary" cannot accept parameters like `vertical=healthcare` or `deal_size=enterprise`. Instructions are static markdown.

**Classification: Extension + Refactoring**

The architecture is correct. The skill content is an Extension (author proposal skills using existing SKILL.md format). The governance, distribution, and parameterization needs are a Refactoring of the SkillLoader and skill storage model.

---

## 5. Validation

### What the requirement means
Systematic quality checks on a proposal before it advances to the next stage or is delivered to the client: completeness (all required sections present), compliance (all RFP requirements addressed), pricing consistency (no conflicting numbers), legal language approval, win probability scoring, and competitive position assessment.

### What exists today
No validation system exists. The PermissionEngine's PLAN mode enforces a review gate, but it is **human-driven** — a human reads the plan and approves or rejects it. There is no automated quality check, no structured scoring, no completeness matrix, no compliance cross-reference.

The closest primitive is **Tool Policy** (`RequireApprovalPolicy`) in the aisuite layer, which can gate tool execution, but this is execution gating, not output quality validation.

### The gap

Validation for Proposal Factory is a multi-dimensional, structured process:

- **Structural validation**: Is every required section present? Does each section meet minimum length/content requirements?
- **Compliance validation**: Does every RFP requirement have a corresponding response?
- **Internal consistency**: Do the pricing tables in section 3 match the payment schedule in section 7?
- **Legal validation**: Does any language conflict with approved boilerplate?
- **Scoring**: Win probability, competitive position, pricing competitiveness.

None of these exist. Each requires either a specialized agent, a rule-based checker, or a structured comparison between the proposal and a requirements artifact. The output of validation must feed back into the proposal workflow as structured findings with severity levels and resolution tracking — not just a text response from the LLM.

**Classification: Missing Capability**

Validation is entirely absent. It requires: a validation stage in the proposal lifecycle (reinforcing the Architecture Change in #1), a validation artifact schema (structured findings), a set of validation agents or rule engines, and a resolution tracking mechanism. This is a substantial new capability.

---

## 6. Learning

### What the requirement means
The system improves over time from outcomes: won proposals teach the agent what works; lost proposals teach it what to avoid; client feedback refines skills; reviewer corrections improve future generations. Learning is both short-term (within a proposal cycle) and long-term (across proposal cycles and clients).

### What exists today
**MemoryStore** can persist facts across sessions with GLOBAL, WORKSPACE, and SESSION scopes. The agent can `remember()` corrections and preferences. This is the intended mechanism for learning from user feedback within a session. The `memory_update()` and `memory_forget()` tools allow the agent to maintain and revise its memory.

### The gap

The MemoryStore supports **incidental learning** from individual sessions. It does not support **systematic learning** from proposal outcomes:

1. **No outcome linkage.** There is no mechanism to connect a proposal artifact to its business outcome (won/lost, revenue, client satisfaction score). Without this linkage, the agent cannot learn from wins and losses.

2. **No feedback capture schema.** When a reviewer corrects a proposal section, that correction is a conversation message — it is not captured in a structured, reusable form. The agent may remember "client X prefers shorter executive summaries" but cannot generalize "in the healthcare vertical, executive summaries should lead with regulatory compliance."

3. **No learning pipeline.** Generalizing from individual outcomes to improved skills, knowledge, and methodology is a deliberate process, not an emergent one. It requires: outcome data collection, analysis (which patterns correlate with wins), synthesis (update skills/knowledge based on patterns), and validation (test updated skills before deploying to production). This pipeline does not exist.

4. **Memory scope is insufficient.** The GLOBAL/WORKSPACE/SESSION model does not have a CLIENT scope, a VERTICAL scope, or a DEAL_TYPE scope — all of which are natural learning dimensions for a proposal system.

**Classification: Missing Capability + Architecture Change**

The MemoryStore's scope model requires extension (a Refactoring) and a learning pipeline must be built (Missing Capability). More fundamentally, learning at scale requires a feedback architecture that connects proposal artifacts → outcomes → knowledge updates in a governed, auditable way. This is an architectural layer that does not exist.

---

## 7. Proposal Generation

### What the requirement means
Producing a formatted, deliverable proposal document: structured sections assembled in the correct order, formatted for the client (Word, PDF, PowerPoint), branded with the organization's visual identity, with tables, charts, and appendices. The generation may be iterative — draft, review, revise, finalize.

### What exists today
The **ArtifactStore** can hold large binary outputs. The **Shell Toolkit** can run command-line document generation tools. The **File Toolkit** can write text files. The **Browser Automation** connector can interact with web-based document tools. The TurnEngine can run multi-iteration loops.

For text-based output, the agent can write markdown or HTML to a file. For formatted documents, the only current path is through the shell — invoking pandoc, LaTeX, or a Python script.

### The gap

The existing capabilities can produce a document, but not in an **enterprise proposal context**:

1. **No template system.** Proposal documents follow branded templates with fixed structure, mandatory sections, and style rules. There is no template engine, no section scaffolding, no enforcement that required sections are populated before rendering.

2. **No structured document assembly.** A proposal is assembled from components: executive summary (from knowledge), capabilities matrix (from capabilities catalog), pricing table (from pricing rules), case studies (from knowledge repository), legal terms (from approved boilerplate). The agent today generates text in a single thread — there is no assembly model that tracks sections as independent artifacts that can be generated, reviewed, and locked independently.

3. **No format-specific output.** Word `.docx`, PDF with headers/footers/page numbers, PowerPoint slides — these are not text-file operations. They require document generation libraries or format-specific tools. No connector or toolkit for this exists.

4. **No version management.** A proposal goes through versions: v0.1 (first draft), v0.2 (after internal review), v1.0 (approved for client delivery). There is no versioning model for proposal artifacts.

**Classification: Extension + Missing Capability**

The Shell Toolkit and Connectors can be extended to invoke document generation tools (Extension). The template system, structured section assembly model, and version management are new capabilities to build (Missing Capability).

---

## 8. Workspace Awareness

### What the requirement means
The agent understands the context of the current deal: the RFP document, the client's industry and history, email threads, meeting notes, past proposals for this client, stakeholder information, the client's stated pain points, budget signals, and timeline. This context is available without the agent having to be explicitly told where to find each piece.

### What exists today
The **RootDir** model gives the agent access to a filesystem directory. The **File Toolkit** can list and read files within that directory. The **Email Connector** can read email threads. The **Web Search/Fetch** tool can retrieve external information. The **context_provider** lambda injects a live directory listing into each turn.

### The gap

The existing workspace model is **filesystem-centric and unstructured**. A proposal workspace requires a richer context model:

1. **No deal context schema.** The workspace today is a directory. A deal workspace needs structured metadata: client name, opportunity ID, RFP deadline, deal owner, deal stage, estimated value, CRM link. This schema does not exist.

2. **No CRM integration.** The most important source of deal context is the CRM (Salesforce, HubSpot, Microsoft Dynamics). There is no CRM connector. Account history, contact relationships, past opportunities, and deal notes all live in the CRM, not in a local filesystem.

3. **No RFP parsing pipeline.** RFPs arrive as PDFs or Word documents with complex structure: requirement sections, evaluation criteria, submission instructions, appendices. There is no pipeline to ingest an RFP, extract structured requirements, and make them available as queryable context.

4. **No stakeholder model.** Proposals require knowing who the decision-makers are, what their roles are, what matters to each of them. There is no stakeholder data model or integration with contact databases.

5. **Context injection is per-turn, ephemeral.** The `context_provider` lambda injects directory information into each turn's message but it is not persisted. Deal context that grows (new emails, new meeting notes) does not update the agent's understanding — the agent must re-read it each turn.

**Classification: Extension + Refactoring**

CRM and email integrations are buildable as Connectors (Extension). The deal context schema, RFP parsing pipeline, and structured workspace initialization are Refactoring of the RootDir/workspace model to support typed deal contexts rather than bare directories.

---

## 9. Long Running Tasks

### What the requirement means
A proposal cycle spans days to weeks. It includes human wait times (waiting for a reviewer, waiting for a client clarification, waiting for legal approval), system wait times (overnight knowledge retrieval, batch validation), and parallel work streams (multiple sections being drafted simultaneously by different agents). The system must support durable, resumable, multi-day workflows that survive process restarts.

### What exists today
The **Scheduler** (`automation/scheduler.py`) provides cron-based recurring tasks with 30-second tick resolution. The **StateStore** (File/Postgres backends) can persist and resume `RunState` across process restarts using a `thread_id`. The `max_iterations=150` cap limits the length of a single run. The **Runner** in aisuite supports continuation from a prior RunResult.

### The gap

The existing persistence model supports **session continuation** (resuming a conversation thread), not **workflow orchestration** (managing dependencies between tasks, human wait steps, and parallel execution streams):

1. **No durable workflow state.** A proposal workflow has states: `requirements_analysis` → `awaiting_sme_input` → `draft_generation` → `internal_review` → `revision` → `legal_review` → `approved` → `delivered`. The StateStore holds message history; it has no concept of workflow stage, required inputs per stage, or transition conditions.

2. **No human-in-the-loop wait semantics.** The Scheduler can fire a task at a time. It cannot express "fire this task when a human completes an external action" (e.g., "resume when the legal team approves"). There is no external trigger mechanism.

3. **No parallel workflow branches.** A proposal with 12 sections might have 4 sections being drafted simultaneously by different agents. The Scheduler fires single tasks sequentially. There is no fan-out model with a join step.

4. **No task dependency graph.** "Generate Section 3 (technical approach) before generating Section 4 (pricing), because pricing depends on the technical scope" cannot be expressed. The Scheduler is a flat list of independent tasks.

5. **The 30-second scheduler tick is too coarse for responsive workflows.** A human completes a review in seconds; waiting up to 30 seconds to detect completion and continue the workflow is not acceptable for interactive review steps.

6. **`max_iterations=150` per turn is a unit of progress, not a workflow duration limit.** A complex proposal may require thousands of LLM calls across many sessions. The per-turn cap creates unnatural breaks in complex generation tasks.

**Classification: Architecture Change**

The Scheduler + StateStore combination can handle simple recurring tasks. Proposal Factory requires a **workflow engine** — a system that can represent a directed acyclic graph of tasks, manage human wait steps, handle parallel branches with joins, and maintain workflow state independently of message history. This is a different class of system from a message-continuation store. It either requires a new component (a workflow orchestrator) or a substantial architectural extension of the existing scheduler and state store.

---

## 10. Human Review

### What the requirement means
Multiple stakeholders review proposals at different stages: the proposal owner reviews the first draft, a subject matter expert reviews the technical section, legal reviews the terms, a sales director approves the pricing, and an executive approves before client delivery. Each reviewer has a role-scoped view, can leave structured comments, can request changes, and their approval is a gate that the system enforces before advancing the proposal.

### What exists today
The **PermissionEngine PLAN mode** provides a single-gate approval workflow: the agent proposes a plan, the user approves or rejects it, the agent proceeds. This is exactly the right primitive at the session level. The **`propose_plan` tool** + `plan_approver` callback is a well-designed pattern.

The `ApprovalOutcome` enum (`ONCE`, `ALWAYS_TOOL`, `ALWAYS_COMMAND`, `DENY`) models the kinds of decisions a reviewer makes. The `PERMISSION_REQUIRED` event emitted to the GUI is how approval requests surface to users.

### The gap

The existing approval architecture is **single-reviewer, single-gate, single-session**. Enterprise proposal review requires:

1. **Multiple concurrent reviewers.** Legal, technical, and commercial review can happen in parallel. There is no model for multiple simultaneous approvers on a single artifact, and the system is fundamentally single-user today.

2. **Role-scoped review.** A legal reviewer should only see and approve the legal sections. A technical SME should only see the technical sections. There is no role model, no section-level ownership, and no filtered view of a proposal for a specific reviewer.

3. **Structured comments with resolution tracking.** Reviewers need to leave comments on specific sections ("this pricing is too aggressive — see competitor analysis in shared drive"). Comments need to be addressed and marked as resolved. The current model is approval/rejection at the whole-plan level, with free-text rejection feedback. There is no structured comment schema, no threading, no resolution state.

4. **Approval delegation and escalation.** If a reviewer is unavailable, approval must delegate to an alternate. If a deadline passes, an escalation path must trigger. The Scheduler could model time-based escalation, but approval delegation has no support.

5. **Persistent review state across sessions.** A reviewer may take three days to complete their review. The current `plan_approver` callback is an async function that suspends the `TurnEngine` — the engine must remain running (or the plan approval state is lost). For a multi-day review, this is not viable.

6. **Notification.** When a proposal reaches a review stage, reviewers must be notified. The Connectors system (email, Slack) can send messages, but there is no notification orchestration layer that knows who to notify, when, and how to route the notification based on the reviewer's role and preferences.

**Classification: Refactoring + Missing Capability**

The `propose_plan` / PLAN mode pattern is the correct foundation for single-step approval and should be retained and extended (Refactoring). The multi-reviewer, role-scoped, comment-tracking, delegation, and notification requirements are Missing Capability — they require new components that integrate with the existing approval event model but do not exist today.

---

## Architectural Implications

The ten requirements above converge on four systemic architectural needs that cut across multiple gaps:

```
┌─────────────────────────────────────────────────────────────────┐
│               Four Cross-Cutting Architecture Needs              │
├─────────────────┬───────────────────────────────────────────────┤
│ 1. Multi-User   │ Requirements: Human Review, Long Running,      │
│                 │ Learning, Validation.                           │
│                 │ Today: single-user, single-machine.             │
│                 │ Needed: user identity, roles, session          │
│                 │ ownership, shared state.                        │
├─────────────────┼───────────────────────────────────────────────┤
│ 2. Workflow     │ Requirements: Methodology, Long Running,       │
│    Engine       │ Human Review.                                   │
│                 │ Today: single-turn TurnEngine, cron Scheduler. │
│                 │ Needed: durable DAG with human-in-loop steps,  │
│                 │ parallel branches, event-driven triggers.       │
├─────────────────┼───────────────────────────────────────────────┤
│ 3. Knowledge    │ Requirements: Knowledge Repository,            │
│    Infrastructure│ Capabilities, Learning, Workspace Awareness.  │
│                 │ Today: MemoryStore (key-value facts), file      │
│                 │ toolkit (local reads).                          │
│                 │ Needed: vector retrieval, document ingestion,  │
│                 │ semantic search, knowledge lifecycle.           │
├─────────────────┼───────────────────────────────────────────────┤
│ 4. Proposal     │ Requirements: Methodology, Validation,         │
│    Domain Model │ Generation, Learning.                           │
│                 │ Today: no domain objects.                       │
│                 │ Needed: Proposal, Section, Requirement,        │
│                 │ ReviewComment, Outcome as first-class entities  │
│                 │ with lifecycle state and persistence.           │
└─────────────────┴───────────────────────────────────────────────┘
```

### What the existing architecture contributes

The existing codebase is not a poor foundation for Proposal Factory — it provides:

- **The LLM abstraction layer** (`aisuite/`) is stable and reusable as-is. Provider-agnostic routing, tool calling, streaming, and MCP integration are all production-ready.
- **The agent turn loop** (`TurnEngine`) is the right execution primitive for individual proposal tasks. It is not a workflow engine, but workflow steps execute *inside* TurnEngine runs.
- **The Skills system** is directly applicable for encoding proposal methodology steps and writing guidance.
- **The Connector framework** provides the integration surface for CRM, email, document systems, and collaboration tools.
- **The PLAN mode approval pattern** is the right foundation for human-in-the-loop review gates.
- **The Artifact and StateStore systems** provide the persistence primitives needed for durable proposal artifacts and session continuation.

### What must be built above the existing foundation

```
┌─────────────────────────────────────────────────────────────────┐
│                     Proposal Factory Layer                       │
├────────────────────┬────────────────────────────────────────────┤
│  Workflow Engine   │  Proposal lifecycle DAG, stage transitions, │
│                    │  human wait steps, parallel branches        │
├────────────────────┼────────────────────────────────────────────┤
│  Knowledge Layer   │  Vector store, RAG pipeline, document       │
│                    │  ingestion, semantic retrieval              │
├────────────────────┼────────────────────────────────────────────┤
│  Domain Model      │  Proposal, Section, Requirement,           │
│                    │  ReviewComment, Outcome entities            │
├────────────────────┼────────────────────────────────────────────┤
│  Multi-User Layer  │  Identity, roles, session ownership,        │
│                    │  notification routing                       │
├────────────────────┼────────────────────────────────────────────┤
│  Validation Engine │  Completeness, compliance, consistency      │
│                    │  scoring, structured findings               │
└────────────────────┴────────────────────────────────────────────┘
                              │ uses
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                  Existing aisuite / Platform Layer               │
│  TurnEngine · Skills · Connectors · ArtifactStore · StateStore  │
│  PermissionEngine · ToolRegistry · aisuite Client · MCP         │
└─────────────────────────────────────────────────────────────────┘
```

The existing system is Layer 2. Proposal Factory is Layer 1 built on top of it. No existing component needs to be discarded. The four Architecture Change findings (Methodology, Knowledge Repository, Learning, Long Running Tasks) each require a new Layer 1 component that uses, but does not replace, existing Layer 2 capabilities.

---

## Gap Classification Summary

| Gap | Classification | Builds on Existing | New Component Required |
|---|---|---|---|
| Proposal lifecycle state machine | Architecture Change | PermissionEngine modes, PLAN mode | Workflow engine |
| Structured knowledge store + RAG | Missing Capability + Architecture Change | MemoryStore (interface pattern), file toolkit | Vector store, ingestion pipeline |
| Capabilities catalog | Extension + Missing Capability | Connectors, tool extension points | Catalog data model + service |
| Proposal-domain skills | Extension | SkillLoader, SKILL.md format | Skill content (no architecture) |
| Skill governance + distribution | Refactoring | SkillLoader | Skill registry, approval workflow |
| Validation pipeline | Missing Capability | ArtifactStore, TurnEngine | Validation agents, findings schema |
| Outcome-linked learning | Missing Capability + Architecture Change | MemoryStore, MemoryItem | Feedback pipeline, outcome schema |
| Document generation + templates | Extension + Missing Capability | Shell toolkit, ArtifactStore | Template engine, format connectors |
| Structured document assembly | Missing Capability | ArtifactStore | Section model, version management |
| Deal context schema | Refactoring | RootDir, workspace model | Typed deal workspace |
| CRM integration | Extension | Connectors framework | CRM connector |
| RFP parsing pipeline | Missing Capability | File toolkit | Document parser, requirements extractor |
| Durable workflow with human wait | Architecture Change | Scheduler, StateStore | Workflow orchestrator |
| Multi-reviewer approval | Missing Capability | propose_plan / PLAN mode pattern | Review model, comment schema |
| Role-scoped review | Missing Capability | PermissionEngine (pattern) | Role model, multi-user identity |
| Review notifications | Missing Capability | Connectors (email/Slack) | Notification orchestration layer |
| Multi-user / identity | Architecture Change | — (none) | Identity, auth, session ownership |

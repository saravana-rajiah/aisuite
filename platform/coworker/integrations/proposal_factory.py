
"""ProposalFactoryAdapter — the sole boundary between OpenCowork and ProposalFactory-CoreEngine.

ProposalFactory (engine/ builders/ skills/ validation/) is a separate, independently versioned
project that lives outside this repo. OpenCowork is only its *runtime host*: this adapter must
never contain proposal business logic, retrieve knowledge, execute skills, run validation, or
construct prompts — all of that belongs entirely to ProposalFactory-CoreEngine. This module's
only job is to build the `ProposalContext` ProposalFactory expects, hand it to the one Proposal
Engine it currently exposes (`ProposalBuilderExecutor`, v1), and return whatever comes back
unmodified. If that job ever grows past "build context, call executor, return result," the growth
belongs in ProposalFactory, not here.

In-process by design: ProposalFactory-CoreEngine is treated as a plain Python library (installed
as a regular dependency, same as `aisuite` is sourced into `platform/` — see repo CLAUDE.md), not
a service. There is deliberately no HTTP, subprocess, IPC, or MCP transport here — that would be
infrastructure the v1 in-process requirement doesn't need, and would blur the "runtime host"
boundary into something that has to manage a second process's lifecycle.

ASSUMPTION — placeholder API pending ProposalFactory-CoreEngine's real interface:
This adapter assumes a `proposal_factory` package exposing:
    ProposalContext(workspace: Path, request: str)
    ProposalBuilderExecutor().execute(context: ProposalContext, builder_name: str) -> Any
Once ProposalFactory-CoreEngine's actual constructor/method signatures are available, update
`_executor_instance` and `run` below to match — callers (the Proposal Agent's tool) don't need to
change, since this file is the only place that imports `proposal_factory`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional


class ProposalFactoryAdapter:
    """Owns the single ProposalBuilderExecutor instance and the ProposalContext handoff.

    One instance per agent session (created in the Proposal Agent's tool factory), mirroring how
    `LocalExecutor` is scoped to a session in `agent.py`'s `build_engine` — no process-wide
    singleton, no cross-session state.
    """

    def __init__(self) -> None:
        self._executor: Optional[Any] = None

    def _executor_instance(self) -> Any:
        if self._executor is None:
            # Imported lazily: sessions that never touch the Proposal surface don't pay the
            # import cost, and OpenCowork can still start up if ProposalFactory-CoreEngine isn't
            # installed in a given environment.
            from proposal_factory import ProposalBuilderExecutor

            self._executor = ProposalBuilderExecutor()
        return self._executor

    def run(self, *, workspace: Path, builder_name: str, request: str) -> Any:
        """Build a ProposalContext and execute the named builder. Returns the result as-is."""
        from integrations import ProposalContext

        context = ProposalContext(workspace=workspace, request=request)
        return self._executor_instance().execute(context, builder_name)

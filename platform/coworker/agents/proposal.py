"""Proposal Agent — thin shim that delegates to ProposalFactory-CoreEngine.

All proposal business logic, skills, knowledge tools, and orchestration live in the
`proposal_factory` pip package (ProposalFactory-CoreEngine repo). This file is the
only change OpenCowork requires to host Proposal Factory.

Install ProposalFactory-CoreEngine into the platform venv before starting the server:
    pip install -e /path/to/proposal-factory/proposal-factory-core

Then install skills globally:
    python /path/to/proposal-factory/proposal-factory-core/install_skills.py
"""

from __future__ import annotations

from .base import Agent

try:
    from proposal_factory.agent import PROPOSAL_INSTRUCTIONS, proposal_tool_factory
    _loaded = True
except ImportError:
    PROPOSAL_INSTRUCTIONS = (
        "Proposal Factory is not installed. "
        "Run: pip install -e /path/to/proposal-factory/proposal-factory-core"
    )
    proposal_tool_factory = None  # type: ignore[assignment]
    _loaded = False


def proposal_agent() -> Agent:
    if not _loaded:
        import warnings
        warnings.warn(
            "proposal_factory package not found — Proposal Agent will have no tools.",
            stacklevel=2,
        )
    return Agent(
        name="proposal",
        title="Proposal",
        system_prompt=PROPOSAL_INSTRUCTIONS,
        needs_workspace=True,
        tool_factory=proposal_tool_factory,
    )

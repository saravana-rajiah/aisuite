"""Tests for Engagement Activation + `load_engagement_context` (coworker.tools.engagement).

Exercises the tool functions directly against temp-dir workspaces, the same way
test_code_tools.py exercises grep/git_log — no engine/agent wiring needed.
"""

from __future__ import annotations

from coworker.tools.engagement import engagement_tools


def _tools(workspace, roots=None):
    by_name = {t.__name__: t for t in engagement_tools(str(workspace), roots)}
    return by_name["activate_engagement"], by_name["load_engagement_context"]


def _seed_engagement(root, *, requirements_name="01_Requirements"):
    reqs = root / requirements_name
    reqs.mkdir(parents=True)
    (reqs / "requirements.md").write_text("# Requirements\nBuild X.", encoding="utf-8")
    (reqs / "client-brief.txt").write_text("Client: Acme Co.", encoding="utf-8")
    (reqs / "diagram.png").write_bytes(b"\x89PNG\r\n")  # unsupported, should be skipped


# -- load_engagement_context: implicit auto-detection (no explicit activation) -----------------


def test_workspace_itself_is_the_engagement(tmp_path):
    _seed_engagement(tmp_path)
    _, load_engagement_context = _tools(tmp_path)
    result = load_engagement_context()

    assert result["found"] is True
    assert result["engagement_name"] == tmp_path.name
    paths = {f["path"] for f in result["requirements"]}
    assert len(paths) == 2
    contents = {f["content"] for f in result["requirements"]}
    assert any("Build X" in c for c in contents)
    assert any("Acme Co" in c for c in contents)
    assert any("diagram.png" in w for w in result["warnings"])
    assert result["deliverables_dir_exists"] is False
    assert result["deliverables_dir_display"] == "/03_Deliverables"


def test_engagement_as_subdirectory_of_workspace(tmp_path):
    engagement = tmp_path / "Acme Corp Engagement"
    _seed_engagement(engagement)
    _, load_engagement_context = _tools(tmp_path)
    result = load_engagement_context()

    assert result["found"] is True
    assert result["engagement_name"] == "Acme Corp Engagement"
    assert len(result["requirements"]) == 2


def test_naming_variants_are_recognized(tmp_path):
    for name in ["01 Requirements", "01-requirements", "requirements"]:
        ws = tmp_path / name.replace(" ", "_")
        ws.mkdir()
        _seed_engagement(ws, requirements_name=name)
        _, load_engagement_context = _tools(ws)
        result = load_engagement_context()
        assert result["found"] is True, f"failed for requirements dir name {name!r}"


def test_discovery_and_meeting_notes_are_included(tmp_path):
    _seed_engagement(tmp_path)
    (tmp_path / "Discovery Notes.md").write_text("Stakeholders: CTO, CFO.", encoding="utf-8")
    meetings = tmp_path / "Meeting Notes"
    meetings.mkdir()
    (meetings / "kickoff.md").write_text("Kickoff on Monday.", encoding="utf-8")

    _, load_engagement_context = _tools(tmp_path)
    result = load_engagement_context()

    assert len(result["discovery_notes"]) == 1
    assert "Stakeholders" in result["discovery_notes"][0]["content"]
    assert len(result["meeting_notes"]) == 1
    assert "Kickoff" in result["meeting_notes"][0]["content"]


def test_no_engagement_found(tmp_path):
    (tmp_path / "random.txt").write_text("nothing to see here", encoding="utf-8")
    _, load_engagement_context = _tools(tmp_path)
    result = load_engagement_context()

    assert result["found"] is False
    assert result["requirements"] == []
    assert "No active engagement" in result["summary"]


def test_ambiguous_multiple_implicit_engagements_warns_and_picks_first(tmp_path):
    _seed_engagement(tmp_path / "Alpha Engagement")
    _seed_engagement(tmp_path / "Beta Engagement")
    _, load_engagement_context = _tools(tmp_path)
    result = load_engagement_context()

    assert result["found"] is True
    assert result["engagement_name"] == "Alpha Engagement"
    assert any("Multiple candidate engagement folders" in w for w in result["warnings"])


def test_requirements_dir_missing_is_distinguishable_from_no_engagement(tmp_path):
    # An engagement folder exists (it activates implicitly via the loose fallback only when it
    # directly contains a requirements dir) — simulate an explicitly activated engagement with
    # no requirements folder via activate_engagement instead, since implicit detection requires
    # a requirements dir to even be found.
    engagement = tmp_path / "Oaktree Insurance"
    engagement.mkdir()
    activate_engagement, load_engagement_context = _tools(tmp_path)
    activation = activate_engagement(name="Oaktree Insurance")
    assert activation["activated"] is True
    assert activation["requirements_dir"] is None
    assert any("no 01_Requirements folder" in w for w in activation["warnings"])

    result = load_engagement_context()
    assert result["found"] is True
    assert result["requirements_dir"] is None
    assert result["requirements"] == []


# -- activate_engagement -------------------------------------------------------------------


def test_activate_engagement_by_exact_name(tmp_path):
    _seed_engagement(tmp_path / "Oaktree Insurance")
    activate_engagement, load_engagement_context = _tools(tmp_path)

    result = activate_engagement(name="Oaktree Insurance")
    assert result["activated"] is True
    assert result["engagement_name"] == "Oaktree Insurance"
    assert result["requirements_dir_display"] == "/01_Requirements"
    assert result["deliverables_dir_display"] == "/03_Deliverables"
    assert result["deliverables_dir_exists"] is False

    # subsequent load_engagement_context uses the activated engagement, not auto-detection
    ctx = load_engagement_context()
    assert ctx["found"] is True
    assert ctx["engagement_name"] == "Oaktree Insurance"
    assert len(ctx["requirements"]) == 2


def test_activate_engagement_strips_command_prefix(tmp_path):
    _seed_engagement(tmp_path / "BM&G Legacy Modernization")
    activate_engagement, _ = _tools(tmp_path)

    result = activate_engagement(name="Use Engagement: BM&G Legacy Modernization")
    assert result["activated"] is True
    assert result["engagement_name"] == "BM&G Legacy Modernization"


def test_activate_engagement_no_match_lists_available(tmp_path):
    _seed_engagement(tmp_path / "Oaktree Insurance")
    activate_engagement, _ = _tools(tmp_path)

    result = activate_engagement(name="Nonexistent Client")
    assert result["activated"] is False
    assert "Oaktree Insurance" in result["available_engagements"]


def test_activate_engagement_switches_active_engagement(tmp_path):
    _seed_engagement(tmp_path / "Oaktree Insurance")
    _seed_engagement(tmp_path / "BM&G Legacy Modernization")
    activate_engagement, load_engagement_context = _tools(tmp_path)

    activate_engagement(name="Oaktree Insurance")
    assert load_engagement_context()["engagement_name"] == "Oaktree Insurance"

    activate_engagement(name="BM&G Legacy Modernization")
    assert load_engagement_context()["engagement_name"] == "BM&G Legacy Modernization"


def test_activate_engagement_searches_granted_roots(tmp_path):
    # The primary workspace is an empty scratch dir; the engagement lives in a separate folder
    # granted later via request_directory (modeled here as a second root passed to the toolkit).
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    engagements_repo = tmp_path / "ProposalFactory-Engagements"
    _seed_engagement(engagements_repo / "Oaktree Insurance")

    roots = [
        {"path": str(scratch), "writable": True},
        {"path": str(engagements_repo), "writable": False},
    ]
    activate_engagement, load_engagement_context = _tools(scratch, roots)

    result = activate_engagement(name="Oaktree Insurance")
    assert result["activated"] is True
    assert "ProposalFactory-Engagements" in result["workspace"]

    ctx = load_engagement_context()
    assert ctx["found"] is True
    assert len(ctx["requirements"]) == 2


def test_activate_engagement_empty_name(tmp_path):
    activate_engagement, _ = _tools(tmp_path)
    result = activate_engagement(name="Open Engagement:")
    assert result["activated"] is False
    assert "No engagement name" in result["error"]

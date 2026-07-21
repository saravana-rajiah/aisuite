"""Engagement Activation + `load_engagement_context` — Proposal Factory's business-oriented
context tools.

Two tools, sharing one piece of session state:

- `activate_engagement(name)` — resolves "Open Engagement: <name>" / "Use Engagement: <name>"
  to a folder and remembers it as the session's *active engagement*, so every later request
  defaults to it without the LLM re-discovering or re-asking for it.
- `load_engagement_context()` — reads the active engagement's requirements plus any discovery/
  meeting notes and hands back one consolidated, structured result, instead of the LLM chaining
  list_files/read_file/grep calls itself.

The active engagement is held in a small mutable object captured by both tool closures — the
same shared-by-reference pattern `AgentContext.roots` uses (see roots.py): the TurnEngine is
built once per session and reused for every turn (see agent.py's `build_engine`), so state set
by `activate_engagement` on turn 1 is still there on turn 10 without any engine rebuild.

Both tools are read-only and reuse the existing aisuite `FileToolkit` internally (multi-root
aware, so a folder granted mid-session via `request_directory` is searched/read too) rather than
reimplementing file access.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Optional

from aisuite.agents import ToolMetadata, tool
from aisuite.toolkits.files import DEFAULT_IGNORES, FileToolkit

_SUPPORTED_EXTENSIONS = {".md", ".markdown", ".txt", ".rst", ".csv", ".json", ".yml", ".yaml"}
_MAX_FILES_PER_SECTION = 25
_DISCOVERY_MARKERS = ("discovery",)
_MEETING_MARKERS = ("meeting",)
_NUM_PREFIX_RE = re.compile(r"^\d+\s*[-_. ]*")
_COMMAND_PREFIX_RE = re.compile(
    r"^\s*(open|use|activate|switch\s+to)\s+engagement\s*[:\-]?\s*", re.IGNORECASE
)
_ENGAGEMENT_SEARCH_MAX_DEPTH = 3
_ENGAGEMENT_SEARCH_CAP = 2000


def _normalize(name: str) -> str:
    s = name.strip().lower().replace("_", " ").replace("-", " ")
    s = _NUM_PREFIX_RE.sub("", s)
    return " ".join(s.split())


def _strip_command_prefix(name: str) -> str:
    return _COMMAND_PREFIX_RE.sub("", name or "").strip().strip("\"'")


def _find_named_dir(parent: Path, target: str) -> Optional[Path]:
    try:
        children = sorted(d for d in parent.iterdir() if d.is_dir())
    except OSError:
        return None
    for d in children:
        if _normalize(d.name) == target:
            return d
    return None


def _root_paths(workspace: Path, roots: Optional[list]) -> list[Path]:
    """Duck-types the same RootDir | dict | str/Path shapes aisuite's FileToolkit accepts."""
    out: list[Path] = []
    seen: set[Path] = set()
    for r in roots or []:
        if isinstance(r, dict):
            p = r.get("path")
        elif isinstance(r, (str, Path)):
            p = r
        else:
            p = getattr(r, "path", None)
        if not p:
            continue
        rp = Path(p).expanduser().resolve()
        if rp not in seen:
            seen.add(rp)
            out.append(rp)
    return out or [workspace]


def _iter_dirs(base: Path, max_depth: int, budget: list[int]) -> Iterator[Path]:
    """Yield `base` and its subdirectories up to `max_depth` levels down, skipping ignored/dot
    dirs, capped by a shared `budget` counter so a huge tree can't blow up a search."""
    if budget[0] <= 0 or not base.is_dir():
        return
    budget[0] -= 1
    yield base
    if max_depth <= 0:
        return
    try:
        children = sorted(d for d in base.iterdir() if d.is_dir() and not d.name.startswith("."))
    except OSError:
        return
    for d in children:
        if d.name in DEFAULT_IGNORES:
            continue
        yield from _iter_dirs(d, max_depth - 1, budget)


def _search_engagements(name: str, search_roots: list[Path]) -> tuple[list[Path], list[Path]]:
    """Find directories whose name matches `name`, across all `search_roots`. Returns
    (exact_matches, partial_matches); callers should prefer exact over partial."""
    target = _normalize(name)
    if not target:
        return [], []
    budget = [_ENGAGEMENT_SEARCH_CAP]
    seen: set[Path] = set()
    exact: list[Path] = []
    partial: list[Path] = []
    for root in search_roots:
        for d in _iter_dirs(root, _ENGAGEMENT_SEARCH_MAX_DEPTH, budget):
            if d in seen:
                continue
            seen.add(d)
            norm = _normalize(d.name)
            if not norm:
                continue
            if norm == target:
                exact.append(d)
            elif target in norm or norm in target:
                partial.append(d)
    return exact, partial


def _known_engagements(search_roots: list[Path], limit: int = 20) -> list[str]:
    """Best-effort list of folder names that look like engagements (have a requirements-style
    subfolder), used to help the LLM/user when `activate_engagement` finds no name match."""
    budget = [_ENGAGEMENT_SEARCH_CAP]
    found: list[str] = []
    seen: set[str] = set()
    for root in search_roots:
        for d in _iter_dirs(root, _ENGAGEMENT_SEARCH_MAX_DEPTH, budget):
            if _find_named_dir(d, "requirements") is None:
                continue
            if d.name in seen:
                continue
            seen.add(d.name)
            found.append(d.name)
            if len(found) >= limit:
                return found
    return found


def _engagement_summary(root: Path) -> dict[str, Any]:
    """Requirements + deliverables folders for an engagement root, resolved fresh (not cached,
    since a deliverable write can create the deliverables folder mid-session)."""
    req_dir = _find_named_dir(root, "requirements")
    del_dir = _find_named_dir(root, "deliverables")
    del_exists = del_dir is not None
    if del_dir is None:
        del_dir = root / "03_Deliverables"
    return {
        "requirements_dir": req_dir,
        "requirements_dir_display": f"/{req_dir.name}" if req_dir else None,
        "deliverables_dir": del_dir,
        "deliverables_dir_display": f"/{del_dir.name}",
        "deliverables_dir_exists": del_exists,
    }


def _locate_implicit(workspace: Path) -> tuple[Optional[Path], list[str]]:
    """No engagement has been explicitly activated: fall back to auto-detecting one within the
    session's primary workspace only (the workspace itself, or one of its direct children)."""
    warnings: list[str] = []
    if not workspace.is_dir():
        return None, warnings
    if _find_named_dir(workspace, "requirements") is not None:
        return workspace, warnings
    try:
        subdirs = sorted(
            d for d in workspace.iterdir() if d.is_dir() and not d.name.startswith(".")
        )
    except OSError:
        subdirs = []
    candidates = [d for d in subdirs if _find_named_dir(d, "requirements") is not None]
    if not candidates:
        return None, warnings
    if len(candidates) > 1:
        names = ", ".join(c.name for c in candidates)
        warnings.append(
            f"Multiple candidate engagement folders found ({names}); used "
            f"'{candidates[0].name}'. Confirm with the user if this is the wrong one."
        )
    return candidates[0], warnings


def _read_files_in(
    dir_path: Path, *, toolkit: FileToolkit, limit: int = _MAX_FILES_PER_SECTION
) -> tuple[list[dict[str, Any]], list[str]]:
    try:
        names = toolkit.list_files(path=str(dir_path), pattern="*", recursive=True, max_results=1000)
    except (ValueError, PermissionError, OSError):
        return [], []

    read_out: list[dict[str, Any]] = []
    skipped: list[str] = []
    for rel in names:
        if Path(rel).suffix.lower() not in _SUPPORTED_EXTENSIONS:
            skipped.append(rel)
            continue
        if len(read_out) >= limit:
            skipped.append(f"{rel} (over the {limit}-file cap for this section)")
            continue
        try:
            content = toolkit.read_file(rel)
        except (ValueError, PermissionError, OSError) as exc:
            skipped.append(f"{rel} ({exc})")
            continue
        read_out.append({"path": rel, "content": content})
    return read_out, skipped


def _read_marker_matches(
    engagement_root: Path,
    markers: tuple[str, ...],
    *,
    toolkit: FileToolkit,
    exclude: Optional[Path],
) -> tuple[list[dict[str, Any]], list[str]]:
    try:
        entries = sorted(engagement_root.iterdir())
    except OSError:
        return [], []

    matches: list[dict[str, Any]] = []
    skipped: list[str] = []
    for entry in entries:
        if exclude is not None and entry == exclude:
            continue
        stem = entry.name if entry.is_dir() else entry.stem
        if not any(m in _normalize(stem) for m in markers):
            continue
        if entry.is_dir():
            files, sk = _read_files_in(entry, toolkit=toolkit)
            matches.extend(files)
            skipped.extend(sk)
        elif entry.is_file():
            if entry.suffix.lower() not in _SUPPORTED_EXTENSIONS:
                skipped.append(str(entry))
                continue
            try:
                content = toolkit.read_file(str(entry))
            except (ValueError, PermissionError, OSError) as exc:
                skipped.append(f"{entry} ({exc})")
                continue
            matches.append({"path": str(entry), "content": content})
    return matches, skipped


@dataclass
class ActiveEngagement:
    name: str
    root: Path


class EngagementSession:
    """Holds the Proposal agent's active engagement for the life of the session."""

    def __init__(self) -> None:
        self.active: Optional[ActiveEngagement] = None


def engagement_tools(workspace: str, roots: Optional[list] = None) -> list:
    ws = Path(workspace).resolve()
    file_kwargs: dict[str, Any] = {"roots": roots} if roots else {"root": ws}
    toolkit = FileToolkit(
        allow_write=False,
        max_read_bytes=200_000,
        max_search_bytes=1_000_000,
        ignore=list(DEFAULT_IGNORES),
        **file_kwargs,
    )
    state = EngagementSession()

    def activate_engagement(name: str) -> dict[str, Any]:
        """Activate an engagement by name so every proposal request in this session uses it
        automatically, until a different engagement is activated. Use this for requests like
        "Open Engagement: Oaktree Insurance" or "Use Engagement: BM&G Legacy Modernization" —
        pass just the name; a leading "Open/Use/Activate Engagement:" phrase is stripped
        automatically if present. Searches the folders available to this session for a
        matching engagement. Call load_engagement_context right after to read its requirements.

        Args:
            name: The engagement's name, e.g. "Oaktree Insurance".
        """
        clean = _strip_command_prefix(name)
        if not clean:
            return {"activated": False, "error": "No engagement name given."}

        search_roots = _root_paths(ws, roots)
        exact, partial = _search_engagements(clean, search_roots)
        candidates = exact or partial
        if not candidates:
            return {
                "activated": False,
                "error": (
                    f"No engagement folder matching '{clean}' found in the directories "
                    "available to this session."
                ),
                "available_engagements": _known_engagements(search_roots),
            }

        chosen = candidates[0]
        warnings: list[str] = []
        if len(candidates) > 1:
            others = ", ".join(c.name for c in candidates[1:5])
            warnings.append(
                f"Multiple folders matched '{clean}' (also: {others}); activated '{chosen.name}'."
            )

        summary = _engagement_summary(chosen)
        if summary["requirements_dir"] is None:
            warnings.append(f"'{chosen.name}' has no 01_Requirements folder yet.")

        state.active = ActiveEngagement(name=chosen.name, root=chosen)
        return {
            "activated": True,
            "engagement_name": chosen.name,
            "workspace": str(chosen),
            "requirements_dir": (
                str(summary["requirements_dir"]) if summary["requirements_dir"] else None
            ),
            "requirements_dir_display": summary["requirements_dir_display"],
            "deliverables_dir": str(summary["deliverables_dir"]),
            "deliverables_dir_display": summary["deliverables_dir_display"],
            "deliverables_dir_exists": summary["deliverables_dir_exists"],
            "warnings": warnings,
        }

    def load_engagement_context() -> dict[str, Any]:
        """Read the active engagement's requirements (everything under its '01_Requirements'
        folder) plus any discovery notes and meeting notes if present, and return it all as one
        consolidated, structured result. If no engagement has been explicitly activated yet,
        auto-detects one in the session workspace and activates it. Call this before generating
        any proposal deliverable instead of discovering files yourself with
        list_files/read_file/grep. If found=true, use the content — do not ask the user for
        client details it already answers. Only ask the user if found=false, requirements_dir
        is null, requirements is empty, or a warning flags something ambiguous.
        """
        warnings: list[str] = []
        if state.active is not None and state.active.root.is_dir():
            engagement_root = state.active.root
            engagement_name = state.active.name
        else:
            if state.active is not None:
                state.active = None  # previously active engagement folder is gone
            engagement_root, warnings = _locate_implicit(ws)
            engagement_name = engagement_root.name if engagement_root is not None else None
            if engagement_root is not None:
                state.active = ActiveEngagement(name=engagement_name, root=engagement_root)

        if engagement_root is None:
            return {
                "found": False,
                "engagement_name": None,
                "engagement_root": None,
                "requirements_dir": None,
                "requirements": [],
                "discovery_notes": [],
                "meeting_notes": [],
                "deliverables_dir": None,
                "deliverables_dir_exists": False,
                "warnings": warnings,
                "summary": (
                    "No active engagement, and none could be auto-detected in this workspace. "
                    'Ask the user to activate one, e.g. "Open Engagement: <name>".'
                ),
            }

        summary = _engagement_summary(engagement_root)
        requirements_dir = summary["requirements_dir"]
        requirements: list[dict[str, Any]] = []
        skipped: list[str] = []
        if requirements_dir is not None:
            requirements, skipped = _read_files_in(requirements_dir, toolkit=toolkit)

        discovery_notes, disc_skipped = _read_marker_matches(
            engagement_root, _DISCOVERY_MARKERS, toolkit=toolkit, exclude=requirements_dir
        )
        meeting_notes, meet_skipped = _read_marker_matches(
            engagement_root, _MEETING_MARKERS, toolkit=toolkit, exclude=requirements_dir
        )
        skipped += disc_skipped + meet_skipped
        if skipped:
            shown = ", ".join(skipped[:10])
            more = f" … and {len(skipped) - 10} more" if len(skipped) > 10 else ""
            warnings.append(f"{len(skipped)} file(s) not read: {shown}{more}")

        note = "" if requirements_dir is not None else " No 01_Requirements folder found."
        return {
            "found": True,
            "engagement_name": engagement_name,
            "engagement_root": str(engagement_root),
            "requirements_dir": str(requirements_dir) if requirements_dir else None,
            "requirements": requirements,
            "discovery_notes": discovery_notes,
            "meeting_notes": meeting_notes,
            "deliverables_dir": str(summary["deliverables_dir"]),
            "deliverables_dir_display": summary["deliverables_dir_display"],
            "deliverables_dir_exists": summary["deliverables_dir_exists"],
            "warnings": warnings,
            "summary": (
                f"Engagement '{engagement_name}': {len(requirements)} requirements file(s), "
                f"{len(discovery_notes)} discovery note(s), {len(meeting_notes)} meeting "
                f"note(s).{note}"
            ),
        }

    activate_engagement.__name__ = "activate_engagement"
    load_engagement_context.__name__ = "load_engagement_context"
    return [
        tool(
            activate_engagement,
            metadata=ToolMetadata(
                category="filesystem",
                risk_level="low",
                capabilities=["read"],
                requires_approval=False,
            ),
        ),
        tool(
            load_engagement_context,
            metadata=ToolMetadata(
                category="filesystem",
                risk_level="low",
                capabilities=["read"],
                requires_approval=False,
            ),
        ),
    ]

"""The work-reporting vocabulary.

kvault reports what it *decided* — not what it did step by step. A note is
emitted only when kvault invented a value, deliberately changed nothing,
half-failed, hid something, or fell back. Operations that went exactly as
asked stay silent, which is what keeps default output short.

The code set is closed on purpose. New reportable behaviour maps onto an
existing code or the set gets an explicit amendment here; that is the
difference between systematic reporting and ad-hoc prints accumulating over
releases. Each code's one-sentence contract is the durable part — the wording
of any individual note is not.

Notes live *inside* the result dict. They are never printed from ``core`` or
``mcp``: stdout there is the MCP JSON-RPC transport, and a stray write
corrupts the stream. Rendering happens only in ``kvault.cli.render``.
"""

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# -- verbosity tiers ---------------------------------------------------------

QUIET = 0
NORMAL = 1
EXPLAIN = 2
TRACE = 3

TIER_NAMES = {
    "quiet": QUIET,
    "normal": NORMAL,
    "explain": EXPLAIN,
    "trace": TRACE,
}


def tier_from_name(name: Optional[str]) -> Optional[int]:
    """Map a tier name to its level, or None if unrecognized.

    Unrecognized names return None rather than raising: this parses a
    user-set environment variable, and a typo there must not break a KB
    operation.
    """
    if not name:
        return None
    return TIER_NAMES.get(str(name).strip().lower())


# -- the closed vocabulary ---------------------------------------------------

NOTE_CODES: Tuple[str, ...] = (
    "autofilled",  # kvault invented a value you did not supply
    "unchanged",  # the operation ran and deliberately changed nothing
    "partial",  # part succeeded, part did not; manual repair needed
    "created",  # something came into existence as a side effect
    "removed",  # something was destroyed, with a count
    "truncated",  # you are not seeing everything that matched or exists
    "skipped",  # kvault could not read something and continued without it
    "waited",  # kvault blocked on, or broke, another process's lock
    "guessed",  # an input was unusable and a fallback was chosen
    "propagate",  # ancestor summaries are stale because of this operation
    "structure",  # this write changed the tree's shape in a way worth a look
)

#: Notes at or below the reader's tier are shown. ``partial`` is deliberately
#: absent: it survives every tier including ``--quiet``, because silencing a
#: half-failure on request is a footgun. ``-q`` means "less detail", not
#: "hide damage".
DEFAULT_LEVELS: Dict[str, int] = {
    "autofilled": NORMAL,
    "unchanged": NORMAL,
    "partial": QUIET,
    "created": NORMAL,
    "removed": NORMAL,
    "truncated": NORMAL,
    "skipped": NORMAL,
    "waited": TRACE,
    "guessed": NORMAL,
    "propagate": EXPLAIN,
    "structure": NORMAL,
}


class UnknownNoteCode(ValueError):
    """Raised when a note code is not in the closed vocabulary.

    Deliberately loud, and deliberately raised at *build* time rather than at
    render time: an unknown code means someone added reportable behaviour
    without deciding what class of thing it is, and the whole value of a
    closed set is that this is a decision, not a default.
    """

    def __init__(self, code: str) -> None:
        super().__init__(
            f"Unknown note code: {code!r}. Must be one of {', '.join(NOTE_CODES)}. "
            "Map the new behaviour onto an existing code, or amend NOTE_CODES "
            "in kvault/core/notes.py with its one-sentence contract."
        )


def note(
    code: str,
    text: str,
    level: Optional[int] = None,
    detail: Optional[Dict[str, Any]] = None,
    why: Optional[str] = None,
    next_step: Optional[str] = None,
) -> Dict[str, Any]:
    """Build one note.

    *why* and *next_step* are carried on the note rather than emitted
    separately so that a note and its explanation can never drift apart or be
    rendered out of order. Both are shown only at ``--explain`` and above.
    """
    if code not in NOTE_CODES:
        raise UnknownNoteCode(code)
    entry: Dict[str, Any] = {
        "code": code,
        "text": text,
        "level": DEFAULT_LEVELS[code] if level is None else level,
    }
    if detail:
        entry["detail"] = detail
    if why:
        entry["why"] = why
    if next_step:
        entry["next"] = next_step
    return entry


def visible(notes: Sequence[Dict[str, Any]], tier: int) -> List[Dict[str, Any]]:
    """Notes a reader at *tier* should see, in emission order."""
    return [n for n in notes if n.get("level", NORMAL) <= tier]


def attach_note(result: Dict[str, Any], entry: Dict[str, Any]) -> None:
    """Append *entry* to result["notes"], keeping notes BEFORE the bulk payload.

    Key order is reading order over MCP. ``setdefault`` on a result without a
    ``notes`` key would insert it at the END — after ``ancestors``, the one
    place a note is guaranteed not to be read.
    """
    if "notes" in result:
        result["notes"].append(entry)
        return
    if "ancestors" in result:
        ancestors = result.pop("ancestors")
        result["notes"] = [entry]
        result["ancestors"] = ancestors
    else:
        result["notes"] = [entry]


def has_partial(notes: Sequence[Dict[str, Any]]) -> bool:
    """True when any note reports a half-failure needing manual repair."""
    return any(n.get("code") == "partial" for n in notes)


def collapse(notes: Iterable[Dict[str, Any]], max_examples: int = 3) -> List[Dict[str, Any]]:
    """Group notes by code for batch commands.

    Specified once, here, rather than per command. ``update_summaries`` calls
    ``write_summary`` in a loop and ``_write_parent_summary_locked`` calls it
    nested inside its own lock, so the leaf function that produces notes fires
    1..N times per user-visible command. Without a single collapse policy each
    call site invents its own, and a 40-ancestor maintenance batch emits 40+
    near-identical notes into an agent's context.

    Order of first appearance is preserved so the collapsed list still reads
    as a narrative. ``count`` is always present, even at 1, so consumers never
    have to branch on its absence.
    """
    grouped: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for entry in notes:
        code = entry.get("code", "")
        if code not in grouped:
            grouped[code] = {
                "code": code,
                "level": entry.get("level", NORMAL),
                "count": 0,
                "examples": [],
            }
            order.append(code)
        bucket = grouped[code]
        bucket["count"] += 1
        # Keep the most severe level seen for this code — a lock that had to be
        # broken must not be hidden behind a routine sub-second wait.
        bucket["level"] = min(bucket["level"], entry.get("level", NORMAL))
        if len(bucket["examples"]) < max_examples:
            example = entry.get("text", "")
            path = (entry.get("detail") or {}).get("path")
            bucket["examples"].append(f"{path}: {example}" if path else example)
    return [grouped[code] for code in order]


__all__ = [
    "QUIET",
    "NORMAL",
    "EXPLAIN",
    "TRACE",
    "TIER_NAMES",
    "NOTE_CODES",
    "DEFAULT_LEVELS",
    "UnknownNoteCode",
    "tier_from_name",
    "note",
    "visible",
    "attach_note",
    "has_partial",
    "collapse",
]

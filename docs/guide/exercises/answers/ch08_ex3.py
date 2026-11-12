#!/usr/bin/env python3

"""
ch08_ex3.py --- reference answer to exercise 8.3: a drafter for every transition

Contains:
    solve(): returns hidden warning drafts, one per transition of the example show
"""

from datetime import UTC, datetime

from stage.examples import two_set_show
from stage.notes.models import Anchor, DraftedNote, Note
from stage.notes.notes import draft_notes


def solve() -> list[Note]:
    """Returns hidden warning drafts, one per transition of the example show."""
    show = two_set_show()

    class PerTransition:
        """Drafts one warning per transition of the show."""

        def draft(self, show_summary: str, concept: str) -> list[DraftedNote]:
            """Returns one warning note anchored on every transition."""
            return [
                DraftedNote(text=concept, kind="warning", anchor=Anchor.on_transition(tid))
                for tid in show.transition_ids()
            ]

    at = datetime(2026, 11, 11, 10, 0, tzinfo=UTC)
    return draft_notes(show, "check spacing", PerTransition(), at)

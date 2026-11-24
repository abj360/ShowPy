#!/usr/bin/env python3

"""
test_proposals.py --- tests the proposal record's invariants and the stored proposal lifecycle

Contains:
    test_proposal_accepted_revision_is_set_exactly_when_accepted: refuses an accepted proposal
        without its revision and a revision on any other state
    test_proposal_reason_is_set_only_when_rejected: refuses a reason on any state but rejected
"""

import pytest

from e8s.core.errors import SchemaError
from e8s.show.collab_models import Proposal, ProposalState

STATES: tuple[ProposalState, ...] = ("open", "accepted", "rejected", "withdrawn")


def _proposal(state: ProposalState, *, accepted_revision: str = "", reason: str = "") -> Proposal:
    return Proposal(
        id="p1",
        base_revision="r1",
        change=(),
        state=state,
        accepted_revision=accepted_revision,
        reason=reason,
    )


def test_proposal_accepted_revision_is_set_exactly_when_accepted() -> None:
    assert _proposal("accepted", accepted_revision="r1").accepted_revision == "r1"
    with pytest.raises(SchemaError, match="accepted_revision"):
        _proposal("accepted")
    for state in STATES:
        if state != "accepted":
            with pytest.raises(SchemaError, match="accepted_revision"):
                _proposal(state, accepted_revision="r1")


def test_proposal_reason_is_set_only_when_rejected() -> None:
    assert _proposal("rejected", reason="too wide").reason == "too wide"
    assert _proposal("rejected").reason == ""
    for state in STATES:
        if state != "rejected":
            with pytest.raises(SchemaError, match="reason"):
                _proposal(state, accepted_revision="r1" if state == "accepted" else "", reason="x")
